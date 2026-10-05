"""Read a single bounded page, then commit its completed prefix atomically."""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import timedelta

from sqlalchemy import insert, select, update

from backend.entity import (AutoTagRule, LedgerEntry, LedgerEntryTag, ReviewAllocation, ReviewCase,
                            TargetTag, TargetTagView, TransactionFact, TagAssignmentRequest)
from backend.entity.base import utc_now
from backend.error import TargetTagError
from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper, ProtectedScanSource, ScanCommitResult
from backend.mapper.tag_write_mapper import TagWriteMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.mapper.target_tag_projection_mapper import TargetTagProjectionMapper
from backend.mapper.setting_mapper import SettingMapper


@dataclass(frozen=True)
class ScanBatchResult:
    status: str
    reason: str
    items: tuple[ScanCommitResult, ...] = ()


class AutoTagScanBatchMapper(AutoTagScanMapper):
    def read_page(self, rule_id, *, limit):
        TrustedRelationMapper(self.db).read_snapshot()
        page = super().read_page(rule_id, limit=limit)
        if page is not None:
            page = replace(page, enabled=page.enabled and SettingMapper(self.db).scan_enabled())
        if page is None or not page.ledger_ids:
            return page
        TargetTagProjectionMapper(self.db).active_dictionary()
        rows = self.db.execute(select(LedgerEntry.id, LedgerEntry.entry_type, LedgerEntry.entry_direction,
            LedgerEntry.amount, LedgerEntry.currency_code, LedgerEntry.occurred_time,
            ReviewAllocation.id.label("allocation_id"), ReviewAllocation.amount.label("allocation_amount"),
            ReviewAllocation.currency_code.label("allocation_currency"), ReviewCase.id.label("review_id"),
            ReviewCase.status.label("review_status"), TransactionFact.id.label("fact_id"),
            TransactionFact.fact_key, TransactionFact.cash_direction.label("fact_direction"),
            TransactionFact.currency_code.label("fact_currency"), TransactionFact.occurred_time.label("fact_time"),
            TransactionFact.counterparty_name, TransactionFact.summary,
        ).outerjoin(ReviewAllocation, ReviewAllocation.ledger_entry_id == LedgerEntry.id
        ).outerjoin(ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id
        ).outerjoin(TransactionFact, TransactionFact.id == ReviewAllocation.transaction_fact_id
        ).where(LedgerEntry.id.in_(page.ledger_ids)).order_by(LedgerEntry.id)).mappings().all()
        counts = Counter(row["id"] for row in rows)
        invalid = {lid for lid in page.ledger_ids if counts[lid] != 1}
        sources = {}
        for row in rows:
            lid = row["id"]
            if (lid in invalid or any(row[key] is None for key in ("allocation_id", "review_id", "fact_id"))
                or row["entry_type"] not in (0, 1, 2, 3) or row["review_status"] not in (0, 1)
                or row["entry_direction"] not in (1, 2)
                or row["amount"] <= 0 or row["amount"] != row["allocation_amount"]
                or row["currency_code"] != row["allocation_currency"] or row["currency_code"] != row["fact_currency"]
                or row["entry_direction"] != row["fact_direction"] or row["occurred_time"] != row["fact_time"]):
                invalid.add(lid)
                continue
            if row["review_status"] == 0 and row["entry_type"] != 3:
                sources[lid] = ProtectedScanSource(direction="IN" if row["entry_direction"] == 1 else "OUT",
                    amount=row["amount"], currency_code=row["currency_code"], merchant=row["counterparty_name"],
                    summary=row["summary"], occurred_time=row["occurred_time"],
                    economic_type={0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "ASSET_LIABILITY"}[row["entry_type"]],
                    synthetic_allowed=row["fact_key"].startswith("pirc24-gate-fictional-"))
        relations = self.db.execute(select(LedgerEntryTag.ledger_id, TargetTag.id.label("tag_id"),
            TargetTag.status.label("tag_status"), TargetTagView.id.label("view_id"), TargetTagView.status.label("view_status")
        ).outerjoin(TargetTag, TargetTag.id == LedgerEntryTag.tag_id
        ).outerjoin(TargetTagView, TargetTagView.id == TargetTag.view_id
        ).where(LedgerEntryTag.ledger_id.in_(page.ledger_ids)).limit(50001)).mappings().all()
        if len(relations) > 50000:
            raise TargetTagError(413, "scan tag relation scope exceeds budget", code="TAG_IMPACT_LIMIT")
        active = Counter()
        for row in relations:
            if row["tag_id"] is None or row["view_id"] is None:
                invalid.add(row["ledger_id"])
            elif row["tag_status"] == row["view_status"] == "ACTIVE":
                active[row["ledger_id"], row["view_id"]] += 1
        invalid.update(lid for (lid, _view), count in active.items() if count > 1)
        # Active sources must have one active value in the rule's current View.
        invalid.update(lid for lid in sources if active[lid, page.view_id] != 1 and page.view_active)
        return replace(page, protected_sources=sources, invalid_ledger_ids=frozenset(invalid))

    def commit_prefix(self, original, outcomes):
        if not 1 <= len(outcomes) <= 100:
            raise ValueError("completed scan prefix must contain 1..100 items")
        ids = [item["ledger_id"] for item in outcomes]
        if ids != list(original.ledger_ids[:len(ids)]) or any(item["kind"] not in
            {"SKIP", "NO_SUGGESTION", "ITEM_FAILURE", "SUGGESTED"} for item in outcomes):
            raise ValueError("scan results must be the original continuous prefix")
        writer = TagWriteMapper(self.db)
        try:
            writer.begin_write()
            current = self.read_page(original.token.rule_id, limit=len(outcomes))
            if current is None or current.token != original.token or not current.enabled:
                writer.rollback()
                return ScanBatchResult("STALE", "RULE_TOKEN_CHANGED")
            if (current.view_id, current.model_id, current.prompt, current.amount_mode, current.targets) != (
                original.view_id, original.model_id, original.prompt, original.amount_mode, original.targets):
                writer.rollback()
                return ScanBatchResult("STALE", "RULE_TOKEN_CHANGED")
            if not current.view_active or not current.model_enabled or not current.targets:
                writer.rollback()
                return ScanBatchResult("STALE", "CONFIG_CHANGED")
            if ids != list(current.ledger_ids[:len(ids)]):
                writer.rollback()
                return ScanBatchResult("STALE", "SOURCE_CHANGED")
            rule = self.db.get(AutoTagRule, original.token.rule_id)
            records, results, deltas = [], [], Counter()
            targets = {target.tag_id for target in current.targets}
            stop = "PREFIX_COMMITTED"
            for item in outcomes:
                lid, kind = item["ledger_id"], item["kind"]
                if lid in current.invalid_ledger_ids:
                    stop = "TAG_RELATION_BROKEN"
                    break
                suggestions = tuple(item.get("suggestions", ()))
                reason = "ANALYSIS_COMMITTED"
                if (lid not in current.active_ledger_ids or current.active_tag_states.get(lid) != ("unclassified",)
                    or lid in current.existing_request_ids):
                    kind, suggestions, reason = "SKIP", (), "SOURCE_INELIGIBLE"
                elif current.protected_sources.get(lid) != original.protected_sources.get(lid):
                    writer.rollback()
                    return ScanBatchResult("STALE", "SOURCE_CHANGED")
                proposed = [suggestion.tag_id for suggestion in suggestions]
                if kind == "SUGGESTED" and (not proposed or len(proposed) != len(set(proposed)) or
                    not set(proposed).issubset(targets) or any(not s.reason.strip() or len(s.reason) > 200 for s in suggestions)):
                    kind, suggestions, reason = "ITEM_FAILURE", (), "INVALID_SUGGESTION"
                count = len(suggestions) if kind == "SUGGESTED" else 0
                results.append(ScanCommitResult("COMMITTED", reason, count))
                deltas["analyzed_count"] += int(kind != "SKIP")
                deltas["failed_count"] += int(kind == "ITEM_FAILURE")
                deltas["suggested_count"] += count
                if count:
                    records.extend(dict(rule_id=rule.id, rule_revision=rule.rule_revision, ledger_id=lid,
                        view_id=rule.view_id, proposed_tag_id=s.tag_id, status=1, reason_summary=s.reason) for s in suggestions)
            if not results:
                writer.rollback()
                return ScanBatchResult("COMMITTED", stop)
            counters = {name: self._counter(getattr(rule, name), deltas[name], name) for name in
                        ("analyzed_count", "failed_count", "suggested_count")}
            now = max(utc_now(), rule.updated_time + timedelta(microseconds=1))
            for row in records:
                row.update(created_time=now, updated_time=now)
            for offset in range(0, len(records), 400):
                self.db.execute(insert(TagAssignmentRequest.__table__), records[offset:offset + 400])
            written = self.db.execute(update(AutoTagRule).where(AutoTagRule.id == rule.id,
                AutoTagRule.rule_revision == original.token.rule_revision,
                AutoTagRule.scan_epoch == original.token.scan_epoch,
                AutoTagRule.scan_after_ledger_id == original.token.scan_after_ledger_id
            ).values(scan_after_ledger_id=ids[len(results) - 1], updated_time=now, **counters))
            if written.rowcount != 1:
                writer.rollback()
                return ScanBatchResult("STALE", "RULE_TOKEN_CHANGED")
            writer.commit()
            return ScanBatchResult("COMMITTED", stop, tuple(results))
        except Exception:
            writer.rollback()
            raise
