from __future__ import annotations

from sqlalchemy.orm import Session

from backend.error import TargetEconomicError
from backend.mapper.target_economic_mapper import TargetEconomicMapper
from backend.schema.target_review import (
    TargetFactAllocationCandidateRead,
    TargetReviewCandidateFilter,
    TargetReviewCandidateListBody,
    TargetReviewCandidateListRequest,
    TargetReviewCandidateSorter,
    parse_review_time,
)
from backend.schema.review_case import (
    ReviewCaseFilter,
    ReviewCaseListBody,
    ReviewCaseListItem,
    ReviewCaseListRequest,
    ReviewCaseSorter,
    parse_review_case_time,
)
from backend.schema.list_query import BetweenValue, iter_filter_fields
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetEconomicService:
    """Transitional read/intake facade; all legacy Review writes are retired."""

    def __init__(self, db: Session):
        self.mapper = TargetEconomicMapper(db)
        self.tags = TargetTagProjectionService(db)

    def backfill_defaults(self) -> None:
        raise TargetEconomicError(410, "startup default backfill retired", code="REVIEW_WRITE_RETIRED")

    def ensure_defaults(self, fact_ids: list[int], *, commit: bool = False, account_refs=None) -> None:
        """Compatibility intake hook: initialize truly unallocated new Facts only."""
        from backend.mapper.review_command_mapper import ReviewCommandMapper, chunks
        mapper = ReviewCommandMapper(self.mapper.db)
        fact_ids = sorted(set(fact_ids))
        if not fact_ids:
            return
        facts = mapper.named_rows("facts", fact_ids)
        if len(facts) != len(fact_ids):
            raise TargetEconomicError(409, "unknown Fact", code="FACT_NOT_FOUND")
        existing = mapper.allocations_for_facts(fact_ids)
        allocated = {row["transaction_id"] for row in existing}
        defaults = mapper.allocations_for_facts(allocated, defaults=True)
        default_facts = [row["transaction_id"] for row in defaults]
        if len(default_facts) != len(set(default_facts)) or set(default_facts) != allocated:
            raise TargetEconomicError(409, "original defaults need review", code="DEFAULT_IDENTITY_REQUIRED")
        new_ids = set(fact_ids) - allocated
        if new_ids:
            _, _, ledgers, _ = mapper.create_initial_defaults(new_ids, account_refs=account_refs)
            for batch in chunks(row.id for group in ledgers for row in group):
                self.tags.sync_ledgers(batch)
        if commit:
            self.mapper.commit()

    def page(
        self,
        *,
        request: ReviewCaseListRequest,
    ) -> ReviewCaseListBody:
        filter_value = self._review_mapper_filter(request)
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = ReviewCaseSorter(
            field=sorter_expression.key if sorter_expression else "updated_time",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.review_page(
            request.page_index, request.page_size, filter_value, sorter
        )
        return ReviewCaseListBody(
            items=[ReviewCaseListItem(**row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    @staticmethod
    def _review_mapper_filter(request: ReviewCaseListRequest) -> ReviewCaseFilter:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key in {"created_time", "updated_time"}:
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values[f"{expression.key}_start"] = parse_review_case_time(between.start, expression.key)
                    values[f"{expression.key}_end"] = parse_review_case_time(between.end, expression.key)
                elif expression.op == ">=":
                    values[f"{expression.key}_start"] = parse_review_case_time(expression.val, expression.key)
                else:
                    values[f"{expression.key}_end"] = parse_review_case_time(expression.val, expression.key)
            else:
                values[expression.key] = expression.val
        return ReviewCaseFilter.model_validate(values)

    def fact_candidates(self, limit: int = 100) -> list[TargetFactAllocationCandidateRead]:
        return [
            TargetFactAllocationCandidateRead(**row)
            for row in self.mapper.fact_candidates(limit)
        ]

    def fact_candidate_page(
        self,
        *,
        request: TargetReviewCandidateListRequest,
    ) -> TargetReviewCandidateListBody:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key == "occurred_time":
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values["occurred_time_start"] = parse_review_time(between.start, expression.key)
                    values["occurred_time_end"] = parse_review_time(between.end, expression.key)
                elif expression.op == ">=":
                    values["occurred_time_start"] = parse_review_time(expression.val, expression.key)
                else:
                    values["occurred_time_end"] = parse_review_time(expression.val, expression.key)
                continue
            values[expression.key] = (
                str(expression.val).strip().upper()
                if expression.key == "currency_code"
                else expression.val
            )
        filter_value = TargetReviewCandidateFilter.model_validate(values)
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = TargetReviewCandidateSorter(
            field=sorter_expression.key if sorter_expression else "occurred_time",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.fact_candidate_page(
            request.page_index, request.page_size, "", filter_value, sorter
        )
        return TargetReviewCandidateListBody(
            items=[TargetFactAllocationCandidateRead(**row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    review_candidate_page = fact_candidate_page

    def create(self, payload):
        raise TargetEconomicError(410, "use Review preview/command", code="REVIEW_WRITE_RETIRED")

    def revoke(self, case_id, payload):
        raise TargetEconomicError(410, "use Review preview/command", code="REVIEW_WRITE_RETIRED")

    def restore(self, case_id, payload):
        raise TargetEconomicError(410, "use Review preview/command", code="REVIEW_WRITE_RETIRED")

    def detail(self, case_id):
        from backend.service.review_command_service import ReviewCommandService
        return ReviewCommandService(self.mapper.db).detail(case_id)
