"""Complete named one-to-one proposals using the existing evidence validators.

Readonly proposals never change a choice, grant NEW cash consent or publish
financial data. Matching is batch-loaded under the existing shared 2s/50k
guard. Ambiguities stay visible instead of choosing the first eligible Fact.
"""
from collections import defaultdict

from backend.core.import_evidence import parsed_source, plan_same_source_links, plan_cross_source_duplicates
from backend.core.import_pairing import ExactPairingIndex
from backend.core.import_risk import risk_signature
from backend.core.import_identity import SOURCE_CODES
from backend.core.import_public_text import masked_reference, masked_summary
from backend.entity import TransactionImportFile, LedgerEntry
from backend.error import TargetIntakeError, TargetEconomicError
from backend.mapper.import_batch_mapper import fail
from backend.mapper.import_match_mapper import ImportMatchMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service.import_confirmation_service import planned_ref, row_locator, source_label
from backend.service.review_command_service import review_po
from backend.service.review_intent_service import validate_duplicate_keepers


class ImportPairingService:
    def __init__(self, db):
        self.mapper = ImportMatchMapper(db)

    def propose(self, state, choices, kind):
        rows = {key:{field:value for field,value in state.rows[key].items()
            if field not in {"_group_issue","_group_token"}} for key in choices}
        relations = TrustedRelationMapper(self.mapper.db)
        relations.read_snapshot()
        with self.mapper.match_budget():
            relations.validate()
            basic = {key:{field:choice[field] for field in ("decision","recheck")} for key,choice in choices.items()}
            candidates = self.mapper._match(rows,basic,target_limit=20000)
            files = {row["id"]:row for row in self.mapper.rows(TransactionImportFile,TransactionImportFile.id,{key[0] for key in rows})}
            originals = {item["file_id"]:item for item in state.files}
            if any(file["sha256"] != originals[id]["sha256"] for id,file in files.items()):
                fail("IDENTITY_CHANGED")
            # Account-only errors do not prevent evidence-only LINK, whose
            # applied draft explicitly removes the prospective new binding.
            if kind == "SAME_SOURCE":
                for candidate in candidates.values():
                    if candidate["issue"] and candidate["issue"] == candidate["account"]["issue"]:
                        candidate.update(issue=None,classification="EXISTING" if candidate["fact_id"] else "NEW")
            else:
                new = {key:rows[key] for key,candidate in candidates.items() if not candidate["fact_id"]}
                accounts = self.mapper.account_premises(new,{key:choices[key] for key in new})
                for key,account in accounts.items():
                    candidates[key]["account"] = account
                    if account["issue"]:
                        candidates[key].update(issue=account["issue"],classification="INVALID")
            sources = {}
            for key,row in rows.items():
                try:
                    sources[key] = parsed_source(row,files[key[0]])
                except TargetIntakeError as error:
                    sources[key] = dict(issue=error.code)
            anchors,anchor_files = {},defaultdict(set)
            for key,candidate in sorted(candidates.items()):
                choice = choices[key]
                if (choice["decision"] == "ACCEPT" and choice["resolution"] in {"AUTO","NEW"} and
                    candidate["classification"] == "NEW" and not candidate["issue"] and
                    not sources[key].get("issue") and
                    (not candidate["stored"] or candidate["stored"]["row_status"] == 0 or
                        candidate["stored"]["row_status"] in {2,3} and choice["recheck"])):
                    identity = ("NEW",candidate["values"]["fact_key"])
                    anchors.setdefault(identity,key)
                    anchor_files[identity].add(key[0])
            signatures = {signature for candidate in candidates.values() if (signature := risk_signature(candidate["values"])) is not None}
            facts = self.mapper.signature_facts(signatures)
            proofs = self.mapper._evidence_targets([fact["id"] for fact in facts],target_limit=50000)
            targets = [dict(identity=("FACT",fact["id"]),target=dict(kind="FACT",transaction_id=fact["id"]),
                values=fact,proof=proofs[fact["id"]]) for fact in facts]
            targets.extend(dict(identity=identity,target=dict(kind="ROW",**row_locator(key)),
                values=candidates[key]["values"],proof=sources[key],key=key) for identity,key in anchors.items())
            index = ExactPairingIndex(targets)
            items,unique = {},{}
            action = "LINK_EXISTING" if kind == "SAME_SOURCE" else "DUPLICATE"
            for key,candidate in candidates.items():
                choice,stored = choices[key],candidate["stored"]
                reason = ("ROWS_ALREADY_PROCESSED" if stored and stored["row_status"] == 1 else
                    "ROW_RECHECK_REQUIRED" if stored and stored["row_status"] in {2,3} and not choice["recheck"] else
                    "USER_INTENT_RETAINED" if choice["decision"] != "ACCEPT" or choice["resolution"] != "AUTO" else
                    "AUTO_IDENTITY_RETAINED" if candidate["fact_id"] or kind == "SAME_SOURCE" and rows[key].get("reference") else
                    candidate["issue"] or sources[key].get("issue"))
                item = dict(row=row_locator(key),state="EXCEPTION",candidate_count=None,
                    source_label_masked=source_label(rows[key]),suggestion=None,reason_codes=[reason] if reason else [])
                items[key] = item
                if reason:
                    continue
                own = ("NEW",candidate["values"]["fact_key"])
                count,target = index.match(candidate["values"],sources[key],kind,own if own in anchors else None)
                item.update(candidate_count=count,state="NO_MATCH" if count == 0 else "AMBIGUOUS" if count > 1 else "EXCEPTION",
                    reason_codes=["IDENTITY_AMBIGUOUS"] if count > 1 else [])
                if target is not None:
                    unique[key] = target
            # Same-file repetitions cannot silently collapse. Different files
            # may each provide one explicit evidence row for the same keeper.
            collisions = defaultdict(set)
            planned_sources = {("NEW",candidates[key]["values"]["fact_key"]) for key in unique}
            for key,target in unique.items():
                excluded = key if kind == "SAME_SOURCE" else candidates[key]["values"]["fact_key"]
                collisions[(key[0],target["identity"])].add(excluded)
            suggested = {}
            for key,target in unique.items():
                item = items[key]
                try:
                    if len(collisions[(key[0],target["identity"])]) > 1:
                        fail("IDENTITY_AMBIGUOUS",422)
                    if target["identity"] in planned_sources:
                        fail("PAIR_TARGET_REQUIRES_REAL_ANCHOR",422)
                    if target["proof"].get("issue"):
                        fail(target["proof"]["issue"],422)
                    if kind == "SAME_SOURCE" and key[0] in anchor_files.get(target["identity"],set()):
                        fail("INVALID_EVIDENCE_TARGET",422)
                    pair_rows,pair_candidates = {key:rows[key]},{key:candidates[key]}
                    pair_choices = {key:choices[key] | dict(resolution=action,target=target["target"],
                        account_ref_id=None if kind == "SAME_SOURCE" else choices[key]["account_ref_id"])}
                    if target["target"]["kind"] == "ROW":
                        anchor = target["key"]
                        pair_rows[anchor],pair_candidates[anchor],pair_choices[anchor] = rows[anchor],candidates[anchor],choices[anchor]
                    planner = plan_same_source_links if kind == "SAME_SOURCE" else plan_cross_source_duplicates
                    planner(pair_rows,files,pair_candidates,pair_choices,proofs)
                    suggested[key] = target
                except TargetIntakeError as error:
                    item["reason_codes"] = [error.code]
            ids = {target["target"]["transaction_id"] for target in suggested.values() if target["target"]["kind"] == "FACT"}
            summaries = defaultdict(list)
            for current in self.mapper.suggestion_reviews(ids):
                summaries[current["transaction_id"]].append(review_po(current) | dict(title=masked_summary(current["title"]),
                    member_count=current["member_count"],allocated_cash_amount=current["allocated_cash_amount"]))
            outputs = defaultdict(list)
            if kind == "CROSS_SOURCE":
                allocations = self.mapper.allocations_for_facts(ids,active=True,limit=80000)
                ledgers = {row["id"]:row for row in self.mapper.rows(LedgerEntry,LedgerEntry.id,{row["ledger_id"] for row in allocations})}
                for allocation in allocations:
                    outputs[allocation["transaction_id"]].append(ledgers[allocation["ledger_id"]])
            for key,target in suggested.items():
                item,values = items[key],target["proof"]["values"]
                locator = target["target"]
                try:
                    if kind == "CROSS_SOURCE":
                        keeper = target["identity"]
                        excluded = ("ROW",key)
                        keeper_outputs = outputs[locator["transaction_id"]] if locator["kind"] == "FACT" else [dict(
                            entry_type=0,cash_amount=values["amount"],account_ref_id=planned_ref(candidates[target["key"]]["account"]))]
                        final = {keeper:keeper_outputs,excluded:[dict(entry_type=3,cash_amount=candidates[key]["values"]["amount"],
                            account_ref_id=planned_ref(candidates[key]["account"]))]}
                        validate_duplicate_keepers([dict(transaction_id=excluded,kept_transaction_id=keeper)],final,
                            {keeper:values,excluded:candidates[key]["values"]})
                    label = f"{next(name for name,code in SOURCE_CODES.items() if code == target['proof']['source_type'])} {masked_reference(values['account_code'])}"
                    item.update(state="SUGGESTED",suggestion=dict(target=locator,resolution=action,
                        occurred_time=values["occurred_time"],amount=values["amount"],currency_code=values["currency_code"],
                        cash_direction="IN" if values["cash_direction"] == 1 else "OUT",summary_masked=masked_summary(values["summary"]),
                        source_label_masked=label,current_review_summaries=summaries[locator["transaction_id"]] if locator["kind"] == "FACT" else []))
                except TargetEconomicError as error:
                    item["reason_codes"] = [error.code]
            return dict(kind=kind,selected_count=len(choices),items=[items[key] for key in sorted(items)])
