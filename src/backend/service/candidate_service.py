from backend.core.money import normalize_currency_code
from backend.core.import_public_text import masked_summary
from backend.error import ListQueryError, TargetEconomicError
from backend.mapper.candidate_mapper import CandidateMapper
from backend.mapper.bounded_query_mapper import query_budget
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.list_query import iter_filter_fields, validate_list_capabilities
from backend.service.position_service import response_size


class CandidateService:
    def __init__(self, db):
        self.mapper = CandidateMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _validate(self, request, search=False):
        fields = {name: ("=", "!=") for name in ("cash_direction", "cash_currency_code", "account_ref_id", "account_id", "party_id", "coverage_state")}
        fields["occurred_time"] = (">=", "<", "between")
        fields["id"] = ("=", "!=")
        validate_list_capabilities(request, query_fields=("summary", "counterparty") if search else (),
            filter_operators=fields, sorter_fields=("id", "occurred_time"), logical_operators=("AND", "OR", "NOT"), max_sorters=3)
        for expression in iter_filter_fields(request.filter):
            key, value = expression.key, expression.val
            valid = True
            if key in ("id", "account_ref_id", "account_id", "party_id"):
                valid = type(value) is int and (0 if key in ("account_ref_id", "account_id") else 1) <= value <= 2**63 - 1
            elif key == "cash_direction":
                valid = value in ("IN", "OUT")
            elif key == "coverage_state":
                valid = value in ("FULL", "PARTIAL", "UNRESOLVED")
            elif key == "cash_currency_code":
                try:
                    valid = isinstance(value, str) and normalize_currency_code(value) == value
                except ValueError:
                    valid = False
            else:
                from datetime import datetime, timezone
                def timestamp(value):
                    if not isinstance(value, str):
                        raise ValueError("timestamp must be text")
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        raise ValueError("timestamp must be aware")
                    return parsed.astimezone(timezone.utc)
                try:
                    if expression.op == "between":
                        if not isinstance(value, dict) or set(value) != {"start", "end"}:
                            raise ValueError("between requires endpoints")
                        expression.val = {key: timestamp(item) for key, item in value.items()}
                        valid = expression.val["start"] < expression.val["end"]
                    else:
                        expression.val = timestamp(value)
                except (ValueError, TypeError):
                    valid = False
            if not valid:
                raise ListQueryError("invalid candidate filter value", code="LIST_FILTER_INVALID")

    @staticmethod
    def po(row):
        if row["allocated_cash_amount"] > row["cash_amount"] or row["allocated_cash_amount"] < 0:
            raise TargetEconomicError(409, "candidate coverage is inconsistent", code="RELATION_BROKEN")
        result = {key: row[key] for key in ("occurred_time", "cash_direction", "cash_amount", "cash_currency_code", "account_ref_id")}
        result.update(summary=masked_summary(row["summary"]))
        result["transaction_id"] = row["id"]
        result["default_review"] = dict(review_id=row["default_review_id"], ledger_id=row["default_ledger_id"]) if row["default_count"] == 1 else None
        result["coverage"] = dict(state=row["coverage_state"], allocated_cash_amount=row["allocated_cash_amount"],
            remaining_cash_amount=row["cash_amount"] - row["allocated_cash_amount"],
            default_identity_state="KNOWN" if row["default_count"] == 1 else "MISSING" if row["default_count"] == 0 else "AMBIGUOUS",
            account_identity_state="MULTIPLE" if row["ref_count"] > 1 else "KNOWN" if row["account_ref_id"] else "UNKNOWN")
        return result

    def page(self, request):
        self._validate(request)
        with query_budget(self.mapper.db):
            self.relations.read_snapshot()
            self.relations.validate()
            rows, total = self.mapper.page(request)
            return response_size(dict(items=[self.po(row) for row in rows], total=total,
                page_index=request.page_index, page_size=request.page_size))

    def search(self, request):
        self._validate(request, search=True)
        with query_budget(self.mapper.db):
            self.relations.read_snapshot()
            self.relations.validate()
            result = self.mapper.search(request)
            result["items"] = [self.po(row) for row in result["items"]]
            return response_size(result)
