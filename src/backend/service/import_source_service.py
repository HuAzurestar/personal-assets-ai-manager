"""Read-only persisted progress and evidence, never a command receipt."""
import json
import re
from collections import defaultdict
from backend.core.import_identity import canonical_json
from backend.core.import_public_text import masked_reference, public_issue
from backend.entity import TransactionImportFile, TransactionImportRow, TransactionFact, LedgerEntry, ReviewCase
from backend.entity.base import utc_now
from backend.error import ListQueryError, TargetEconomicError
from backend.mapper.import_batch_mapper import ImportBatchMapper, fail
from backend.mapper.import_source_mapper import ImportSourceMapper
from backend.mapper.bounded_query_mapper import query_budget
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.import_file import parse_import_file_time
from backend.schema.list_query import iter_filter_fields, validate_list_capabilities, BetweenValue
from backend.service.fact_read_service import fact_po
from backend.service.review_intent_service import validate_duplicate_keepers


class ImportSourceService:
    def __init__(self, db):
        self.db = db
        self.mapper = ImportSourceMapper(db)
        self.progress_mapper = ImportBatchMapper(db)
        self.relations = TrustedRelationMapper(db)

    def read(self, action, *, limit=2 * 1024 * 1024):
        with query_budget(self.db):
            self.relations.read_snapshot()
            self.relations.validate()
            result = action()
            if len(canonical_json(result).encode("utf-8")) > limit:
                fail("DETAIL_LIMIT", 413)
            return result

    def file(self, file_id):
        rows = self.mapper.rows(TransactionImportFile, TransactionImportFile.id, [file_id])
        if not rows:
            fail("REFERENCE_NOT_FOUND", 404)
        if self.mapper.broken_file_sources(file_id):
            fail("RELATION_BROKEN")
        return rows[0]

    def validate(self, request, *, rows=False, search=False):
        filters = {"row_status": ("=",), "source_row_number": ("=", ">=", "<", "between")} if rows else {
            "id": ("=",), "source_type": ("=",), "file_format": ("=",), "status": ("=",), "sha256": ("=",),
            "created_time": (">=", "<", "between"), "updated_time": (">=", "<", "between")}
        validate_list_capabilities(request, query_fields=("issue_code", "issue_message") if rows and search else
            ("filename",) if search else (), filter_operators=filters,
            sorter_fields=("source_row_number",) if rows else ("id", "created_time", "updated_time"),
            logical_operators=("AND",), max_sorters=3)
        for expression in iter_filter_fields(request.filter):
            value, key = expression.val, expression.key
            valid = True
            if key in {"created_time", "updated_time"}:
                if expression.op == "between":
                    between = BetweenValue.model_validate(value)
                    start, end = parse_import_file_time(between.start, key), parse_import_file_time(between.end, key)
                    valid = start < end
                    expression.val = dict(start=start, end=end)
                else:
                    expression.val = parse_import_file_time(value, key)
            elif key == "sha256":
                valid = isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
            elif key == "source_row_number" and expression.op == "between":
                between = BetweenValue.model_validate(value)
                valid = type(between.start) is int and type(between.end) is int and 0 < between.start < between.end <= 2**63 - 1
                expression.val = between.model_dump()
            else:
                allowed = {"source_type": {0, 1, 101, 102, 201, 202, 203}, "file_format": {0, 1, 2, 3, 4},
                           "status": {0, 1, 2, 3}, "row_status": {0, 1, 2, 3}}
                valid = type(value) is int and (value in allowed[key] if key in allowed else 0 < value <= 2**63 - 1)
            if not valid:
                raise ListQueryError("invalid source filter value", code="LIST_FILTER_VALUE_INVALID")

    def page(self, request, *, file_id=None, search=False):
        self.validate(request, rows=file_id is not None, search=search)
        def action():
            if file_id is not None:
                self.file(file_id)
            return self.mapper.query(request, file_id=file_id, search=search)
        return self.read(action)

    def detail(self, file_id):
        def action():
            file = self.file(file_id)
            progress = self.progress_mapper.progress([file_id])[0]
            return dict(file=file, progress={key: progress[key] for key in ("accepted", "skipped", "invalid", "remaining")},
                        coverage="ACTIVITY_RANGE_ONLY")
        return self.read(action)

    @staticmethod
    def source_po(row):
        return {key: value for key, value in row.items() if key not in {"raw_payload", "transaction_fact_id"}} | dict(
            transaction_id=row["transaction_fact_id"], source_reference=masked_reference(row["source_reference"]),
            issue_message=public_issue(row["issue_code"]))

    def row_detail(self, file_id, row_id):
        def action():
            self.file(file_id)
            rows = self.mapper.rows(TransactionImportRow, TransactionImportRow.id, [row_id])
            if not rows or rows[0]["transaction_import_file_id"] != file_id:
                fail("REFERENCE_NOT_FOUND", 404)
            row = rows[0]
            payload = row["raw_payload"]
            if payload is not None and len(payload.encode("utf-8")) > 1024 * 1024:
                fail("DETAIL_LIMIT", 413)
            try:
                payload = json.loads(payload) if payload is not None else None
                if payload is not None and not isinstance(payload, dict):
                    fail("RELATION_BROKEN")
            except (ValueError, TypeError):
                fail("RELATION_BROKEN")
            facts = self.mapper.rows(TransactionFact, TransactionFact.id, [row["transaction_fact_id"]]) if row["transaction_fact_id"] else []
            return dict(row=self.source_po(row), raw_payload=payload, fact=fact_po(facts[0],detail=True) if facts else None)
        return self.read(action, limit=1024 * 1024)

    def row_relations(self, file_id, row_ids):
        if not isinstance(row_ids, list) or not 1 <= len(row_ids) <= 100 or \
           any(type(id) is not int or not 0 < id <= 2**63 - 1 for id in row_ids) or len(set(row_ids)) != len(row_ids):
            fail("INPUT_LIMIT", 422)
        def action():
            self.file(file_id)
            rows = self.mapper.rows(TransactionImportRow, TransactionImportRow.id, row_ids)
            if len(rows) != len(row_ids) or any(row["transaction_import_file_id"] != file_id for row in rows):
                fail("REFERENCE_NOT_FOUND", 404)
            items = self.mapper.relation_rows(file_id, row_ids)
            if len(items) > 4000:
                fail("DETAIL_LIMIT", 413)
            for row in items:
                if row["review_status"] is not None:
                    row["review_status"] = "CONFIRMED" if row["review_status"] == 0 else "REVOKED"
            return dict(items=items, total=len(items))
        return self.read(action)

    def reconcile(self, payload):
        """Read current persisted effects against explicit client locators.

        Not a receipt: an accepted source may have subsequent explanations.
        Nothing is replayed, repaired, activated or guessed from equal money.
        """
        def action():
            keys={(row.file_id,row.source_row_number) for row in payload.rows}
            keys |= {(row.target.file_id,row.target.source_row_number) for row in payload.rows
                if row.target is not None and row.target.kind == "ROW"}
            files={row["id"]:row for row in self.mapper.rows(TransactionImportFile,TransactionImportFile.id,{key[0] for key in keys})}
            proofs={file.file_id:file.sha256 for file in payload.files}
            sources={(row["transaction_import_file_id"],row["source_row_number"]):row for row in self.mapper.located_rows(keys)}
            ids={row["transaction_fact_id"] for row in sources.values() if row["transaction_fact_id"]}
            ids |= {row.target.transaction_id for row in payload.rows if row.target is not None and row.target.kind == "FACT"}
            facts={row["id"]:row for row in self.mapper.rows(TransactionFact,TransactionFact.id,ids,limit=2000)}
            for source in sources.values():
                if (source["row_status"] == 1 and source["transaction_fact_id"] not in facts or
                    source["row_status"] != 1 and source["transaction_fact_id"] != 0):
                    fail("RELATION_BROKEN")
            allocations=self.mapper.allocations_for_facts(facts,limit=4000)
            reviews={row["id"]:row for row in self.mapper.rows(ReviewCase,ReviewCase.id,{row["review_id"] for row in allocations})}
            ledgers={row["id"]:row for row in self.mapper.rows(LedgerEntry,LedgerEntry.id,{row["ledger_id"] for row in allocations})}
            current=defaultdict(list)
            outputs=[]
            names=("TRANSACTION","ACCOUNT_TRANSFER","ASSET_LIABILITY","DUPLICATE")
            for allocation in allocations:
                ledger=ledgers[allocation["ledger_id"]]
                status=reviews[allocation["review_id"]]["status"]
                if status == 0:current[allocation["transaction_id"]].append(ledger)
                outputs.append(dict(transaction_id=allocation["transaction_id"],allocation_id=allocation["id"],
                    review_id=allocation["review_id"],review_status="CONFIRMED" if status == 0 else "REVOKED",
                    ledger_id=ledger["id"],economic_type=names[ledger["entry_type"]],account_ref_id=ledger["account_ref_id"],
                    cash_amount=allocation["cash_amount"],cash_currency_code=allocation["cash_currency_code"],
                    cash_direction="IN" if ledger["entry_direction"] == 1 else "OUT",occurred_time=ledger["occurred_time"]))
            items=[]
            for row in payload.rows:
                key=(row.file_id,row.source_row_number)
                source=sources.get(key)
                item=dict(row=dict(file_id=key[0],source_row_number=key[1]),resolution=row.resolution,
                    row_id=source["id"] if source else 0,row_status=source["row_status"] if source else None,
                    transaction_id=source["transaction_fact_id"] if source else 0,target_transaction_id=0,
                    state="UNRESOLVED",fully_observed=False,reason_codes=[])
                items.append(item)
                target_key=(row.target.file_id,row.target.source_row_number) if row.target is not None and row.target.kind == "ROW" else None
                scoped_files={key[0]} | ({target_key[0]} if target_key else set())
                if any(id not in files or proofs.get(id) != files[id]["sha256"] for id in scoped_files):
                    item["reason_codes"]=["SOURCE_FILE_IDENTITY_REQUIRED"]
                    continue
                if not source:
                    item.update(state="NOT_PERSISTED",reason_codes=["PERSISTED_RESULT_NOT_FOUND"])
                    continue
                if source["row_status"] == 0:
                    item.update(state="UNPROCESSED",reason_codes=["PERSISTED_RESULT_NOT_FINAL"])
                    continue
                if source["row_status"] in {2,3}:
                    item.update(state="SKIPPED" if source["row_status"] == 2 else "INVALID",fully_observed=True,
                        reason_codes=["CURRENT_NON_ACCEPTED_STATE"])
                    continue
                id=source["transaction_fact_id"]
                if row.resolution in {"AUTO","NEW"}:
                    item.update(state="ACCEPTED",fully_observed=True)
                    if any(ledger["entry_type"] == 3 for ledger in current[id]):
                        item.update(state="CURRENT_STATE_CHANGED",reason_codes=["CURRENT_DUPLICATE_WITHOUT_PAIR_CONTEXT"])
                    elif not current[id] or sum(ledger["cash_amount"] for ledger in current[id]) != facts[id]["amount"]:
                        item.update(state="CURRENT_STATE_CHANGED",reason_codes=["CURRENT_CASH_COVERAGE_CHANGED"])
                    if row.decision == "SKIP":item.update(state="CURRENT_STATE_CHANGED",reason_codes=["CURRENT_DECISION_CHANGED"])
                    continue
                if row.target is None:
                    item["reason_codes"]=["PAIR_CONTEXT_REQUIRED"]
                    continue
                if row.target.kind == "FACT":target=row.target.transaction_id
                else:
                    anchor=sources.get(target_key)
                    target=anchor["transaction_fact_id"] if anchor and anchor["row_status"] == 1 else 0
                item["target_transaction_id"]=target
                if not target or target not in facts:
                    item["reason_codes"]=["PAIR_TARGET_NOT_OBSERVED"]
                    continue
                if row.resolution == "LINK_EXISTING":
                    if id != target:
                        item["reason_codes"]=["EVIDENCE_TARGET_CHANGED"]
                    else:item.update(state="EVIDENCE_LINKED",fully_observed=True)
                    continue
                if id == target:
                    item["reason_codes"]=["DUPLICATE_TARGET_CHANGED"]
                    continue
                if any(facts[id][field] != facts[target][field] for field in
                    ("amount","currency_code","cash_direction","occurred_time")):
                    # Immutable core mismatch is a wrong/lost target, not a
                    # later legitimate change of the published explanation.
                    item["reason_codes"]=["PAIR_TARGET_CORE_MISMATCH"]
                    continue
                try:
                    validate_duplicate_keepers([dict(transaction_id=id,kept_transaction_id=target)],current,facts)
                    item.update(state="DUPLICATE_EXCLUDED",fully_observed=True,
                        reason_codes=["KEEPER_LOCATED_FROM_CLIENT_CONTEXT"])
                except TargetEconomicError:
                    # A later legitimate change is not evidence of failed import.
                    item.update(state="CURRENT_STATE_CHANGED",fully_observed=True,
                        reason_codes=["CURRENT_PAIR_EFFECT_CHANGED","KEEPER_LOCATED_FROM_CLIENT_CONTEXT"])
            return dict(observed_at=utc_now(),items=items,fully_observed=all(item["fully_observed"] for item in items),
                current_state_only=True,facts=[dict(id=row["id"],amount=row["amount"],currency_code=row["currency_code"],
                    cash_direction="IN" if row["cash_direction"] == 1 else "OUT",occurred_time=row["occurred_time"]) for row in facts.values()],
                outputs=outputs)
        return self.read(action)
