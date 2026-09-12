from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.mappers.target_review_mapper import TargetReviewMapper
from app.schemas.target_review import (
    TargetReviewCaseRead,
    TargetReviewCreateRequest,
    TargetReviewFactVO,
    TargetReviewLineRequest,
    TargetReviewLineWriteVO,
    TargetReviewTransitionRequest,
    TargetReviewUpdateRequest,
)
from app.services.target_review_projection_service import TargetReviewProjectionService


ROLE_DIRECTIONS = {
    "AA": {"AA_PAID": "OUT", "AA_RECEIVED": "IN"},
    "LOAN_BORROW": {"LOAN_RECEIVED": "IN", "LOAN_REPAID": "OUT"},
    "LOAN_LEND": {"LOAN_LENT": "OUT", "LOAN_RECOVERED": "IN"},
    "REFUND": {"REFUND_RECEIVED": "IN", "REFUND_EXPENSE": "OUT"},
    "TRANSFER": {"TRANSFER_OUT": "OUT", "TRANSFER_IN": "IN", "TRANSFER_FEE": "OUT"},
    "FX_EXCHANGE": {"FX_OUT": "OUT", "FX_IN": "IN", "FX_FEE": "OUT"},
    "DUPLICATE": {"DUPLICATE_RETAINED": None, "DUPLICATE_EXCLUDED": None},
}


