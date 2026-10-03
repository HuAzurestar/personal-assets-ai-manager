"""Complete read-only import effects and frozen batch premises.

The import Service owns the snapshot or write transaction. This planner never
creates a temporary Fact, guesses an ID, commits, or calls a public command.
"""
from collections import defaultdict

from backend.core.import_identity import canonical_json, source_account_code
from backend.core.import_public_text import masked_reference, masked_summary
from backend.core.review_default import original_defaults
from backend.error import TargetIntakeError
from backend.mapper.import_batch_mapper import fail, fingerprint
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service.review_command_service import review_po, flow_po
from backend.service.target_tag_projection_service import TargetTagProjectionService

MAX_BATCH_PREVIEW_BYTES = 24 * 1024 * 1024


def row_locator(key):
    return dict(file_id=key[0], source_row_number=key[1])


def source_label(row):
    return f"{row.get('source_type', 'unknown')} {masked_reference(source_account_code(row))}"


def group_rows(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


class ImportConfirmationService:
    def __init__(self, mapper):
        self.mapper = mapper

    def target_context(self, ids):
        """Load original/current whole groups, not one output per selected Fact."""
        defaults = self.mapper.allocations_for_facts(ids, defaults=True, limit=4000)
        current = self.mapper.allocations_for_facts(ids, active=True, limit=4000)
        bundle = self.mapper.bundle({row["review_id"] for row in defaults + current})
        fact_ids = set(ids) | {row["transaction_id"] for row in bundle["allocations"]}
        if len(fact_ids) > 2000:
            fail("DETAIL_LIMIT", 413)
        facts = {row["id"]: row for row in self.mapper.named_rows("facts", fact_ids)}
        if set(facts) != fact_ids:
            fail("RELATION_BROKEN")
        default_ids, _refs = original_defaults({id: facts[id] for id in ids}, defaults, bundle)
        positions = self.mapper.named_rows("positions", {row["position_id"] for row in bundle["position_legs"]})
        sources = self.mapper.named_rows("legs", {row["source_position_leg_id"] for row in bundle["position_legs"]
                                                if row["source_position_leg_id"]})
        source_reviews = self.mapper.named_rows("reviews", {row["review_id"] for row in sources})
        refs = self.mapper.named_rows("refs", {row["account_ref_id"] for row in bundle["ledger_entries"] if row["account_ref_id"]})
        accounts = self.mapper.named_rows("accounts", {row["account_id"] for row in refs if row["account_id"]})
        parties = self.mapper.named_rows("parties", {row["party_id"] for row in accounts})
        return dict(defaults=default_ids, facts=list(facts.values()), bundle=bundle, positions=positions,
                    sources=sources, source_reviews=source_reviews, refs=refs, accounts=accounts, parties=parties)

    @staticmethod
    def public_states(context):
        bundle = context["bundle"]
        first = group_rows(bundle["allocations"], "review_id")
        legs = group_rows(bundle["position_legs"], "review_id")
        links = group_rows(bundle["position_allocations"], "review_id")
        ledger_rows = {row["id"]: row for row in bundle["ledger_entries"]}
        positions = {row["id"]: row for row in context["positions"]}
        states = []
        for review in bundle["reviews"]:
            rid = review["id"]
            selected_legs = legs[rid]
            selected_positions = [positions[id] for id in sorted({row["position_id"] for row in selected_legs})]
            public = review_po(review) | dict(title=masked_summary(review["title"]), allocations=first[rid],
                ledger_entries=[flow_po(ledger_rows[row["ledger_id"]]) for row in first[rid]],
                position_legs=[row | dict(unit_code=positions[row["position_id"]]["unit_code"],
                    basis=masked_summary(row["basis"])) for row in selected_legs],
                position_allocations=links[rid], positions=[row | {key: masked_summary(row[key])
                    for key in ("title", "description", "counterparty")} for row in selected_positions])
            states.append(dict(before=public, after_status=public["status"]))
        return states

    def plan(self, state, order, candidates, source_digest):
        order = sorted(order)
        choices = {key: state.choices[key] for key in order if key in state.choices}
        issues, valid = [], set()
        for key in order:
            try:
                self.mapper.validate_selection({key: candidates[key]}, {key: choices[key]} if key in choices else {})
                valid.add(key)
            except TargetIntakeError as error:
                issues.append(row_locator(key) | dict(code=error.code))
        accepted = [key for key in order if key in valid and choices[key]["decision"] == "ACCEPT"]
        new, evidence_only = {}, 0
        for key in accepted:
            candidate = candidates[key]
            if candidate["fact_id"] or candidate.get("evidence_link"):
                evidence_only += 1
            elif candidate["values"]["fact_key"] in new:
                evidence_only += 1
            else:
                new[candidate["values"]["fact_key"]] = key
        existing = {candidates[key]["fact_id"] for key in accepted if candidates[key]["fact_id"]}
        TrustedRelationMapper(self.mapper.db).validate()
        context = self.target_context(existing)
        bundle = context["bundle"]
        outputs = len(new) + len(bundle["ledger_entries"]) + len(bundle["position_legs"])
        link_count = len(new) + len(bundle["allocations"]) + len(bundle["position_allocations"])
        if max(outputs, link_count, len(bundle["reviews"])) > 4000 or len(new) + len(context["facts"]) > 2000:
            fail("DETAIL_LIMIT", 413)
        dictionary, tag_rows = (), []
        if new:
            dictionary = TargetTagProjectionService(self.mapper.db).mapper.active_dictionary()
        default_tags = [item for item in dictionary if item.tag_system_name == "unclassified"]
        tag_count = len(new) * len(default_tags)
        if tag_count > 50000:
            fail("TAG_IMPACT_LIMIT", 413)
        if new:
            default_tag_ids = {item.tag_id for item in default_tags}
            tag_rows = [row for row in self.mapper.tag_dictionary() if row["id"] in default_tag_ids]
        tag_meta = {row["id"]: row for row in tag_rows}
        tag_effect = dict(new_output_count=len(new), affected_view_ids=sorted(item.view_id for item in default_tags),
            default_assignments=[dict(view_id=item.view_id, tag_id=item.tag_id,
                view_name_masked=masked_summary(tag_meta[item.tag_id]["view_name"]),
                tag_name_masked=masked_summary(tag_meta[item.tag_id]["tag_name"])) for item in default_tags],
            projected_assignment_count=tag_count)
        planned, currency = [], {}
        for index, key in enumerate(sorted(new.values())):
            candidate = candidates[key]
            values, account = candidate["values"], candidate["account"]
            planned.append(dict(row=row_locator(key), output_index=index, type="NORMAL_TRANSACTION", economic_type="TRANSACTION",
                cash_direction="IN" if values["cash_direction"] == 1 else "OUT", amount=values["amount"],
                currency_code=values["currency_code"], occurred_time=values["occurred_time"],
                account_ref_id=account["ref_id"] or (None if account["auto_identity"] else 0),
                source_label_masked=source_label(state.rows[key])))
            effect = currency.setdefault(values["currency_code"], dict(currency_code=values["currency_code"],
                cash_in_amount=0, cash_out_amount=0, excluded_in_amount=0, excluded_out_amount=0))
            effect["cash_in_amount" if values["cash_direction"] == 1 else "cash_out_amount"] += values["amount"]
        pairs = []
        issue_by_key = {(issue["file_id"], issue["source_row_number"]): issue["code"] for issue in issues}
        for key in order:
            candidate, choice = candidates[key], choices.get(key, {})
            values, link = candidate["values"], candidate.get("evidence_link")
            target = choice.get("target") or (dict(kind="FACT", transaction_id=candidate["fact_id"]) if candidate["fact_id"] else None)
            labels = [source_label(state.rows[key])]
            if link:
                if link["kind"] == "ROW":
                    labels.append(source_label(state.rows[link["locator"]]))
                else:
                    proof = link["premise"]
                    labels.append(f"source:{proof['source_type']} {masked_reference(proof['account_code'])}")
            pairs.append(dict(row=row_locator(key), target=target, resolution=choice.get("resolution", "AUTO"),
                source_labels_masked=labels, comparison=dict(occurred_time=values["occurred_time"] if values else None,
                    amount=values["amount"] if values else None, currency_code=values["currency_code"] if values else None,
                    cash_direction=("IN" if values["cash_direction"] == 1 else "OUT") if values else None,
                    exact_match=bool(link or candidate["fact_id"]) and not candidate["issue"]),
                reason_codes=[issue_by_key.get(key) or candidate["issue"]]
                             if key in issue_by_key or candidate["issue"] else []))
        invalid = sum(key in valid and choices[key]["decision"] == "SKIP" and bool(candidates[key]["issue"])
                      and candidates[key]["issue"] not in {"NON_POSTED_EVIDENCE", "NEUTRAL_EVIDENCE"} for key in order)
        skipped = sum(key in valid and choices[key]["decision"] == "SKIP" for key in order) - invalid
        states = self.public_states(context)
        expected = [dict(review_id=row["id"], status="CONFIRMED" if row["status"] == 0 else "REVOKED",
                         updated_time=row["updated_time"]) for row in bundle["reviews"]]
        preview = dict(source_preview_digest=source_digest, selected_rows=[row_locator(key) for key in order],
            expected_reviews=expected, counts=dict(new_real_fact=len(new), new_duplicate_fact=0, evidence_only=evidence_only,
                skipped=skipped, invalid=invalid, unresolved=len(issues)), pairs=pairs,
            effects=dict(by_currency=[currency[code] for code in sorted(currency)], before_after_review_states=states,
                new_original_defaults=planned, new_duplicate_reviews=[], tag_effect=tag_effect),
            budget=dict(selected_rows=len(order), review_groups=len(new) + len(bundle["reviews"]),
                facts=len(new) + len(context["facts"]), outputs=outputs, position_links=link_count, tag_changes=tag_count),
            can_confirm=not issues, issues=issues)
        premises = dict(source_digest=source_digest, selected=[dict(row=row_locator(key), choice=choices.get(key),
                        candidate=candidates[key]["premise_hash"]) for key in order],
                        context=context, tag_dictionary=tag_rows, effects=preview)
        if len(canonical_json(premises).encode("utf-8")) > MAX_BATCH_PREVIEW_BYTES:
            fail("DETAIL_LIMIT", 413)
        preview["batch_preview_digest"] = fingerprint(premises)
        return preview
