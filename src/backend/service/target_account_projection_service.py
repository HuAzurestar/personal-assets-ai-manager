from __future__ import annotations

from sqlalchemy.orm import Session

from backend.mapper.target_account_projection_mapper import TargetAccountProjectionMapper


class EffectiveAccount:
    __slots__ = ("account_code", "review_case_id", "review_version")

    def __init__(self, account_code: str, review_case_id: int, review_version: int):
        self.account_code = account_code
        self.review_case_id = review_case_id
        self.review_version = review_version


class TargetAccountProjectionService:
    def __init__(self, db: Session):
        self.mapper = TargetAccountProjectionMapper(db)

    def effective(self, facts) -> dict[int, EffectiveAccount]:
        facts = tuple(facts)
        reviewed = self.mapper.confirmed([fact.id for fact in facts])
        result = {}
        for fact in facts:
            decision = reviewed.get(fact.id)
            account = (
                decision.account_code if decision else fact.account_code
            ).strip() or "UNKNOWN"
            if account == "MULTIPLE":
                raise ValueError(f"fact {fact.id} account cannot be MULTIPLE")
            result[fact.id] = EffectiveAccount(
                account,
                decision.case_id if decision else 0,
                decision.version if decision else 0,
            )
        return result
