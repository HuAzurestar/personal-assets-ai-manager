"""Read-only persisted progress and evidence, never a command receipt."""
import json
import re
from backend.core.import_identity import canonical_json
from backend.core.import_public_text import masked_reference, public_issue
from backend.entity import TransactionImportFile, TransactionImportRow, TransactionFact
from backend.error import ListQueryError
from backend.mapper.import_batch_mapper import ImportBatchMapper, fail
from backend.mapper.import_source_mapper import ImportSourceMapper
from backend.mapper.bounded_query_mapper import query_budget
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.import_file import parse_import_file_time
from backend.schema.list_query import iter_filter_fields, validate_list_capabilities, BetweenValue


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
                valid = type(between.start) is int and type(between.end) is int and 0 < between.start < between.end
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
            return dict(row=self.source_po(row), raw_payload=payload, fact=facts[0] if facts else None)
        return self.read(action, limit=1024 * 1024)

    def row_relations(self, file_id, row_ids):
        if not 1 <= len(row_ids) <= 100 or len(set(row_ids)) != len(row_ids) or any(type(id) is not int or id <= 0 for id in row_ids):
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
