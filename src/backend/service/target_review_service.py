from __future__ import annotations

from sqlalchemy.orm import Session

from backend.error import TargetReviewError
from backend.mapper.target_review_mapper import TargetReviewMapper
from backend.schema.target_review import TargetReviewCasePageRead, TargetReviewCaseRead


class TargetReviewService:
    """Read boundary shared by account and fact-conflict reviews."""

    def __init__(self, db: Session):
        self.mapper = TargetReviewMapper(db)

    def detail(self, case_id: int) -> TargetReviewCaseRead:
        case = self.mapper.detail(case_id)
        if case is None:
            raise TargetReviewError(404, "review case not found")
        return case

    def page(
        self,
        page: int,
        page_size: int,
        status: str = "",
        review_type: str = "",
    ) -> TargetReviewCasePageRead:
        items, total = self.mapper.page(page, page_size, status, review_type)
        return TargetReviewCasePageRead(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            status=status,
            review_type=review_type,
        )
