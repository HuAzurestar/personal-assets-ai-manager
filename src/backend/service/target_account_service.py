from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.mapper.target_account_mapper import TargetAccountMapper
from backend.mapper.target_review_mapper import TargetReviewMapper
from backend.schema.target_review import (
    TargetAccountSetRequest,
    TargetReviewCaseRead,
    TargetReviewTransitionRequest,
)
from backend.service.target_projection_service import TargetProjectionService
from backend.service.target_review_projection_service import TargetReviewProjectionService
from backend.service.target_review_service import TargetReviewError


class TargetAccountService:
    """Versioned ACCOUNT Review command with atomic hot-projection rebuild."""

    def __init__(self, db: Session):
        self.account = TargetAccountMapper(db)
        self.review = TargetReviewMapper(db)
        self.defaults = TargetProjectionService(db)
        self.financial = TargetReviewProjectionService(db)

    def set(self, fact_id: int, payload: TargetAccountSetRequest) -> TargetReviewCaseRead:
        account_code = payload.account_code.strip()
        if not account_code or account_code == "MULTIPLE":
            raise TargetReviewError(422, "account_code must identify one real account")
        request_json = self._canonical({
            "operation": "ACCOUNT_SET",
            "fact_id": fact_id,
            **payload.model_dump(mode="json"),
            "account_code": account_code,
        })
        try:
            self.review.begin_write()
            replay = self.review.idempotency(payload.idempotency_key)
            if replay is not None:
                if replay.operation != "ACCOUNT_SET" or replay.request_json != request_json:
                    raise TargetReviewError(
                        409, "idempotency key was already used by another command"
                    )
                result = self._required(replay.case_id)
                self.review.commit()
                return result

            target = self.account.target(fact_id)
            if target is None:
                raise TargetReviewError(404, "bill_fact or its ledger projection was not found")
            if target.projection_version != payload.expected_projection_version:
                raise TargetReviewError(
                    409, "ledger projection changed; reload before correcting the account"
                )
            case_id = self.account.account_case_id(fact_id)
            before = self._required(case_id) if case_id else None
            if before is not None and before.status != "CONFIRMED":
                raise TargetReviewError(409, "revoked ACCOUNT Review must be restored before editing")
            now = datetime.now()
            result_json = self._canonical({"account_name": account_code})
            if before is None:
                case_id = self.account.create_case(target, result_json, now)
                version = 1
            else:
                version = self.account.update_case(
                    case_id,
                    target,
                    result_json,
                    before.version,
                    now,
                )
            after = self._required(case_id)
            after_json = self._snapshot(after)
            self.review.add_history(
                case_id=case_id,
                version=version,
                operation="ACCOUNT_SET",
                request_json=request_json,
                before_json=self._snapshot(before) if before else "{}",
                after_json=after_json,
                snapshot_hash=self._hash(after_json),
                reverses_history_id=0,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=now,
            )
            self._republish(target, now)
            self.review.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.review.rollback()
            raise
        except ValueError as error:
            self.review.rollback()
            raise TargetReviewError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.review.rollback()
            raise TargetReviewError(
                409, "account correction write conflict; retry from the latest projection"
            ) from error
        except Exception:
            self.review.rollback()
            raise

    def revoke(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
        *,
        restore: bool = False,
    ) -> TargetReviewCaseRead:
        operation = "RESTORE" if restore else "REVOKE"
        request_json = self._canonical({
            "operation": operation,
            "case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.review.begin_write()
            replay = self.review.idempotency(payload.idempotency_key)
            if replay is not None:
                if replay.operation != operation or replay.request_json != request_json:
                    raise TargetReviewError(
                        409, "idempotency key was already used by another command"
                    )
                result = self._required(replay.case_id)
                self.review.commit()
                return result
            before = self._required(case_id)
            if before.review_type != "ACCOUNT" or len(before.lines) != 1:
                raise TargetReviewError(422, "case is not a one-Fact ACCOUNT Review")
            expected_status = "REVOKED" if restore else "CONFIRMED"
            if before.status != expected_status:
                raise TargetReviewError(
                    409, f"case status is {before.status}; expected {expected_status}"
                )
            if before.version != payload.expected_version:
                raise TargetReviewError(409, "Review version changed; reload before writing")
            target = self.account.target(before.lines[0].bill_id)
            if target is None:
                raise TargetReviewError(409, "ACCOUNT Fact projection is missing")
            now = datetime.now()
            version = self.review.transition(
                case_id,
                status="CONFIRMED" if restore else "REVOKED",
                expected_version=before.version,
                now=now,
            )
            if version is None:
                raise TargetReviewError(409, "Review version changed; reload before writing")
            after = self._required(case_id)
            after_json = self._snapshot(after)
            reversed_history_id = self.account.latest_history_id(
                case_id,
                ("REVOKE",) if restore else ("ACCOUNT_SET", "RESTORE"),
            )
            if not reversed_history_id:
                raise TargetReviewError(409, "reversed ACCOUNT history is missing")
            self.review.add_history(
                case_id=case_id,
                version=version,
                operation=operation,
                request_json=request_json,
                before_json=self._snapshot(before),
                after_json=after_json,
                snapshot_hash=self._hash(after_json),
                reverses_history_id=reversed_history_id,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=now,
            )
            self._republish(target, now)
            self.review.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.review.rollback()
            raise
        except ValueError as error:
            self.review.rollback()
            raise TargetReviewError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.review.rollback()
            raise TargetReviewError(
                409, "account transition write conflict; retry from the latest Review"
            ) from error
        except Exception:
            self.review.rollback()
            raise

    def _republish(self, target, now: datetime) -> None:
        financial_case_id = self.account.financial_case_id(target.ledger_id)
        if financial_case_id:
            financial_case = self._required(financial_case_id)
            fact_ids = sorted({line.bill_id for line in financial_case.lines})
            facts = self.review.facts(fact_ids)
            self.financial.publish(financial_case, facts, now)
        else:
            self.defaults.rebuild_defaults([target.fact_id])

    def _required(self, case_id: int) -> TargetReviewCaseRead:
        case = self.review.detail(case_id)
        if case is None:
            raise TargetReviewError(404, "Review case not found")
        return case

    @classmethod
    def _snapshot(cls, case: TargetReviewCaseRead) -> str:
        return cls._canonical(case.model_dump(
            mode="json",
            exclude={"history", "created_time", "updated_time"},
        ))

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
