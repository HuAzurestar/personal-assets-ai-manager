"""Read-only, exact import candidates; suggestions are never consent."""
from collections import defaultdict

from backend.core.import_evidence import parsed_source, plan_same_source_links, plan_cross_source_duplicates
from backend.core.import_identity import SOURCE_CODES
from backend.core.import_public_text import masked_reference, masked_summary
from backend.entity import LedgerEntry, TransactionImportFile
from backend.error import TargetIntakeError, TargetEconomicError, ListQueryError
from backend.mapper.candidate_mapper import CandidateMapper
from backend.mapper.import_batch_mapper import fail
from backend.mapper.import_match_mapper import ImportMatchMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service.import_confirmation_service import planned_ref
from backend.service.position_service import response_size
from backend.service.review_command_service import review_po
from backend.service.review_intent_service import validate_duplicate_keepers
from backend.schema.identifier import SQLITE_ID_MAX


class ImportMatchService:
    def __init__(self, db):
        self.mapper = ImportMatchMapper(db)

    def page(self, state, key, kind, request):
        if not isinstance(key, tuple) or len(key) != 2 or any(
                type(value) is not int or not 0 < value <= SQLITE_ID_MAX for value in key):
            fail("INVALID_EVIDENCE_TARGET", 422)
        if key not in state.rows:
            fail("PREVIEW_ROW_NOT_FOUND", 404)
        if kind not in {"SAME_SOURCE", "CROSS_SOURCE"}:
            fail("INVALID_EVIDENCE_TARGET", 422)
        if request.query:
            raise ListQueryError("exact matches do not support text Query", code="LIST_QUERY_NOT_SUPPORTED")
        if request.filter is not None:
            raise ListQueryError("exact matches do not support Filter", code="LIST_FILTER_NOT_SUPPORTED")
        if request.sorter:
            raise ListQueryError("exact matches have fixed time/ID ordering", code="LIST_SORTER_NOT_SUPPORTED")
        relations = TrustedRelationMapper(self.mapper.db)
        relations.read_snapshot()
        with self.mapper.match_budget():
            relations.validate()
            # Ignore an old suggested target, but re-read this source's current
            # stored identity. A previously accepted source is never re-decided.
            rows = {key: state.rows[key]}
            candidates = self.mapper._match(rows, {})
            candidate = candidates[key]
            if candidate["values"] is None:
                fail("ROW_INVALID", 422)
            # LINK adds evidence, never cash or a new binding. Match's AUTO
            # projection may report an unavailable account for a prospective
            # new default; that account-only issue is irrelevant to LINK. Keep
            # source/group conflicts and persisted processing guards intact.
            if (kind == "SAME_SOURCE" and candidate["issue"] and
                    candidate["issue"] == candidate["account"]["issue"] and not rows[key].get("_group_issue")):
                candidate = candidate | dict(issue=None,
                    classification="EXISTING" if candidate["fact_id"] else "NEW")
                candidates[key] = candidate
            files = {row["id"]: row for row in self.mapper.rows(TransactionImportFile,
                TransactionImportFile.id, [key[0]])}
            if key[0] not in files:
                fail("RELATION_BROKEN")
            original = next(item for item in state.files if item["file_id"] == key[0])
            file = files[key[0]]
            if (file["sha256"] != original["sha256"] or file["source_type"] != SOURCE_CODES.get(rows[key]["source_type"])):
                fail("IDENTITY_CHANGED")
            source, source_issue = None, None
            try:
                source = parsed_source(rows[key], file)
            except TargetIntakeError as error:
                source_issue = error.code
            page, total = self.mapper.exact_page(candidate["values"], source, kind, request)
            ids = [fact["id"] for fact, _proof in page]
            summaries = defaultdict(list)
            for current in CandidateMapper(self.mapper.db).current_reviews([dict(id=id) for id in ids]):
                summaries[current["transaction_id"]].append(review_po(current) | dict(
                    title=masked_summary(current["title"]), member_count=current["member_count"],
                    allocated_cash_amount=current["allocated_cash_amount"]))
            outputs = defaultdict(list)
            if kind == "CROSS_SOURCE":
                allocations = self.mapper.allocations_for_facts(ids, active=True, limit=4000)
                ledgers = {row["id"]: row for row in self.mapper.rows(LedgerEntry, LedgerEntry.id,
                    [row["ledger_id"] for row in allocations])}
                for allocation in allocations:
                    outputs[allocation["transaction_id"]].append(ledgers[allocation["ledger_id"]])
                candidate["account"] = self.mapper.account_premises(rows, state.choices)[key]
            items = []
            for fact, proof in page:
                reasons, actions = [], []
                action = "LINK_EXISTING" if kind == "SAME_SOURCE" else "DUPLICATE"
                try:
                    if source_issue or proof.get("issue"):
                        fail(source_issue or proof["issue"], 422)
                    if kind == "CROSS_SOURCE" and candidate["account"]["issue"]:
                        fail(candidate["account"]["issue"], 422)
                    choice = dict(decision="ACCEPT", resolution=action,
                        target=dict(kind="FACT", transaction_id=fact["id"]))
                    planner = plan_same_source_links if kind == "SAME_SOURCE" else plan_cross_source_duplicates
                    planner(rows, files, candidates, {key: choice}, {fact["id"]: proof})
                    if kind == "CROSS_SOURCE":
                        excluded = ("ROW", key)
                        final = {fact["id"]: outputs[fact["id"]], excluded: [dict(entry_type=3,
                            cash_amount=candidate["values"]["amount"], account_ref_id=planned_ref(candidate["account"]))]}
                        validate_duplicate_keepers([dict(transaction_id=excluded, kept_transaction_id=fact["id"])],
                            final, {fact["id"]: fact, excluded: candidate["values"]})
                    actions.append(action)
                except (TargetIntakeError, TargetEconomicError) as error:
                    reasons.append(error.code)
                label = "来源身份待核对" if proof.get("issue") else (
                    f"{next(name for name, code in SOURCE_CODES.items() if code == proof['source_type'])} "
                    f"{masked_reference(proof['values']['account_code'])}")
                items.append(dict(transaction_id=fact["id"], occurred_time=fact["occurred_time"], amount=fact["amount"],
                    currency_code=fact["currency_code"], cash_direction="IN" if fact["cash_direction"] == 1 else "OUT",
                    summary_masked=masked_summary(fact["summary"]), source_label_masked=label,
                    current_review_summaries=summaries[fact["id"]], eligible_actions=actions, reason_codes=reasons))
            return response_size(dict(items=items, total=total, page_index=request.page_index, page_size=request.page_size))
