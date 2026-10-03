"""Complete informed partition previews, without a queue or financial writes.

Preload the union once; component measurement and batch disclosure are pure
in-memory projections. Every future financial POST still owns its short lock
and revalidates its exact <=1000-row batch through the existing import Service.
"""
from collections import defaultdict
from time import monotonic

from backend.core.import_identity import canonical_json
from backend.core.import_partition import (ImportPartitionImpact, plan_import_partitions,
    COMPOUND_LIMITS, MAX_SELECTION)
from backend.error import TargetIntakeError
from backend.mapper.import_batch_mapper import fail, fingerprint
from backend.service.import_confirmation_service import ImportConfirmationService, row_locator
from backend.service.target_tag_projection_service import TargetTagProjectionService

MAX_OPERATION_BYTES = 24 * 1024 * 1024
MAX_OPERATION_RELATIONS = MAX_SELECTION * 4
MAX_OPERATION_FACTS = MAX_SELECTION * 2


def row_ranges(keys):
    """Inclusive contiguous ranges, never conceal unselected gaps."""
    result = []
    for file_id, number in sorted(keys):
        if result and result[-1]["file_id"] == file_id and result[-1]["row_end"] + 1 == number:
            result[-1]["row_end"] = number
            result[-1]["row_count"] += 1
        else:
            result.append(dict(file_id=file_id, row_start=number, row_end=number, row_count=1))
    return result


