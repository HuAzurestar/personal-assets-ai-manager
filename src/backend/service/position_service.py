"""Metadata and evidence reads; cash/legs can only be published through Review."""
from collections import defaultdict
from time import monotonic
from typing import get_args
from sqlalchemy.exc import IntegrityError, OperationalError
from backend.core.unit import unit_definition
from backend.error import ListQueryError, TargetEconomicError
from backend.mapper.position_mapper import PositionMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.mapper.bounded_query_mapper import query_budget, canonical
from backend.schema.list_query import iter_filter_fields, validate_list_capabilities
from backend.schema.review_command import UsageScenario
from backend.schema.identifier import SQLITE_ID_MAX
from backend.service.review_command_service import review_po


def reject(code, message, status=409):
    raise TargetEconomicError(status, message, code=code)


def response_size(value):
    if len(canonical(value).encode()) > 2 * 1024 * 1024:
        reject("DETAIL_LIMIT", "paged response exceeds two MiB; use a smaller page", 413)
    return value


class PositionService:
    def __init__(self, db):
        self.mapper = PositionMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _get(self, position_id):
        row = self.mapper.get(position_id)
        if row is None:
            reject("REFERENCE_NOT_FOUND", "Position not found", 404)
        try:
            unit_definition(row["unit_code"])
        except ValueError:
            reject("RELATION_BROKEN", "Position unit registration is invalid")
        return row

    def _validate_query(self, request, *, search=False, leg=False):
        fields = {"type": ("=", "!="), "usage_scenario": ("=", "!="),
                  "status": ("=", "!="), "party_id": ("=", "!=")}
        if leg:
            fields = {}
        try:
            validate_list_capabilities(request, query_fields=("title", "description", "counterparty") if search else (),
                filter_operators=fields, sorter_fields=("id", "created_time", "updated_time", "occurred_time") if leg
                else ("id", "created_time", "updated_time"), logical_operators=("AND", "OR", "NOT"), max_sorters=3)
        except ListQueryError as error:
            component = error.details.get("component")
            code = "LIST_SORTER_INVALID" if component == "sorter" else "LIST_FILTER_INVALID" if component == "filter" else "LIST_QUERY_INVALID"
            raise ListQueryError(str(error), code=code) from error
        values = {"type": ("ASSET", "LIABILITY"), "status": ("ACTIVE", "ARCHIVED", "SETTLED"),
                  "usage_scenario": get_args(UsageScenario)}
        for expr in iter_filter_fields(request.filter):
            valid = type(expr.val) is int and 0 < expr.val <= SQLITE_ID_MAX if expr.key == "party_id" else isinstance(expr.val, str) and expr.val in values[expr.key]
            if not valid:
                raise ListQueryError("invalid Position filter value", code="LIST_FILTER_INVALID")

    def get(self, position_id):
        with query_budget(self.mapper.db):
            self.relations.read_snapshot()
            self.relations.validate()
            row = self._get(position_id)
            return row | self.mapper.quantity(row)

    def page(self, request):
        self._validate_query(request)
        with query_budget(self.mapper.db):
            self.relations.read_snapshot()
            self.relations.validate()
            rows, total = self.mapper.page(request)
            return response_size(dict(items=rows, total=total, page_index=request.page_index, page_size=request.page_size))

    def search(self, request):
        self._validate_query(request, search=True)
        with query_budget(self.mapper.db):
            self.relations.read_snapshot()
            self.relations.validate()
            return response_size(self.mapper.search(request))

    def legs(self, position_id, request):
        self._validate_query(request, leg=True)
        with query_budget(self.mapper.db):
            self.relations.read_snapshot()
            self.relations.validate()
            position = self._get(position_id)
            rows, total, links, reviews = self.mapper.leg_page(position_id, request)
            by_leg = defaultdict(list)
            for link in links:
                by_leg[link["position_leg_id"]].append(link)
            by_review = {row["id"]: review_po(row) for row in reviews}
            items = [row | dict(unit_code=position["unit_code"], position_allocations=by_leg[row["id"]],
                               review=by_review[row["review_id"]]) for row in rows]
            return response_size(dict(items=items, total=total, page_index=request.page_index, page_size=request.page_size))

    def _write(self, operation):
        commit_started = False
        try:
            self.mapper.begin_write()
            self.relations.validate()
            row = operation()
            self.relations.validate()
            result = row | self.mapper.quantity(row)
            if monotonic() - self.mapper.write_started > 2:
                reject("WRITE_BUSY", "Position metadata write budget exceeded", 503)
            self.mapper.end_write()
            commit_started = True
            self.mapper.db.commit()
            return result
        except Exception as error:
            self.mapper.end_write()
            self.mapper.db.rollback()
            if commit_started:
                reject("RESULT_UNKNOWN", "commit outcome is unknown; query Position candidates, do not repeat POST", 503)
            if isinstance(error, (IntegrityError, OperationalError)):
                reject("WRITE_BUSY", "Position metadata write not committed", 503)
            raise

    def create(self, payload):
        def operation():
            if not payload.title.strip():
                reject("INVALID_POSITION_TITLE", "Position title cannot be blank", 422)
            party = self.mapper.party(payload.party_id)
            if party is None:
                reject("REFERENCE_NOT_FOUND", "managed person not found", 404)
            if party["status"] != "ACTIVE":
                reject("ACCOUNT_NOT_ACTIVE", "managed person is closed")
            return self.mapper.create(payload.model_dump())
        return self._write(operation)

    def update(self, position_id, payload):
        def operation():
            row = self._get(position_id)
            if row["updated_time"] != payload.expected_updated_time:
                reject("ENTITY_CHANGED", "Position metadata changed; reload")
            if payload.status == "SETTLED":
                quantity = self.mapper.quantity(row)
                self.mapper.resume_write_budget()
                if quantity["quantity_state"] != "KNOWN" or quantity["quantity"] != 0:
                    reject("POSITION_NOT_SETTLED", "only a known zero with valid sources may be settled")
            return self.mapper.edit(row, payload.model_dump(exclude={"expected_updated_time"}))
        return self._write(operation)
