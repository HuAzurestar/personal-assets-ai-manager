"""Read-only preview and atomic immutable Review publication."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from time import monotonic

from sqlalchemy.exc import IntegrityError, OperationalError

from backend.entity.base import utc_now
from backend.core.tag_semantics import tag_effect
from backend.core.review_default import original_defaults
from backend.core.feature_observability import observed, observability
from backend.error import TargetEconomicError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.review_command_mapper import ReviewCommandMapper, chunks
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
from backend.service.review_intent_service import expand, raw_draft, reject, validate_duplicate_keepers
from backend.service.target_tag_projection_service import TargetTagProjectionService


REVIEW_NAMES = {0: "NORMAL_TRANSACTION", 1: "BORROW_AND_REPAY", 2: "CREDIT_CARD",
                3: "SHARED_SETTLEMENT", 4: "OTHER_MANUAL"}
ECONOMIC_NAMES = {0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "ASSET_LIABILITY", 3: "DUPLICATE"}


def canonical(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.isoformat(timespec="microseconds")
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=encode)


def review_po(row):
    return {key: row[key] for key in ("id", "title", "created_time", "updated_time")} | {
        "type": REVIEW_NAMES[row["behavior_type"]], "status": "CONFIRMED" if row["status"] == 0 else "REVOKED"}


def flow_po(row):
    return {key: row[key] for key in ("id", "cash_amount", "cash_currency_code", "account_ref_id",
                                     "occurred_time", "created_time", "updated_time")} | {
        "economic_type": ECONOMIC_NAMES[row["entry_type"]],
        "cash_direction": "IN" if row["entry_direction"] == 1 else "OUT"}


class ReviewCommandService:
    def __init__(self, db):
        self.db = db
        self.mapper = ReviewCommandMapper(db)
        self.relations = TrustedRelationMapper(db)
        self.tags = TargetTagProjectionService(db)
        self.requests = TagAssignmentRequestMapper(db)
        self.rules = AutoTagRuleMapper(db)

    def detail(self, review_id):
        self.relations.read_snapshot()
        self.relations.validate()
        bundle = self.mapper.bundle([review_id])
        if not bundle["reviews"]:
            reject("REVIEW_NOT_FOUND", "Review not found", status=404)
        count = sum(len(rows) for rows in bundle.values())
        if count > 4000:
            reject("DETAIL_LIMIT", "use paged relation reads", status=413)
        positions = self.mapper.named_rows("positions", [row["position_id"] for row in bundle["position_legs"]])
        units = {row["id"]: row["unit_code"] for row in positions}
        result = review_po(bundle["reviews"][0]) | {
            "allocations": bundle["allocations"], "ledger_entries": [flow_po(row) for row in bundle["ledger_entries"]],
            "position_legs": [row | {"unit_code": units[row["position_id"]]} for row in bundle["position_legs"]],
            "position_allocations": bundle["position_allocations"], "positions": positions}
        if len(canonical(result).encode("utf8")) > 8 * 1024 * 1024:
            reject("DETAIL_LIMIT", "use paged relation reads", status=413)
        return result

    def preview(self, intent: ReviewChangeInput):
        self.relations.read_snapshot()
        self.relations.validate()
        try:
            return self._plan(intent)["preview"]
        except TargetEconomicError as error:
            if error.code == "ENTITY_CHANGED":
                raise
            # Invalid business decisions are preview issues, not partial writes.
            return dict(reviews=[], coverage=[], impact=dict(conflicting_review_ids=[], restored_default_review_ids=[],
                affected_account_ref_ids=[], affected_position_ids=[], dependent_position_leg_ids=[], tag_ledger_ids=[]),
                blocking_issues=[dict(code=error.code, message=str(error))], expected_reviews=[], tag_effect={},
                preview_digest=hashlib.sha256(canonical(intent.model_dump()).encode()).hexdigest())

    def _plan(self, intent):
        raw = [raw_draft(row) for row in intent.new_reviews]
        selected = set(intent.activate_review_ids) | set(intent.deactivate_review_ids)
        selected_bundle = self.mapper.bundle(selected)
        selected_reviews = {row["id"]: row for row in selected_bundle["reviews"]}
        if set(selected_reviews) != selected:
            reject("REVIEW_NOT_FOUND", "selected Review not found", status=409)
        incoming = {row["transaction_id"] for row in selected_bundle["allocations"]
                    if row["review_id"] in intent.activate_review_ids}
        raw_ids = set()
        activation_duplicates = [row.model_dump() for row in intent.activation_duplicates]
        activated_duplicate_ledgers = {row["id"] for row in selected_bundle["ledger_entries"] if row["entry_type"] == 3}
        activated_duplicate_facts = {row["transaction_id"] for row in selected_bundle["allocations"]
            if row["review_id"] in intent.activate_review_ids and row["ledger_id"] in activated_duplicate_ledgers}
        if {row["transaction_id"] for row in activation_duplicates} != activated_duplicate_facts:
            reject("INVALID_DUPLICATE", "reactivation requires exactly one explicit kept Fact for each duplicate Fact")
        raw_ids.update(row["kept_transaction_id"] for row in activation_duplicates)
        for draft in raw:
            incoming.update(draft["transaction_ids"])
            incoming.update(row["transaction_id"] for row in draft["allocations"])
            incoming.update(row["transaction_id"] for row in draft["duplicate_transactions"])
            raw_ids.update(row["kept_transaction_id"] for row in draft["duplicate_transactions"])
        current_incoming = self.mapper.allocations_for_facts(incoming, active=True)
        conflicts = {row["review_id"] for row in current_incoming} - set(intent.activate_review_ids)
        closing = conflicts | set(intent.deactivate_review_ids)
        affected_bundle = self.mapper.bundle(selected | closing)
        affected = incoming | {row["transaction_id"] for row in affected_bundle["allocations"]}
        if len(affected | raw_ids) > 2000 or len(selected | closing) + len(raw) > 100:
            reject("REVIEW_CHANGE_LIMIT", "Review/Fact publication budget exceeded", status=413)
        defaults = self.mapper.allocations_for_facts(affected | raw_ids, defaults=True)
        default_ids = {row["review_id"] for row in defaults}
        full = self.mapper.bundle(selected | closing | default_ids)
        review_rows = {row["id"]: row for row in full["reviews"]}
        if len(review_rows) + len(raw) > 100:
            reject("REVIEW_CHANGE_LIMIT", "expanded Review groups exceed budget", status=413)
        facts = {row["id"]: row for row in self.mapper.named_rows("facts", affected | raw_ids)}
        if set(facts) != affected | raw_ids:
            reject("FACT_NOT_FOUND", "selected Fact not found", status=409)
        by_review = defaultdict(list)
        ledger_rows = {row["id"]: row for row in full["ledger_entries"]}
        for allocation in full["allocations"]:
            by_review[allocation["review_id"]].append(allocation)
        default_by_fact, default_refs = original_defaults(facts, defaults, full)
        current = self.mapper.allocations_for_facts(affected | raw_ids, active=True)
        current_bundle = self.mapper.bundle({row["review_id"] for row in current})
        current_ledgers = current_bundle["ledger_entries"]
        refs_by_fact = defaultdict(set)
        allocation_fact = {row["ledger_id"]: row["transaction_id"] for row in current}
        for row in current_ledgers:
            if row["id"] in allocation_fact:
                refs_by_fact[allocation_fact[row["id"]]].add(row["account_ref_id"])
        for fact_id in raw_ids:
            values = refs_by_fact[fact_id]
            default_refs[fact_id] = next(iter(values)) if len(values) == 1 else 0
        position_ids = {row["position_id"] for row in full["position_legs"]}
        source_ids, ref_ids, party_ids = set(), set(), set()
        source_ids.update(row["source_position_leg_id"] for row in full["position_legs"]
                          if row["review_id"] in intent.activate_review_ids and row["source_position_leg_id"])
        for draft in raw:
            source_ids.update(row["source"] for row in draft["legs"] if row["source"])
            position_ids.update(row["existing_position_id"] for row in draft["legs"] if row["existing_position_id"] is not None)
            ref_ids.update(row["account_ref_id"] for row in draft["allocations"] if row["account_ref_id"])
            ref_ids.update(row["account_ref_id"] for row in draft["account_bindings"])
            ref_ids.update(default_refs.get(fact_id, 0) for fact_id in draft["transaction_ids"])
            party_ids.update(row["party_id"] for row in draft["new_positions"])
        ref_ids.discard(0)
        ref_ids.update(row["account_ref_id"] for row in current_ledgers if row["account_ref_id"])
        ref_ids.update(row["account_ref_id"] for row in full["ledger_entries"] if row["account_ref_id"])
        positions = {row["id"]: row for row in self.mapper.named_rows("positions", position_ids)}
        refs = {row["id"]: row for row in self.mapper.named_rows("refs", ref_ids)}
        accounts = self.mapper.named_rows("accounts", [row["account_id"] for row in refs.values() if row["account_id"]])
        party_ids.update(row["party_id"] for row in accounts)
        parties = {row["id"]: row for row in self.mapper.named_rows("parties", party_ids)}
        sources = {row["id"]: row for row in self.mapper.named_rows("legs", source_ids)}
        source_reviews = {row["id"]: row for row in self.mapper.named_rows("reviews", [row["review_id"] for row in sources.values()])}
        if any(row["review_id"] in closing for row in sources.values()):
            reject("POSITION_SOURCE_INVALID", "this command would deactivate a new leg's source", status=409)
        drafts = [expand(draft, facts, positions, refs, parties, sources, source_reviews, default_refs,
                         defer_duplicate_source=True) for draft in raw]
        if sum(len(d["allocations"]) + len(d["legs"]) for d in drafts) > 4000 or sum(len(d["position_allocations"]) for d in drafts) > 4000:
            reject("REVIEW_CHANGE_LIMIT", "output publication budget exceeded", status=413)
        incoming_counts = Counter()
        for review_id in intent.activate_review_ids:
            totals = Counter()
            for row in by_review[review_id]:
                totals[row["transaction_id"]] += row["cash_amount"]
            if any(amount != facts[fact_id]["amount"] for fact_id, amount in totals.items()):
                reject("LEGACY_COVERAGE_REVIEW_REQUIRED", "legacy partial Review requires explicit reorganization", status=409)
            incoming_counts.update(totals.keys())
        for draft in drafts:
            incoming_counts.update({row["transaction_id"] for row in draft["allocations"]})
        if any(count > 1 for count in incoming_counts.values()):
            reject("FACT_COVERAGE_REQUIRED", "incoming Reviews overlap on a Fact")
        states = {review_id: 1 for review_id in closing}
        states.update({review_id: 0 for review_id in intent.activate_review_ids})
        restored = set()
        for fact_id in affected - set(incoming_counts):
            if default_by_fact[fact_id] in intent.deactivate_review_ids:
                reject("DEFAULT_DEACTIVATION_FORBIDDEN", "cannot leave a Fact without its original default", status=409)
            restored.add(default_by_fact[fact_id])
        states.update({review_id: 0 for review_id in restored})
        for fact_id in incoming_counts:
            default_id = default_by_fact[fact_id]
            if default_id not in intent.activate_review_ids:
                states[default_id] = 1
        final_coverage = Counter()
        for row in current:
            if row["transaction_id"] in affected and states.get(row["review_id"], 0) == 0:
                final_coverage[row["transaction_id"]] += row["cash_amount"]
        current_ids = {row["review_id"] for row in current}
        for review_id, status in states.items():
            if status == 0 and review_id not in current_ids:
                for row in by_review[review_id]:
                    final_coverage[row["transaction_id"]] += row["cash_amount"]
        for draft in drafts:
            for row in draft["allocations"]:
                final_coverage[row["transaction_id"]] += row["cash_amount"]
        if any(final_coverage[fact_id] != facts[fact_id]["amount"] for fact_id in affected):
            reject("LEGACY_COVERAGE_REVIEW_REQUIRED", "whole-group change cannot restore exact coverage without another explicit decision", status=409)
        final_outputs = defaultdict(list)
        current_ledger_rows = {row["id"]: row for row in current_ledgers}
        for row in current:
            if states.get(row["review_id"], 0) == 0:
                final_outputs[row["transaction_id"]].append(current_ledger_rows[row["ledger_id"]])
        for review_id, status in states.items():
            if status == 0 and review_id not in current_ids:
                for row in by_review[review_id]:
                    final_outputs[row["transaction_id"]].append(ledger_rows[row["ledger_id"]])
        for draft in drafts:
            for row in draft["allocations"]:
                final_outputs[row["transaction_id"]].append(row)
        duplicate_decisions = activation_duplicates + [row for draft in drafts for row in draft["duplicate_transactions"]]
        validate_duplicate_keepers(duplicate_decisions, final_outputs, facts)
        # Source-bound reductions are exact integers; never infer FIFO or transfer an old leg.
        source_premises = self.mapper.source_consumption(sources, states)
        source_consumption = Counter({row["source_id"]: row["amount"] for row in source_premises})
        for draft in drafts:
            for leg in draft["legs"]:
                if leg["leg_direction"] == "OUT":
                    source_consumption[leg["source"]] += leg["leg_amount"]
        if any(source_consumption[source_id] > row["leg_amount"] for source_id, row in sources.items()):
            reject("POSITION_QUANTITY_EXCEEDED", "reductions exceed their explicit source evidence")
        quantity_before = self.mapper.position_totals(position_ids)
        quantity_after = self.mapper.position_totals(position_ids, states)
        before_by_id, after_by_id = ({row["position_id"]: row for row in values} for values in (quantity_before, quantity_after))
        changes = []
        def quantity_view(row):
            if not row or not row["evidence_count"]:
                return dict(quantity_state="UNKNOWN", quantity=None)
            if row["invalid_sources"]:
                return dict(quantity_state="NEEDS_REVIEW", quantity=None)
            if abs(row["quantity"]) > 9_000_000_000_000:
                reject("QUANTITY_LIMIT", "quantity is outside supported range")
            return dict(quantity_state="KNOWN", quantity=row["quantity"])
        for pid in sorted(position_ids):
            row = dict(after_by_id.get(pid, dict(position_id=pid, evidence_count=0, quantity=0, invalid_sources=0)))
            for draft in drafts:
                for leg in draft["legs"]:
                    if leg["existing_position_id"] == pid:
                        row["evidence_count"] += 1
                        row["quantity"] += leg["leg_amount"] * (1 if leg["leg_direction"] == "IN" else -1)
            changes.append(dict(position_id=pid, before=quantity_view(before_by_id.get(pid)), after=quantity_view(row)))
        for review_index, draft in enumerate(drafts):
            for position_index, position in enumerate(draft["new_positions"]):
                quantity = sum(leg["leg_amount"] * (1 if leg["leg_direction"] == "IN" else -1) for leg in draft["legs"]
                               if leg["new_position_index"] == position_index)
                changes.append(dict(new_review_index=review_index, new_position_index=position_index,
                    before=dict(quantity_state="UNKNOWN", quantity=None), after=quantity_view(dict(evidence_count=1, invalid_sources=0, quantity=quantity))))
        expected = [dict(review_id=review_id, status="CONFIRMED" if review_rows[review_id]["status"] == 0 else "REVOKED",
                         updated_time=review_rows[review_id]["updated_time"]) for review_id in sorted(states)]
        supplied = [row.model_dump() for row in intent.expected_reviews]
        if supplied and canonical(sorted(supplied, key=lambda row: row["review_id"])) != canonical(expected):
            reject("ENTITY_CHANGED", "Review state has changed; refresh preview", status=409)
        changed = {review_id for review_id, status in states.items() if review_rows[review_id]["status"] != status}
        affected_ledgers = sorted(row["ledger_id"] for row in full["allocations"] if row["review_id"] in changed)
        changed_legs = [row["id"] for row in full["position_legs"] if row["review_id"] in changed]
        dependents = self.mapper.dependents(changed_legs)
        position_ids.update(row["position_id"] for row in dependents)
        tags = self.mapper.named_rows("tags", affected_ledgers, "ledger_id")
        dictionary = self.mapper.tag_dictionary()
        # Also validates active Views with no Tag rows; an inner join cannot.
        self.tags.mapper.active_dictionary()
        for batch in chunks(affected_ledgers):
            self.tags.mapper.current_states(batch)
        view_ids = {row["view_id"] for row in dictionary}
        projected = (len(affected_ledgers) + sum(len(d["allocations"]) for d in drafts)) * len(view_ids)
        if projected > 50000:
            reject("TAG_IMPACT_LIMIT", "tag effect exceeds publication budget", status=413)
        rules = self.mapper.named_rows("rules", view_ids, "view_id") if affected_ledgers or any(d["allocations"] for d in drafts) else []
        requests = self.mapper.impact_requests(affected_ledgers)
        if max(projected, len(tags)) + len(requests) + len(rules) > 50000:
            reject("TAG_IMPACT_LIMIT", "combined tag/request/rule effect exceeds publication budget", status=413)
        closing_now = {rid for rid in changed if states[rid] == 1}
        effect = tag_effect(full, closing_now, drafts, facts, positions, dictionary, tags, len(requests), True)
        effect.update(affected_views=sorted(view_ids), affected_rule_ids=sorted(row["id"] for row in rules))
        preview_drafts = [dict(case_code=draft["case_code"], type=REVIEW_NAMES[draft["type"]], title=draft["title"],
            allocations=[{key: row[key] for key in ("transaction_id", "economic_type", "cash_amount", "account_ref_id")}
                         for row in draft["allocations"]], new_positions=draft["new_positions"], legs=draft["legs"],
            position_allocations=draft["position_allocations"]) for draft in drafts]
        preview = dict(reviews=[review_po(review_rows[rid]) for rid in sorted(states)],
            new_reviews=preview_drafts, position_changes=changes, coverage=[dict(transaction_id=fid, cash_amount=facts[fid]["amount"],
                                             effective_cash_amount=final_coverage[fid]) for fid in sorted(affected)],
            impact=dict(conflicting_review_ids=sorted(conflicts), restored_default_review_ids=sorted(restored),
                affected_account_ref_ids=sorted(ref_ids | {row["account_ref_id"] for row in full["ledger_entries"] if row["account_ref_id"]}),
                affected_position_ids=sorted(position_ids), dependent_position_leg_ids=sorted(row["id"] for row in dependents),
                tag_ledger_ids=affected_ledgers), blocking_issues=[], expected_reviews=expected,
            tag_effect=effect)
        premises = dict(intent=intent.model_dump(exclude={"expected_reviews", "preview_digest"}), preview=preview,
            facts=list(facts.values()), originals=full, current=current, current_outputs=current_bundle,
            positions=list(positions.values()), refs=list(refs.values()),
            accounts=accounts, parties=list(parties.values()), sources=list(sources.values()), source_reviews=list(source_reviews.values()),
            source_consumption=source_premises,
            quantity_before=quantity_before, quantity_after=quantity_after,
            tags=tags, dictionary=dictionary, rules=rules, requests=requests)
        preview["preview_digest"] = hashlib.sha256(canonical(premises).encode("utf8")).hexdigest()
        return dict(preview=preview, drafts=drafts, facts=facts, states=states, review_rows=review_rows,
                    bundle=full, changed=changed, affected_ledgers=affected_ledgers, view_ids=view_ids)

    @observed("PUBLISH", "publish_duration_ms")
    def command(self, intent: ReviewCommandInput, *, fault=None):
        commit_started = False
        try:
            self.mapper.begin_write()
            self.relations.validate()
            try:
                plan = self._plan(intent)
            except TargetEconomicError as error:
                if error.code in {"POSITION_NOT_ACTIVE", "POSITION_SOURCE_INVALID"}:
                    reject("ENTITY_CHANGED", "Position premises changed; refresh preview", status=409)
                raise
            preview = plan["preview"]
            if canonical([row.model_dump() for row in intent.expected_reviews]) != canonical(preview["expected_reviews"]):
                reject("ENTITY_CHANGED", "complete expected Review states are required", status=409)
            if intent.preview_digest != preview["preview_digest"]:
                reject("ENTITY_CHANGED", "preview premises changed; preview again", status=409)
            reviews, positions, ledger_groups, leg_groups = self.mapper.publish(plan["drafts"], plan["facts"],
                plan["states"], plan["review_rows"], fault=fault)
            new_ids = [row.id for group in ledger_groups for row in group]
            active_old = [row["ledger_id"] for row in plan["bundle"]["allocations"]
                          if row["review_id"] in plan["changed"] and plan["states"][row["review_id"]] == 0]
            for batch in chunks(new_ids + active_old):
                self.tags.sync_ledgers(batch)
            inherited = defaultdict(list)
            committed_mappings = []
            for mapping in preview["tag_effect"]["mappings"]:
                output = mapping["new_output"]
                ledger_id = ledger_groups[output["review_index"]][output["allocation_index"]].id if output else None
                committed_mappings.append({key: value for key, value in mapping.items() if key != "new_output"} |
                                          dict(ledger_id=ledger_id))
                if output:
                    inherited[ledger_id].append(mapping["tag_id"])
            for batch in chunks(inherited):
                self.tags.mapper.replace({lid: tuple(inherited[lid]) for lid in batch})
            affected = sorted(set(plan["affected_ledgers"] + new_ids))
            now = utc_now()
            for batch in chunks(affected):
                self.requests.retire_for_ledger_ids(batch, now=now)
            for view_batch in chunks(plan["view_ids"]):
                self.rules.rewind_for_ledger_ids(affected, now=now, view_ids=set(view_batch))
            if fault:
                fault("tags")
            self.relations.validate()
            result = dict(created_reviews=[review_po(dict(id=row.id, title=row.title, behavior_type=row.behavior_type,
                           status=row.status, created_time=row.created_time, updated_time=row.updated_time)) for row in reviews],
                created_positions=[dict(id=row.id, title=row.title, unit_code=row.unit_code) for group in positions for row in group],
                review_states=[dict(review_id=row["id"], status="CONFIRMED" if row["status"] == 0 else "REVOKED",
                                    updated_time=row["updated_time"]) for row in self.mapper.named_rows("reviews", plan["states"])],
                coverage=preview["coverage"], tag_effect=preview["tag_effect"] | dict(mappings=committed_mappings),
                consumer_state=dict(affected_position_ids=sorted(set(preview["impact"]["affected_position_ids"] +
                                        [row.id for group in positions for row in group])),
                    position_states=[dict(position_id=change.get("position_id") or
                        positions[change["new_review_index"]][change["new_position_index"]].id, **change["after"])
                        for change in preview["position_changes"]],
                    dependent_position_leg_ids=preview["impact"]["dependent_position_leg_ids"]))
            if monotonic() - self.mapper.write_started > 2:
                reject("WRITE_BUSY", "Review write budget exceeded", status=503)
            self.mapper.end_write()
            commit_started = True
            self.db.commit()
            observability.metric("tag_invalidated", "PUBLISH", len({row["ledger_id"]
                for row in plan["bundle"]["allocations"]
                if row["review_id"] in plan["changed"] and plan["states"][row["review_id"]] == 1}))
            observability.metric("manual_mapping_ambiguous", "PUBLISH",
                sum(mapping.get("disposition") == "REVIEW_REQUIRED" for mapping in committed_mappings))
            if fault:
                fault("response")
            return result
        except (OperationalError, IntegrityError) as error:
            self.mapper.end_write()
            self.db.rollback()
            if commit_started:
                reject("RESULT_UNKNOWN", "commit outcome uncertain; inspect current Review before another decision", status=503)
            code = "WRITE_BUSY" if isinstance(error, OperationalError) else "RELATION_BROKEN"
            raise TargetEconomicError(503 if code == "WRITE_BUSY" else 409, "Review command not committed", code=code) from error
        except Exception:
            self.mapper.end_write()
            self.db.rollback()
            if commit_started:
                reject("RESULT_UNKNOWN", "commit outcome uncertain; inspect current Review before another decision", status=503)
            raise
