from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.entity import ReviewCase, ReviewCaseBill


@dataclass(frozen=True, slots=True)
class ReviewedAccount:
    account_code: str
    case_id: int
    version: int


class TargetAccountProjectionMapper:
    """Batch-load confirmed ACCOUNT decisions for immutable Facts."""

    def __init__(self, db: Session):
        self.db = db

    def confirmed(self, fact_ids: list[int]) -> dict[int, ReviewedAccount]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewCaseBill.bill_id,
            ReviewCase.id.label("case_id"),
            ReviewCase.version,
            ReviewCase.result_json,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.review_type == "ACCOUNT",
            ReviewCase.status == "CONFIRMED",
        ).order_by(ReviewCaseBill.bill_id, ReviewCase.id)).mappings().all()
        result = {}
        owners = {}
        for row in rows:
            fact_id = row["bill_id"]
            if fact_id in result:
                raise ValueError(
                    f"fact {fact_id} has multiple confirmed ACCOUNT reviews: "
                    f"{owners[fact_id]}, {row['case_id']}"
                )
            try:
                account = json.loads(row["result_json"])["account_name"]
            except (json.JSONDecodeError, TypeError, KeyError):
                raise ValueError(
                    f"ACCOUNT review {row['case_id']} has invalid account_name"
                ) from None
            if not isinstance(account, str) or not account.strip():
                raise ValueError(
                    f"ACCOUNT review {row['case_id']} has invalid account_name"
                )
            result[fact_id] = ReviewedAccount(
                account_code=account.strip(),
                case_id=row["case_id"],
                version=row["version"],
            )
            owners[fact_id] = row["case_id"]
        return result