class TargetReviewError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class TargetReviewService:
    """Versioned target Review commands with atomic projection updates."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetReviewMapper(db)
        self.projection = TargetReviewProjectionService(db)

    def create(self, payload: TargetReviewCreateRequest) -> TargetReviewCaseRead:
        request_json = self._canonical({
            "operation": "CREATE",
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, "CREATE", request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            facts, lines, allocation_status = self._validated_lines(
                payload.review_type,
                payload.lines,
            )
            now = datetime.now()
            case_id = self.mapper.create_case(
                review_type=payload.review_type,
                allocation_status=allocation_status,
                title=payload.title,
                result_json=self._canonical(payload.result),
                lines=lines,
                now=now,
            )
            case = self._required(case_id)
            after_json = self._snapshot(case)
            self.mapper.add_history(
                case_id=case_id,
                version=1,
                operation="CREATE",
                request_json=request_json,
                before_json="{}",
                after_json=after_json,
                snapshot_hash=self._hash(after_json),
                reverses_history_id=0,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=now,
            )
            self.mapper.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetReviewError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetReviewError(409, "review write conflict; retry from the latest version") from error
        except Exception:
            self.mapper.rollback()
            raise

    def confirm(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
        *,
        restore: bool = False,
    ) -> TargetReviewCaseRead:
        operation = "RESTORE" if restore else "CONFIRM"
        request_json = self._canonical({
            "operation": operation,
            "case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, operation, request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            before = self._required(case_id)
            expected_status = "REVOKED" if restore else "PENDING"
            if before.status != expected_status:
                raise TargetReviewError(
                    409,
                    f"case status is {before.status}; expected {expected_status}",
                )
            if before.version != payload.expected_version:
                raise TargetReviewError(409, "review version changed; reload before confirming")
            fact_ids = sorted({line.bill_id for line in before.lines})
            facts = self.mapper.facts(fact_ids)
            if len(facts) != len(fact_ids):
                raise TargetReviewError(409, "one or more review facts no longer exist")
            owners = self.mapper.confirmed_financial_owners(
                fact_ids,
                exclude_case_id=case_id,
            )
            if owners:
                raise TargetReviewError(409, f"facts already belong to confirmed reviews: {owners}")
            now = datetime.now()
            reversed_history_id = (
                self.mapper.latest_revoke_history_id(case_id) if restore else 0
            )
            version = self.mapper.transition(
                case_id,
                status="CONFIRMED",
                expected_version=payload.expected_version,
                now=now,
            )
            if version is None:
                raise TargetReviewError(409, "review version changed; reload before confirming")
            after = self._required(case_id)
            self.projection.publish(after, facts, now)
            after_json = self._snapshot(after)
            self.mapper.add_history(
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
            self.mapper.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetReviewError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetReviewError(409, "review write conflict; retry from the latest version") from error
        except Exception:
            self.mapper.rollback()
            raise

    def update(
        self,
        case_id: int,
        payload: TargetReviewUpdateRequest,
    ) -> TargetReviewCaseRead:
        request_json = self._canonical({
            "operation": "UPDATE",
            "case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, "UPDATE", request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            before = self._required(case_id)
            if before.status not in {"PENDING", "REVOKED"}:
                raise TargetReviewError(409, "confirmed review must be revoked before editing")
            if before.version != payload.expected_version:
                raise TargetReviewError(409, "review version changed; reload before editing")
            _facts, lines, allocation_status = self._validated_lines(
                before.review_type,
                payload.lines,
            )
            now = datetime.now()
            version = self.mapper.replace_case(
                case_id,
                allocation_status=allocation_status,
                title=payload.title,
                result_json=self._canonical(payload.result),
                lines=lines,
                expected_version=payload.expected_version,
                now=now,
            )
            if version is None:
                raise TargetReviewError(409, "review version changed; reload before editing")
            after = self._required(case_id)
            after_json = self._snapshot(after)
            self.mapper.add_history(
                case_id=case_id,
                version=version,
                operation="UPDATE",
                request_json=request_json,
                before_json=self._snapshot(before),
                after_json=after_json,
                snapshot_hash=self._hash(after_json),
                reverses_history_id=0,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=now,
            )
            self.mapper.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetReviewError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetReviewError(409, "review write conflict; retry from the latest version") from error
        except Exception:
            self.mapper.rollback()
            raise

    def revoke(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetReviewCaseRead:
        request_json = self._canonical({
            "operation": "REVOKE",
            "case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, "REVOKE", request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            before = self._required(case_id)
            if before.status != "CONFIRMED":
                raise TargetReviewError(409, f"case status is {before.status}; expected CONFIRMED")
            if before.version != payload.expected_version:
                raise TargetReviewError(409, "review version changed; reload before revoking")
            fact_ids = sorted({line.bill_id for line in before.lines})
            now = datetime.now()
            reversed_history_id = self.mapper.latest_confirmation_history_id(case_id)
            version = self.mapper.transition(
                case_id,
                status="REVOKED",
                expected_version=payload.expected_version,
                now=now,
            )
            if version is None:
                raise TargetReviewError(409, "review version changed; reload before revoking")
            self.projection.revoke(case_id, fact_ids)
            after = self._required(case_id)
            after_json = self._snapshot(after)
            self.mapper.add_history(
                case_id=case_id,
                version=version,
                operation="REVOKE",
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
            self.mapper.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetReviewError(409, "review write conflict; retry from the latest version") from error
        except Exception:
            self.mapper.rollback()
            raise

    def detail(self, case_id: int) -> TargetReviewCaseRead:
        return self._required(case_id)

    def list(self, limit: int = 100) -> list[TargetReviewCaseRead]:
        return self.mapper.list(limit)

    def _validated_lines(
        self,
        review_type: str,
        requested: list[TargetReviewLineRequest],
    ) -> tuple[
        tuple[TargetReviewFactVO, ...],
        tuple[TargetReviewLineWriteVO, ...],
        str,
    ]:
        bill_ids = sorted({line.bill_id for line in requested})
        facts = self.mapper.facts(bill_ids)
        fact_by_id = {fact.id: fact for fact in facts}
        missing = sorted(set(bill_ids) - set(fact_by_id))
        if missing:
            raise ValueError(f"unknown bill_fact IDs: {missing}")
        role_directions = ROLE_DIRECTIONS[review_type]
        grouped: dict[int, list[TargetReviewLineRequest]] = defaultdict(list)
        for line in requested:
            expected_direction = role_directions.get(line.role, "INVALID")
            if expected_direction == "INVALID":
                raise ValueError(f"role {line.role} is not valid for {review_type}")
            fact = fact_by_id[line.bill_id]
            if expected_direction is not None and fact.cash_direction != expected_direction:
                raise ValueError(
                    f"role {line.role} requires {expected_direction} fact {line.bill_id}"
                )
            grouped[line.bill_id].append(line)

        writes = []
        allocation_complete = True
        for bill_id in bill_ids:
            fact = fact_by_id[bill_id]
            lines = grouped[bill_id]
            missing_amounts = [line for line in lines if line.amount_value is None]
            if len(missing_amounts) > 1:
                raise ValueError(f"fact {bill_id} has more than one implicit amount")
            explicit = sum(line.amount_value or 0 for line in lines)
            remaining = fact.amount_value - explicit
            if remaining < 0 or (missing_amounts and remaining <= 0):
                raise ValueError(f"allocation exceeds fact {bill_id} amount")
            for line in lines:
                amount_value = line.amount_value if line.amount_value is not None else remaining
                writes.append(TargetReviewLineWriteVO(
                    bill_id=bill_id,
                    role=line.role,
                    party=line.party,
                    amount_value=amount_value,
                    amount_scale=fact.amount_scale,
                    currency_code=fact.currency_code,
                ))
            allocated = explicit + (remaining if missing_amounts else 0)
            if allocated < fact.amount_value:
                allocation_complete = False

        roles = [line.role for line in writes]
        if review_type == "DUPLICATE":
            if len(bill_ids) < 2:
                raise ValueError("DUPLICATE review requires at least two facts")
            if roles.count("DUPLICATE_RETAINED") != 1:
                raise ValueError("DUPLICATE review requires exactly one retained fact")
            if roles.count("DUPLICATE_EXCLUDED") != len(bill_ids) - 1:
                raise ValueError("every other duplicate fact must be excluded")
            signature = {
                (fact.cash_direction, fact.amount_value, fact.amount_scale, fact.currency_code)
                for fact in facts
            }
            if len(signature) != 1:
                raise ValueError("duplicate facts must have equal direction, amount, and currency")
        elif review_type == "REFUND" and not {
            "REFUND_RECEIVED", "REFUND_EXPENSE"
        }.issubset(roles):
            raise ValueError("REFUND review requires received and expense facts")
        elif review_type in {"TRANSFER", "FX_EXCHANGE"}:
            prefix = "TRANSFER" if review_type == "TRANSFER" else "FX"
            if not {f"{prefix}_IN", f"{prefix}_OUT"}.issubset(roles):
                raise ValueError(f"{review_type} review requires incoming and outgoing facts")
        return facts, tuple(writes), "COMPLETE" if allocation_complete else "PARTIAL"

    def _replay(
        self,
        key: str,
        operation: str,
        request_json: str,
    ) -> TargetReviewCaseRead | None:
        existing = self.mapper.idempotency(key)
        if existing is None:
            return None
        if existing.operation != operation or existing.request_json != request_json:
            raise TargetReviewError(409, "idempotency key was already used by another command")
        return self._required(existing.case_id)

    def _required(self, case_id: int) -> TargetReviewCaseRead:
        case = self.mapper.detail(case_id)
        if case is None:
            raise TargetReviewError(404, "review case not found")
        return case

    @staticmethod
    def _snapshot(case: TargetReviewCaseRead) -> str:
        return TargetReviewService._canonical(case.model_dump(
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
