"""One-snapshot canonical Review reads and explicit complete relation pages."""
from backend.core.import_public_text import masked_summary
from backend.error import TargetEconomicError
from backend.mapper.bounded_query_mapper import query_budget
from backend.mapper.review_read_mapper import ReviewReadMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service.flow_read_service import limited
from backend.service.review_command_service import review_po, flow_po


class ReviewReadService:
    def __init__(self, db):
        self.mapper = ReviewReadMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _snapshot(self):
        self.relations.read_snapshot()
        self.relations.validate()

    def _get(self, review_id):
        if type(review_id) is not int or not 1 <= review_id <= 2**63 - 1:
            raise TargetEconomicError(422, "invalid Review identity", code="LIST_FILTER_VALUE_INVALID")
        row = self.mapper.get(review_id)
        if row is None:
            raise TargetEconomicError(404, "Review not found", code="REVIEW_NOT_FOUND")
        return row

    @staticmethod
    def po(row):
        return review_po(row) | dict(title=masked_summary(row["title"]))

    def page(self, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            rows, total = self.mapper.page(request)
            return limited(dict(items=[self.po(row) for row in rows], total=total,
                page_index=request.page_index, page_size=request.page_size))

    def search(self, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            result = self.mapper.search(request, lambda row: row | dict(title=masked_summary(row["title"])))
            result["items"] = [self.po(row) for row in result["items"]]
            return limited(result)

    def detail(self, review_id):
        with query_budget(self.mapper.db):
            self._snapshot()
            review = self._get(review_id)
            # A fixed five-query relation bundle, independently capped before
            # materialization. No Python IN expansion, history or Claim.
            allocations = self.mapper.relations(review_id, "allocation")
            flows = self.mapper.relations(review_id, "flow")
            legs = self.mapper.relations(review_id, "position_leg")
            links = self.mapper.relations(review_id, "position_allocation")
            positions = self.mapper.relations(review_id, "position")
            if 1 + sum(map(len, (allocations, flows, legs, links, positions))) > 4000:
                raise TargetEconomicError(413, "use complete paged Review relations", code="DETAIL_LIMIT")
            return limited(self.po(review) | dict(allocations=allocations, ledger_entries=[flow_po(row) for row in flows],
                position_legs=legs, position_allocations=links, positions=positions))

    def relation_page(self, review_id, kind, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            self._get(review_id)
            rows, total = self.mapper.relation_page(review_id, kind, request)
            if kind == "flow":
                rows = [flow_po(row) for row in rows]
            return limited(dict(items=rows, total=total, page_index=request.page_index, page_size=request.page_size))
