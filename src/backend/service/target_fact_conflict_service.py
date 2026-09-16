from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetReviewError
from backend.mapper.target_fact_conflict_mapper import TargetFactConflictMapper
from backend.mapper.target_review_mapper import TargetReviewMapper
from backend.schema.target_review import (
    TargetFactConflictResolveRequest,
    TargetReviewCaseRead,
    TargetReviewTransitionRequest,
)
from backend.service.target_economic_service import TargetEconomicService


class TargetFactConflictService:
    """Resolve imported immutable-Fact conflicts without rewriting accepted Facts."""

    def __init__(self, db: Session):
        self.mapper = TargetFactConflictMapper(db)
        self.review = TargetReviewMapper(db)
        self.economic = TargetEconomicService(db)

    def resolve(
        self,
        case_id: int,
        payload: TargetFactConflictResolveRequest,
    ) -> TargetReviewCaseRead:
        return self._write(case_id, "RESOLVE", payload)

    def dismiss(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetReviewCaseRead:
        return self._write(case_id, "DISMISS", payload)

    def reopen(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetReviewCaseRead:
        return self._write(case_id, "REOPEN", payload)

    def _write(self, case_id: int, operation: str, payload) -> TargetReviewCaseRead:
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
            if before.review_type != "FACT_CONFLICT":
                raise TargetReviewError(422, "case is not a FACT_CONFLICT Review")
            expected_status = "REJECTED" if operation == "REOPEN" else "PENDING"
            if before.status != expected_status:
                raise TargetReviewError(
                    409, f"case status is {before.status}; expected {expected_status}"
                )
            if before.version != payload.expected_version:
                raise TargetReviewError(409, "Review version changed; reload before writing")
            raw_id = before.result.get("bill_raw_id")
            if not isinstance(raw_id, int) or raw_id <= 0:
                raise TargetReviewError(409, "conflict Review has no valid bill_raw_id")
            raw = self.mapper.raw(raw_id)
            if raw is None or raw.issue_code != "FACT_CONFLICT":
                raise TargetReviewError(409, "conflict raw evidence is missing")
            now = datetime.now()
            reverses_history_id = 0
            if operation == "RESOLVE":
                fact = self._resolved_fact(raw, payload, now)
                result_json = self._canonical({
                    "bill_raw_id": raw.id,
                    "issue_code": raw.issue_code,
                    "issue_message": raw.issue_message,
                    "resolution": {
                        "resolution_type": payload.resolution_type,
                        "bill_id": fact.id,
                    },
                })
                version = self.mapper.write_resolution(
                    case_id=case_id,
                    raw_id=raw.id,
                    fact=fact,
                    result_json=result_json,
                    expected_version=before.version,
                    now=now,
                )
                affected_fact_id = fact.id
            elif operation == "DISMISS":
                result_json = self._result(before, {"resolution_type": "DISMISS"})
                version = self.mapper.write_state(
                    case_id=case_id,
                    raw_id=raw.id,
                    status="REJECTED",
                    raw_status="SKIPPED",
                    allocation_status="COMPLETE",
                    result_json=result_json,
                    expected_version=before.version,
                    now=now,
                )
                affected_fact_id = 0
            else:
                result_json = self._result(before, {})
                reverses_history_id = self.mapper.latest_dismiss_history_id(case_id)
                if not reverses_history_id:
                    raise TargetReviewError(409, "dismiss history is missing")
                version = self.mapper.write_state(
                    case_id=case_id,
                    raw_id=raw.id,
                    status="PENDING",
                    raw_status="INVALID",
                    allocation_status="CONFLICT",
                    result_json=result_json,
                    expected_version=before.version,
                    now=now,
                )
                affected_fact_id = 0
            after = self._required(case_id)
            after_json = self._snapshot(after)
            self.review.add_history(
                case_id=case_id,
                version=version,
                operation=operation,
                request_json=request_json,
                before_json=self._snapshot(before),
                after_json=after_json,
                snapshot_hash=self._hash(after_json),
                reverses_history_id=reverses_history_id,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=now,
            )
            if affected_fact_id:
                self.economic.ensure_defaults([affected_fact_id])
            self.review.commit()
            return self._required(case_id)
        except TargetReviewError:
            self.review.rollback()
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.review.rollback()
            raise TargetReviewError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.review.rollback()
            raise TargetReviewError(
                409, "conflict resolution write conflict; retry from the latest Review"
            ) from error
        except Exception:
            self.review.rollback()
            raise

    def _resolved_fact(self, raw, payload, now):
        if payload.resolution_type == "LINK_EXISTING":
            if payload.existing_bill_id <= 0:
                raise ValueError("LINK_EXISTING requires existing_bill_id")
            fact = self.mapper.fact(payload.existing_bill_id)
            if fact is None:
                raise ValueError("existing bill_fact was not found")
            return fact
        if payload.existing_bill_id:
            raise ValueError("CREATE_NEW does not accept existing_bill_id")
        envelope = json.loads(raw.raw_payload)
        normalized = envelope["normalized"]
        amount = int(normalized["amount_minor"])
        if not amount:
            raise ValueError("conflict amount cannot be zero")
        account = normalized.get("account", {})
        account_code = account.get("identity", "UNKNOWN") if isinstance(account, dict) else "UNKNOWN"
        fact_id = self.mapper.create_fact({
            "fact_key": self._hash(f"resolved-conflict:{raw.id}:{raw.raw_hash}"),
            "occurred_time": datetime.fromisoformat(normalized["occurred_at"]),
            "cash_direction": "IN" if amount > 0 else "OUT",
            "amount_value": abs(amount),
            "amount_scale": 2,
            "currency_code": normalized.get("currency", "CNY"),
            "account_code": account_code or "UNKNOWN",
            "counterparty": normalized.get("merchant", ""),
            "summary": normalized.get("note", ""),
        }, now)
        return self.mapper.fact(fact_id)

    def _required(self, case_id: int) -> TargetReviewCaseRead:
        case = self.review.detail(case_id)
        if case is None:
            raise TargetReviewError(404, "Review case not found")
        return case

    @classmethod
    def _result(cls, case: TargetReviewCaseRead, resolution: dict) -> str:
        return cls._canonical({
            "bill_raw_id": case.result["bill_raw_id"],
            "issue_code": case.result.get("issue_code", "FACT_CONFLICT"),
            "issue_message": case.result.get("issue_message", ""),
            "resolution": resolution,
        })

    @classmethod
    def _snapshot(cls, case: TargetReviewCaseRead) -> str:
        return cls._canonical(case.model_dump(
            mode="json",
            exclude={"history", "created_time", "updated_time"},
        ))

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
