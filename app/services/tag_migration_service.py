from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    BillFactShadowVO,
    LegacyTagAuditVO,
    ReviewCaseBillShadowVO,
    ReviewCaseShadowVO,
    ReviewHistoryShadowVO,
    ReviewShadowReport,
)
from app.services.review_migration_service import (
    CHILD_ID_BITS,
    MAX_CHILD_ID,
    ReviewMatterShadowMigrationService,
)


TAG_CASE_START = 5 << 40
TAG_CASE_END = 6 << 40


class TagReviewShadowMigrationService:
    """Migrate tag decisions and suggestions without changing legacy rows."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> ReviewShadowReport:
        blockers: list[str] = []
        audits = self.mapper.legacy_tag_audits()
        bill_ids = sorted({audit.bill_id for audit in audits})
        bills = {bill.id: bill for bill in self.mapper.legacy_tag_bills(bill_ids)}
        facts = {fact.id: fact for fact in self.mapper.target_bill_facts(bill_ids)}
        expected_cases, expected_lines, expected_history = self._expected(
            audits,
            bills,
            facts,
            blockers,
        )
        current_cases, current_lines, current_history = self._current()
        case_inserts = self._missing(expected_cases, current_cases)
        line_inserts = self._missing(expected_lines, current_lines)
        history_inserts = ReviewMatterShadowMigrationService._missing_history(
            expected_history,
            current_history,
            blockers,
        )
        try:
            self.mapper.insert_review_cases([asdict(row) for row in case_inserts])
            self.mapper.insert_review_case_bills([asdict(row) for row in line_inserts])
            self.mapper.insert_review_history([asdict(row) for row in history_inserts])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        actual_cases, actual_lines, actual_history = self._current()
        support = ReviewMatterShadowMigrationService
        case_report = support._table_report(expected_cases, actual_cases, len(case_inserts))
        line_report = support._table_report(expected_lines, actual_lines, len(line_inserts))
        history_report = support._table_report(
            expected_history,
            actual_history,
            len(history_inserts),
        )
        return ReviewShadowReport(
            matched=(
                not blockers
                and not case_report.mismatched_ids
                and not line_report.mismatched_ids
                and not history_report.mismatched_ids
            ),
            review_case=case_report,
            review_case_bill=line_report,
            review_history=history_report,
            blockers=blockers,
        )

    def _current(self):
        query = {"case_id_start": TAG_CASE_START, "case_id_end": TAG_CASE_END}
        return (
            self.mapper.target_review_cases(**query),
            self.mapper.target_review_case_bills(**query),
            self.mapper.target_review_history(**query),
        )

    @classmethod
    def _expected(cls, audits, bills, facts, blockers):
        audits_by_bill: dict[int, list[LegacyTagAuditVO]] = {}
        for audit in audits:
            audits_by_bill.setdefault(audit.bill_id, []).append(audit)

        cases: list[ReviewCaseShadowVO] = []
        lines: list[ReviewCaseBillShadowVO] = []
        histories: list[ReviewHistoryShadowVO] = []
        for bill_id, bill_audits in sorted(audits_by_bill.items()):
            if bill_id <= 0 or bill_id >= (1 << 40):
                blockers.append(f"tag bill {bill_id}: ID exceeds migration namespace")
                continue
            if len(bill_audits) >= MAX_CHILD_ID:
                blockers.append(f"tag bill {bill_id}: audit history exceeds migration range")
                continue
            bill = bills.get(bill_id)
            fact = facts.get(bill_id)
            if not bill or not fact:
                blockers.append(f"tag bill {bill_id}: missing bill or bill_fact")
                continue

            accepted_state = cls._tag_state(
                bill_audits[0].before_state_json,
                f"tag audit {bill_audits[0].id} before state",
                blockers,
            )
            if accepted_state is None:
                continue
            accepted_category = bill_audits[0].before_category
            accepted_status = "REVOKED"
            accepted_meta: dict[str, object] = {}
            suggestion: dict[str, object] = {}
            current_event_id: int | None = None
            history_ids: dict[int, int] = {}
            accepted_status_before: dict[int, str] = {}
            reversed_ids: set[int] = set()
            case_history: list[ReviewHistoryShadowVO] = []
            case_id = TAG_CASE_START + bill_id
            valid = True

            for version, audit in enumerate(bill_audits, start=1):
                if audit.action not in {"confirm", "suggest", "undo"}:
                    blockers.append(f"tag audit {audit.id}: unsupported action {audit.action}")
                    valid = False
                    break
                before_state = cls._tag_state(
                    audit.before_state_json,
                    f"tag audit {audit.id} before state",
                    blockers,
                )
                after_state = cls._tag_state(
                    audit.tag_state_json,
                    f"tag audit {audit.id} tag state",
                    blockers,
                )
                if before_state is None or after_state is None:
                    valid = False
                    break
                if before_state != accepted_state or audit.before_category != accepted_category:
                    blockers.append(f"tag audit {audit.id}: before state breaks the decision chain")
                    valid = False
                    break

                before_json = cls._snapshot(
                    fact,
                    accepted_category,
                    accepted_state,
                    accepted_meta,
                    suggestion,
                    cls._case_status(accepted_status, suggestion),
                    version - 1,
                )
                accepted_status_before[audit.id] = accepted_status
                reverses_history_id = 0
                event_meta = cls._event_meta(audit, after_state)
                if audit.action == "suggest":
                    if not audit.superseded or audit.undone or audit.reverses_audit_id is not None:
                        blockers.append(f"tag audit {audit.id}: suggestion lifecycle flags are invalid")
                        valid = False
                        break
                    suggestion = event_meta
                    operation = "CREATE" if version == 1 else "UPDATE"
                elif audit.action == "confirm":
                    if audit.reverses_audit_id is not None:
                        blockers.append(f"tag audit {audit.id}: confirmation cannot reverse another audit")
                        valid = False
                        break
                    accepted_category = audit.category
                    accepted_state = after_state
                    accepted_status = "CONFIRMED"
                    accepted_meta = event_meta
                    suggestion = {}
                    current_event_id = audit.id
                    operation = "CREATE" if version == 1 else "CONFIRM"
                else:
                    reversed_id = audit.reverses_audit_id
                    if reversed_id is None or reversed_id != current_event_id:
                        blockers.append(f"tag audit {audit.id}: reversal does not target the current decision")
                        valid = False
                        break
                    if reversed_id in reversed_ids or reversed_id not in history_ids:
                        blockers.append(f"tag audit {audit.id}: reversed audit is missing or already reversed")
                        valid = False
                        break
                    accepted_category = audit.category
                    accepted_state = after_state
                    accepted_status = accepted_status_before[reversed_id]
                    accepted_meta = event_meta
                    suggestion = {}
                    current_event_id = audit.id
                    reversed_ids.add(reversed_id)
                    reverses_history_id = history_ids[reversed_id]
                    operation = "RESTORE"

                request_json = cls._canonical_json_text(
                    audit.request_payload,
                    f"tag audit {audit.id} request",
                    blockers,
                )
                if request_json is None:
                    valid = False
                    break
                after_json = cls._snapshot(
                    fact,
                    accepted_category,
                    accepted_state,
                    accepted_meta,
                    suggestion,
                    cls._case_status(accepted_status, suggestion),
                    version,
                )
                history_id = cls._child_id(case_id, version)
                case_history.append(ReviewHistoryShadowVO(
                    id=history_id,
                    created_time=audit.created_at,
                    updated_time=audit.created_at,
                    case_id=case_id,
                    version=version,
                    operation=operation,
                    schema_version=1,
                    request_json=request_json,
                    before_json=before_json,
                    after_json=after_json,
                    snapshot_hash=cls._sha256(after_json),
                    reverses_history_id=reverses_history_id,
                    actor=audit.actor or "local-user",
                    reason=audit.reason or "",
                    idempotency_key=cls._idempotency_key(audit.idempotency_key or ""),
                ))
                history_ids[audit.id] = history_id

            if not valid:
                continue
            referenced_ids = {
                audit.reverses_audit_id
                for audit in bill_audits
                if audit.action == "undo" and audit.reverses_audit_id is not None
            }
            inconsistent_undo = [
                audit.id for audit in bill_audits
                if audit.undone != (audit.id in referenced_ids)
                or audit.undone != (audit.undone_at is not None)
            ]
            state_audits = [audit for audit in bill_audits if audit.action != "suggest"]
            inconsistent_current = [
                audit.id for audit in state_audits
                if audit.superseded != (audit.id != current_event_id)
            ]
            if inconsistent_undo or inconsistent_current:
                blockers.append(
                    f"tag bill {bill_id}: lifecycle flags disagree for audits "
                    f"{sorted(set(inconsistent_undo + inconsistent_current))}"
                )
                continue
            current_state = cls._tag_state(
                bill.tag_state_json,
                f"tag bill {bill_id} current state",
                blockers,
            )
            if current_state is None:
                continue
            if current_state != accepted_state or bill.category != accepted_category:
                blockers.append(f"tag bill {bill_id}: current bill tags differ from audit history")
                continue

            status = cls._case_status(accepted_status, suggestion)
            result = cls._result(
                accepted_category,
                accepted_state,
                accepted_meta,
                suggestion,
            )
            updated_time = bill_audits[-1].created_at
            cases.append(ReviewCaseShadowVO(
                id=case_id,
                created_time=bill_audits[0].created_at,
                updated_time=updated_time,
                review_type="TAG",
                status=status,
                allocation_status="COMPLETE",
                version=len(bill_audits),
                title=f"Tags: {fact.counterparty}",
                result_json=cls._canonical(result),
            ))
            lines.append(ReviewCaseBillShadowVO(
                id=cls._child_id(case_id, 1),
                created_time=updated_time,
                updated_time=updated_time,
                case_id=case_id,
                bill_id=bill_id,
                role="TAG",
                party="",
                amount_value=fact.amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
            ))
            histories.extend(case_history)
        return tuple(cases), tuple(lines), tuple(histories)

    @classmethod
    def _snapshot(
        cls,
        fact: BillFactShadowVO,
        category: str,
        tag_state: dict[str, str],
        decision: dict[str, object],
        suggestion: dict[str, object],
        status: str,
        version: int,
    ) -> str:
        return cls._canonical({
            "allocation_status": "COMPLETE",
            "lines": [{
                "bill_id": fact.id,
                "role": "TAG",
                "party": "",
                "amount_value": fact.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
            }],
            "result": cls._result(category, tag_state, decision, suggestion),
            "review_type": "TAG",
            "status": status,
            "title": f"Tags: {fact.counterparty}",
            "version": version,
        })

    @staticmethod
    def _result(category, tag_state, decision, suggestion):
        return {
            "category": category,
            "decision": decision,
            "suggestion": suggestion,
            "tag_state": tag_state,
        }

    @staticmethod
    def _event_meta(audit: LegacyTagAuditVO, state: dict[str, str]) -> dict[str, object]:
        return {
            "action": audit.action,
            "confidence": audit.confidence,
            "provider": audit.provider,
            "strategy": audit.strategy,
            "tags": [tag.strip() for tag in audit.tags.split(",") if tag.strip()],
            "tag_state": state,
        }

    @staticmethod
    def _case_status(accepted_status: str, suggestion: dict[str, object]) -> str:
        if accepted_status == "CONFIRMED":
            return "CONFIRMED"
        return "PENDING" if suggestion else "REVOKED"

    @classmethod
    def _tag_state(cls, raw: str, label: str, blockers: list[str]):
        parsed = ReviewMatterShadowMigrationService._parse_object(raw or "{}", label, blockers)
        if parsed is None:
            return None
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in parsed.items()):
            blockers.append(f"{label}: keys and values must be strings")
            return None
        return parsed

    @staticmethod
    def _canonical_json_text(raw: str, label: str, blockers: list[str]):
        return ReviewMatterShadowMigrationService._canonical_json_text(raw, label, blockers)

    @staticmethod
    def _canonical(value: object) -> str:
        return ReviewMatterShadowMigrationService._canonical(value)

    @staticmethod
    def _sha256(value: str) -> str:
        return ReviewMatterShadowMigrationService._sha256(value)

    @classmethod
    def _idempotency_key(cls, source: str) -> str:
        if not source:
            return ""
        value = f"tag:{source}"
        return value if len(value) <= 120 else f"tag:sha256:{cls._sha256(source)}"

    @staticmethod
    def _child_id(case_id: int, child: int) -> int:
        return (case_id << CHILD_ID_BITS) | child

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)
