"""Coordinate new import duplicates through the existing Review write kernel.

No transaction boundaries, public Review command, guessed IDs or receipts.
The import caller must first validate its complete frozen batch preview.
"""
from collections import defaultdict

from backend.entity import LedgerEntry
from backend.core.review_default import original_defaults
from backend.mapper.import_batch_mapper import fail, fingerprint
from backend.schema.review_command import ReviewChangeInput
from backend.service.import_confirmation_service import ImportConfirmationService
from backend.service.review_command_service import ReviewCommandService
from backend.service.review_intent_service import validate_duplicate_keepers


class ImportDuplicateService:
    def __init__(self, mapper):
        self.mapper = mapper
        self.confirmation = ImportConfirmationService(mapper)

    @staticmethod
    def existing_ids(candidates, choices):
        ids = {candidate["fact_id"] for key, candidate in candidates.items()
               if choices[key]["decision"] == "ACCEPT" and candidate["fact_id"]}
        ids.update(candidate["duplicate_plan"]["locator"] for candidate in candidates.values()
                   if candidate.get("duplicate_plan", {}).get("kind") == "FACT")
        return ids

    def publish(self, candidates, choices, order, result, preview, before_context, *, fault=None):
        records = {(row["file_id"], row["source_row_number"]): row for row in result["processed_rows"]}
        kept, keys = {}, {}
        for key in sorted(candidates):
            candidate = candidates[key]
            pair = candidate.get("duplicate_plan")
            if pair:
                id = records[key]["transaction_id"]
                kept[id] = pair["locator"] if pair["kind"] == "FACT" else records[pair["locator"]]["transaction_id"]
                keys.setdefault(id, key)
        old_ids = self.existing_ids(candidates, choices)
        if fingerprint(self.confirmation.target_context(old_ids)) != fingerprint(before_context):
            fail("STALE_PREVIEW")
        context = self.confirmation.target_context(old_ids | set(kept.values()))
        new_defaults = {row["created_review_id"] for row in records.values() if row["created_review_id"]}
        bundle = self.mapper.bundle(new_defaults | {row["id"] for row in context["bundle"]["reviews"]})
        actual_budget = dict(selected_rows=len(order), review_groups=len(bundle["reviews"]) + 1,
            facts=len({row["transaction_id"] for row in bundle["allocations"]}),
            outputs=len(bundle["ledger_entries"]) + len(bundle["position_legs"]) + len(kept),
            position_links=len(bundle["allocations"]) + len(bundle["position_allocations"]) + len(kept))
        if any(value != preview["budget"][name] for name, value in actual_budget.items()):
            fail("STALE_PREVIEW")
        # Verify actual newly created defaults against frozen source values and
        # account identities before handing their real IDs to Review intent.
        ledgers = {row["id"]: row for row in bundle["ledger_entries"]}
        facts = {row["id"]: row for row in self.mapper.named_rows("facts",
            {row["transaction_id"] for row in records.values() if row["created_review_id"]})}
        original_defaults(facts, [row for row in bundle["allocations"] if row["review_id"] in new_defaults], bundle)
        if any(row["status"] != 0 for row in bundle["reviews"] if row["id"] in new_defaults):
            fail("IDENTITY_CHANGED")
        refs = {row["id"]: row for row in self.mapper.named_rows("refs",
            {row["account_ref_id"] for row in ledgers.values() if row["account_ref_id"]})}
        canonical = {}
        for key in sorted(records):
            if records[key]["created_review_id"]:
                canonical.setdefault(records[key]["created_review_id"], key)
        for key, record in records.items():
            if not record["created_review_id"]:
                continue
            candidate = candidates[key]
            # A reliable same-key supplementary row shares the canonical Fact.
            canonical_key = canonical[record["created_review_id"]]
            values = candidates[canonical_key]["values"]
            if any(facts[record["transaction_id"]][name] != value for name, value in values.items()):
                fail("IDENTITY_CHANGED")
            ledger, account = ledgers[record["created_ledger_id"]], candidate["account"]
            if (ledger["cash_amount"] != values["amount"] or ledger["cash_currency_code"] != values["currency_code"] or
                    ledger["entry_direction"] != values["cash_direction"] or ledger["occurred_time"] != values["occurred_time"]):
                fail("IDENTITY_CHANGED")
            if account["auto_identity"]:
                ref = refs.get(ledger["account_ref_id"])
                if ref is None or [ref["source_namespace"], ref["source_identity"]] != account["auto_identity"]:
                    fail("IDENTITY_CHANGED")
            elif ledger["account_ref_id"] != account["ref_id"]:
                fail("IDENTITY_CHANGED")
        bindings = {id: ledgers[records[key]["created_ledger_id"]]["account_ref_id"] for id, key in keys.items()}
        review_service = ReviewCommandService(self.mapper.db)
        plan = review_service._plan(ReviewChangeInput(new_reviews=[dict(case_code="DUPLICATE", title="",
            parameters=dict(transaction_ids=sorted(kept)),
            account_bindings=[dict(transaction_id=id, account_ref_id=bindings[id]) for id in sorted(kept)],
            duplicate_transactions=[dict(transaction_id=id, kept_transaction_id=kept[id]) for id in sorted(kept)])]))
        allowed = {records[key]["created_review_id"] for key in keys.values()}
        if plan["changed"] != allowed or any(plan["states"][id] != 1 for id in allowed):
            fail("INVALID_DUPLICATE", 422)
        actual_tags = (len(new_defaults) + len(kept)) * len(plan["view_ids"]) + len(plan["preview"]["tag_effect"]["affected_rule_ids"])
        if actual_tags != preview["budget"]["tag_changes"]:
            fail("STALE_PREVIEW")
        def duplicate_fault(stage):
            if fault:
                fault(f"duplicate_{stage}")
        review_service.publish_in_transaction(plan, owner=self.mapper, fault=duplicate_fault)
        # Reuse the keeper check on actual persistent outputs, not just drafts.
        final = self.mapper.allocations_for_facts(set(kept) | set(kept.values()), active=True, limit=4000)
        final_ledgers = {row["id"]: row for row in self.mapper.rows(LedgerEntry, LedgerEntry.id,
            {row["ledger_id"] for row in final})}
        outputs = defaultdict(list)
        effective_by_fact = defaultdict(list)
        for allocation in final:
            ledger = final_ledgers[allocation["ledger_id"]]
            outputs[allocation["transaction_id"]].append(dict(entry_type=ledger["entry_type"],
                cash_amount=ledger["cash_amount"], account_ref_id=ledger["account_ref_id"]))
            effective_by_fact[allocation["transaction_id"]].append(allocation)
        validate_duplicate_keepers([dict(transaction_id=id, kept_transaction_id=target) for id, target in kept.items()],
                                   outputs, plan["facts"])
        stored = self.mapper.source_rows(order)
        for key, record in records.items():
            if (stored[key]["transaction_fact_id"] != record["transaction_id"] or
                    stored[key]["row_status"] != record["row_status"] or
                    stored[key]["raw_hash"] != candidates[key]["raw_hash"]):
                fail("IDENTITY_CHANGED")
            id = record["transaction_id"]
            if id in kept:
                effective = effective_by_fact[id]
                record.update(effective_review_ids=sorted({row["review_id"] for row in effective}),
                    effective_ledger_ids=sorted({row["ledger_id"] for row in effective}),
                    duplicate_kept_transaction_id=kept[id],
                    resolution_effect="EVIDENCE_ONLY" if record["resolution_effect"] == "EVIDENCE_ONLY" else "DUPLICATE_ZERO")
        result["duplicate_fact_count"] = len(kept)
        result["files"] = self.mapper.progress({key[0] for key in order}, persist=True)
        result["remaining_count"] = sum(file["remaining"] for file in result["files"])
        if fault:
            fault("file_counts")