class ImportOperationService:
    def __init__(self, mapper, *, deadline):
        self.mapper, self.deadline = mapper, deadline
        self.confirmation = ImportConfirmationService(mapper)

    def check_budget(self):
        if monotonic() > self.deadline:
            fail("QUERY_BUSY", 503)

    @staticmethod
    def existing_ids(keys, candidates, accepted):
        result = set()
        for key in keys:
            if key not in accepted:
                continue
            candidate = candidates[key]
            if candidate["fact_id"]:
                result.add(candidate["fact_id"])
            duplicate = candidate.get("duplicate_plan")
            if duplicate and duplicate["kind"] == "FACT":
                result.add(duplicate["locator"])
        return result

    def index_context(self, context):
        self.context = context
        self.tables = {name: {row["id"]: row for row in context[name]} for name in
            ("facts", "positions", "sources", "source_reviews", "refs", "accounts", "parties")}
        bundle = context["bundle"]
        self.bundle_tables = {name: {row["id"]: row for row in rows} for name, rows in bundle.items()}
        self.rows_by_review = {}
        for name in ("allocations", "position_legs", "position_allocations"):
            grouped = defaultdict(list)
            for row in bundle[name]:
                grouped[row["review_id"]].append(row)
            self.rows_by_review[name] = grouped
        self.reviews_by_fact = defaultdict(set)
        for allocation in bundle["allocations"]:
            id, rid = allocation["transaction_id"], allocation["review_id"]
            if self.bundle_tables["reviews"][rid]["status"] == 0 or context["defaults"].get(id) == rid:
                self.reviews_by_fact[id].add(rid)
        # One immutable identity set per whole Review, reused by every component.
        self.review_impacts = {}
        for review in bundle["reviews"]:
            rid = review["id"]
            allocations = self.rows_by_review["allocations"][rid]
            legs = self.rows_by_review["position_legs"][rid]
            links = self.rows_by_review["position_allocations"][rid]
            self.review_impacts[rid] = dict(
                review_groups={("REVIEW", rid)},
                facts={("FACT", row["transaction_id"]) for row in allocations},
                outputs={("LEDGER", row["ledger_id"]) for row in allocations} | {("LEG", row["id"]) for row in legs},
                position_links={("ALLOCATION", row["id"]) for row in allocations} | {("POSITION_ALLOCATION", row["id"]) for row in links},
                tag_changes=set())

    def context_for(self, ids):
        rids = set().union(*(self.reviews_by_fact[id] for id in ids)) if ids else set()
        bundle = dict(reviews=[self.bundle_tables["reviews"][id] for id in sorted(rids)])
        for name in ("allocations", "position_legs", "position_allocations"):
            bundle[name] = sorted((row for rid in rids for row in self.rows_by_review[name][rid]), key=lambda row: row["id"])
        ledger_ids = {row["ledger_id"] for row in bundle["allocations"]}
        bundle["ledger_entries"] = [self.bundle_tables["ledger_entries"][id] for id in sorted(ledger_ids)]
        fact_ids = ids | {row["transaction_id"] for row in bundle["allocations"]}
        sources = {row["source_position_leg_id"] for row in bundle["position_legs"] if row["source_position_leg_id"]}
        source_reviews = {self.tables["sources"][id]["review_id"] for id in sources}
        refs = {row["account_ref_id"] for row in bundle["ledger_entries"] if row["account_ref_id"]}
        accounts = {self.tables["refs"][id]["account_id"] for id in refs if self.tables["refs"][id]["account_id"]}
        parties = {self.tables["accounts"][id]["party_id"] for id in accounts}
        selected = dict(facts=fact_ids, positions={row["position_id"] for row in bundle["position_legs"]},
            sources=sources, source_reviews=source_reviews, refs=refs, accounts=accounts, parties=parties)
        return dict(defaults={id: self.context["defaults"][id] for id in sorted(ids)}, bundle=bundle,
            **{name: [self.tables[name][id] for id in sorted(values)] for name, values in selected.items()})

    def plan(self, state, order, candidates, risks, source_digest):
        self.check_budget()
        accepted = set()
        for key in order:
            try:
                choice = state.choices.get(key)
                self.mapper.validate_selection({key: candidates[key]}, {key: choice} if choice else {})
                if choice["decision"] == "ACCEPT":
                    accepted.add(key)
            except TargetIntakeError:
                # Only normal row-choice failures become complete child issues;
                # integrity/budget/database errors never become a silent skip.
                pass
        existing = self.existing_ids(order, candidates, accepted)
        context = self.confirmation.target_context(existing, relation_limit=MAX_OPERATION_RELATIONS, fact_limit=MAX_OPERATION_FACTS)
        self.index_context(context)
        new_keys = {candidates[key]["values"]["fact_key"] for key in accepted
            if not candidates[key]["fact_id"] and not candidates[key].get("evidence_link")}
        dictionary = TargetTagProjectionService(self.mapper.db).mapper.active_dictionary() if new_keys else ()
        defaults = [item for item in dictionary if item.tag_system_name == "unclassified"]
        tag_ids = {item.tag_id for item in defaults}
        tag_rows = [row for row in self.mapper.tag_dictionary() if row["id"] in tag_ids] if new_keys else []
        rules = self.mapper.named_rows("rules", {item.view_id for item in defaults}, "view_id") if any(
            candidates[key].get("duplicate_plan") for key in accepted) else []
        if len(canonical_json(dict(context=context, tag_rows=tag_rows, rules=rules)).encode("utf-8")) > MAX_OPERATION_BYTES:
            fail("DETAIL_LIMIT", 413)
        groups = {key: candidates[key]["values"]["fact_key"] for key in order if candidates[key]["values"]}
        targets = {key: candidate["locator"] for key in order
            if (candidate := candidates[key].get("evidence_link") or candidates[key].get("duplicate_plan")) and candidate["kind"] == "ROW"}

        def measure(component):
            self.check_budget()
            ids = self.existing_ids(component, candidates, accepted)
            values = {name: set() for name in COMPOUND_LIMITS}
            rids = set().union(*(self.reviews_by_fact[id] for id in ids)) if ids else set()
            for rid in rids:
                for name in values:
                    values[name].update(self.review_impacts[rid][name])
            new = {candidates[key]["values"]["fact_key"] for key in component if key in accepted
                and not candidates[key]["fact_id"] and not candidates[key].get("evidence_link")}
            duplicate = {candidates[key]["values"]["fact_key"] for key in component
                if key in accepted and candidates[key].get("duplicate_plan")}
            for fact_key in new:
                values["facts"].add(("NEW", fact_key))
                values["review_groups"].add(("DEFAULT", fact_key))
                values["outputs"].add(("DEFAULT_LEDGER", fact_key))
                values["position_links"].add(("DEFAULT_ALLOCATION", fact_key))
                values["tag_changes"].update(("DEFAULT_TAG", fact_key, item.tag_id) for item in defaults)
            if duplicate:
                values["review_groups"].add(("PLANNED_BATCH_DUP",))
                values["tag_changes"].update(("RULE", row["id"]) for row in rules)
            for fact_key in duplicate:
                values["outputs"].add(("DUP_LEDGER", fact_key))
                values["position_links"].add(("DUP_ALLOCATION", fact_key))
                values["tag_changes"].update(("DUP_TAG", fact_key, item.tag_id) for item in defaults)
            return ImportPartitionImpact(compound=bool(duplicate), **{name: frozenset(items) for name, items in values.items()})

        partition = plan_import_partitions(order, group_keys=groups, row_targets=targets, measure=measure)
        batches = []
        for index, selected in enumerate(partition["batches"]):
            self.check_budget()
            keys = selected["selected_rows"]
            prepared = dict(context=self.context_for(self.existing_ids(keys, candidates, accepted)),
                risks={key: risks[key] for key in keys}, dictionary=dictionary, tag_rows=tag_rows, rules=rules)
            disclosure = self.confirmation.plan(state, keys, {key: candidates[key] for key in keys}, source_digest, prepared=prepared)
            if disclosure["budget"] != selected["budget"]:
                fail("RELATION_BROKEN")
            revoke_scope = [row for review in disclosure["effects"]["new_duplicate_reviews"] for row in review["revoke_original_defaults"]]
            batches.append(dict(batch_index=index, row_ranges=row_ranges(keys), preview=disclosure, duplicate_revoke_scope=revoke_scope))
        blocked = [dict(selected_rows=[row_locator(key) for key in item["selected_rows"]],
            row_ranges=row_ranges(item["selected_rows"]), budget=item["budget"], issue=item["issue"] | dict(action="EXCLUDE_OR_REVIEW_BLOCKED_GROUP"))
            for item in partition["blocked"]]
        result = dict(source_preview_digest=source_digest, selected_rows=[row_locator(key) for key in sorted(order)],
            selected_count=len(order), batches=batches, blocked=blocked,
            can_confirm=not blocked and all(item["preview"]["can_confirm"] for item in batches), cross_batch_atomic=False,
            execution_policy=dict(max_batch_rows=1000, serial=True, retain_committed=True,
                stop_on=["CANCEL", "FAILURE", "STALE_PREVIEW", "RESULT_UNKNOWN"], automatic_post_replay=False))
        # Freeze all complete child premises and blockers, not just the first N.
        result["operation_preview_digest"] = fingerprint(result)
        if len(canonical_json(result).encode("utf-8")) > MAX_OPERATION_BYTES:
            fail("DETAIL_LIMIT", 413)
        self.check_budget()
        return result
