"""Read-only flat tag selection with snapshot, literal scan and finite budgets."""
from backend.error import ListQueryError, TargetEconomicError
from backend.mapper.bounded_query_mapper import canonical, query_budget
from backend.mapper.tag_choice_mapper import TagChoiceMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.identifier import SQLITE_ID_MAX
from backend.schema.list_query import iter_filter_fields, validate_list_capabilities
from backend.schema.tag_choice import TagChoicePO

QUERY_SECONDS = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class TagChoiceService:
    def __init__(self, db):
        self.mapper = TagChoiceMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _snapshot(self):
        self.relations.read_snapshot()
        if self.mapper.broken():
            raise TargetEconomicError(409, "tag dictionary reference or status is broken", code="RELATION_BROKEN")

    def _po(self, row):
        values = dict(row)
        values["display_label"] = f'{values["view_name"]} / {values["name"]}'
        return TagChoicePO.model_validate(values).model_dump()

    def _bounded(self, result):
        if len(canonical(result).encode("utf8")) > MAX_RESPONSE_BYTES:
            raise TargetEconomicError(413, "tag choice response exceeds budget; reduce page size", code="DETAIL_LIMIT")
        return result

    def _validate(self, request, *, search=False):
        validate_list_capabilities(request,
            query_fields=("name", "view_name", "display_label") if search else (),
            filter_operators={key: ("=", "!=") for key in ("id", "view_id", "status", "view_status")},
            sorter_fields=("id", "created_time", "updated_time"),
            logical_operators=("AND", "OR", "NOT"), max_sorters=3)
        for expression in iter_filter_fields(request.filter):
            value = expression.val
            valid = (value in ("ACTIVE", "ARCHIVED") if expression.key in ("status", "view_status")
                     else type(value) is int and 1 <= value <= SQLITE_ID_MAX)
            if not valid:
                raise ListQueryError("invalid tag choice filter value", code="LIST_FILTER_VALUE_INVALID")

    def get(self, tag_id):
        with query_budget(self.mapper.db, seconds=QUERY_SECONDS):
            self._snapshot()
            row = self.mapper.get(tag_id)
            if row is None:
                raise TargetEconomicError(404, "tag not found", code="TAG_NOT_FOUND")
            return self._bounded(self._po(row))

    def page(self, request):
        self._validate(request)
        with query_budget(self.mapper.db, seconds=QUERY_SECONDS):
            self._snapshot()
            rows, total = self.mapper.page(request)
            return self._bounded(dict(items=[self._po(row) for row in rows], total=total,
                                      page_index=request.page_index, page_size=request.page_size))

    def search(self, request):
        self._validate(request, search=True)
        with query_budget(self.mapper.db, seconds=QUERY_SECONDS):
            self._snapshot()
            return self._bounded(self.mapper.search(request, self._po))
