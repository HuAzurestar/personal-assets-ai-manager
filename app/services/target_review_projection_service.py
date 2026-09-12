from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.mappers.target_review_projection_mapper import TargetReviewProjectionMapper
from app.schemas.target_projection import FinancialProjectionWriteVO
from app.schemas.target_review import TargetReviewCaseRead, TargetReviewFactVO
from app.services.target_projection_service import TargetProjectionService


class TargetReviewProjectionService:
    """Publish or detach one financial Review inside the caller's transaction."""

    def __init__(self, db: Session):
        self.mapper = TargetReviewProjectionMapper(db)
        self.defaults = TargetProjectionService(db)

    def publish(
        self,
        case: TargetReviewCaseRead,
        facts: tuple[TargetReviewFactVO, ...],
        now: datetime,
    ) -> None:
        fact_by_id = {fact.id: fact for fact in facts}
        fact_ids = tuple(sorted(fact_by_id))
        contributing = list(facts)
        if case.review_type == "DUPLICATE":
            retained = {
                line.bill_id for line in case.lines
                if line.role == "DUPLICATE_RETAINED"
            }
            contributing = [fact_by_id[next(iter(retained))]]
        incoming = self._leg(contributing, "IN")
        outgoing = self._leg(contributing, "OUT")
        if incoming is None or outgoing is None:
            raise ValueError("one cash direction cannot contain multiple currencies")
        fallback = contributing[0]
        in_value, in_scale, in_currency = incoming or (
            0, fallback.amount_scale, fallback.currency_code
        )
        out_value, out_scale, out_currency = outgoing or (
            0, fallback.amount_scale, fallback.currency_code
        )
        in_accounts = {
            fact.account_code or "UNKNOWN"
            for fact in contributing if fact.cash_direction == "IN"
        }
        out_accounts = {
            fact.account_code or "UNKNOWN"
            for fact in contributing if fact.cash_direction == "OUT"
        }
        ledger_type = case.review_type
        if case.review_type == "DUPLICATE":
            ledger_type = "INCOME" if contributing[0].cash_direction == "IN" else "EXPENSE"
        payload = {
            "case": case.model_dump(mode="json", exclude={"history"}),
            "facts": [{
                "id": fact.id,
                "fact_key": fact.fact_key,
                "occurred_time": fact.occurred_time.isoformat(),
                "cash_direction": fact.cash_direction,
                "amount_value": fact.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
                "account_code": fact.account_code,
                "counterparty": fact.counterparty,
                "summary": fact.summary,
            } for fact in sorted(facts, key=lambda item: item.id)],
        }
        input_hash = hashlib.sha256(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        self.mapper.publish(FinancialProjectionWriteVO(
            fact_ids=fact_ids,
            case_id=case.id,
            ledger_type=ledger_type,
            allocation_status=case.allocation_status,
            title=case.title or contributing[0].counterparty or contributing[0].summary,
            start_time=min(fact.occurred_time for fact in facts),
            end_time=max(fact.occurred_time for fact in facts),
            in_amount_value=in_value,
            in_amount_scale=in_scale,
            in_currency_code=in_currency,
            out_amount_value=out_value,
            out_amount_scale=out_scale,
            out_currency_code=out_currency,
            in_account_code=self._account(in_accounts),
            out_account_code=self._account(out_accounts),
            input_hash=input_hash,
            created_time=min(fact.created_time for fact in facts),
            updated_time=now,
        ))

    def revoke(self, case_id: int, fact_ids: list[int]) -> None:
        self.mapper.detach(case_id)
        self.defaults.rebuild_defaults(fact_ids)

    @staticmethod
    def _leg(facts: list[TargetReviewFactVO], direction: str):
        selected = [fact for fact in facts if fact.cash_direction == direction]
        if not selected:
            return ()
        currencies = {fact.currency_code for fact in selected}
        if len(currencies) != 1:
            return None
        scale = max(fact.amount_scale for fact in selected)
        value = sum(
            fact.amount_value * 10 ** (scale - fact.amount_scale)
            for fact in selected
        )
        return value, scale, selected[0].currency_code

    @staticmethod
    def _account(values: set[str]) -> str:
        if not values:
            return "UNKNOWN"
        if len(values) > 1:
            return "MULTIPLE"
        return next(iter(values))
