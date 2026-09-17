from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetEconomicError
from backend.mapper.target_economic_mapper import (
    ECONOMIC_TYPE_IDS,
    TargetEconomicMapper,
)
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewRead,
    TargetEconomicReviewUpdateRequest,
    TargetFactAllocationCandidateRead,
    TargetReviewCandidateFilter,
    TargetReviewCandidatePageRead,
    TargetReviewCandidateSorter,
    TargetReviewFactVO,
    TargetReviewTransitionRequest,
)
from backend.schema.review_case import (
    ReviewCaseFilter,
    ReviewCaseListItem,
    ReviewCasePageRead,
    ReviewCaseSorter,
)
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetEconomicService:
    """Semantic Review CRUD over the compact DB-owned physical schema."""

    def __init__(self, db: Session):
        self.mapper = TargetEconomicMapper(db)
        self.tags = TargetTagProjectionService(db)

    def backfill_defaults(self) -> None:
        self.ensure_defaults(self.mapper.all_fact_ids(), commit=True)

    def ensure_defaults(self, fact_ids: list[int], *, commit: bool = False) -> None:
        fact_ids = sorted(set(fact_ids))
        if not fact_ids:
            return
        facts = self.mapper.facts(fact_ids)
        if len(facts) != len(fact_ids):
            missing = sorted(set(fact_ids) - {fact.id for fact in facts})
            raise TargetEconomicError(409, f"unknown transaction_fact IDs: {missing}")
        coverage = self.mapper.fact_coverage(fact_ids)
        defaults = []
        for fact in facts:
            allocated = coverage.get(fact.id, 0)
            if allocated > fact.amount:
                raise TargetEconomicError(409, f"fact {fact.id} is over-allocated")
            if allocated < fact.amount:
                defaults.append((fact, fact.amount - allocated, fact.account_code))
        self.mapper.create_defaults(defaults, datetime.now())
        self._assert_exact(facts)
        self.tags.sync_ledgers(list(self.mapper.active_economic_facts(fact_ids)))
        if commit:
            self.mapper.commit()

    def page(
        self,
        page: int,
        page_size: int,
        q: str,
        filter_value: ReviewCaseFilter,
        sorter: ReviewCaseSorter,
    ) -> ReviewCasePageRead:
        rows, total = self.mapper.review_page(
            page, page_size, q, filter_value, sorter
        )
        return ReviewCasePageRead(
            items=[ReviewCaseListItem(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            q=q,
            filter=filter_value,
            sorter=sorter,
        )

    def fact_candidates(self, limit: int = 100) -> list[TargetFactAllocationCandidateRead]:
        return [
            TargetFactAllocationCandidateRead(**row)
            for row in self.mapper.fact_candidates(limit)
        ]

    def fact_candidate_page(
        self,
        page: int,
        page_size: int,
        q: str = "",
        filter_value: TargetReviewCandidateFilter | None = None,
        sorter: TargetReviewCandidateSorter | None = None,
    ) -> TargetReviewCandidatePageRead:
        filter_value = filter_value or TargetReviewCandidateFilter()
        sorter = sorter or TargetReviewCandidateSorter()
        rows, total = self.mapper.fact_candidate_page(
            page, page_size, q, filter_value, sorter
        )
        return TargetReviewCandidatePageRead(
            items=[TargetFactAllocationCandidateRead(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            q=q,
            filter=filter_value,
            sorter=sorter,
        )

    review_candidate_page = fact_candidate_page

    def create(self, payload: TargetEconomicReviewCreateRequest) -> TargetEconomicReviewRead:
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
            self._prepare(payload)
            case_id = self.mapper.create_draft(
                behavior_code=payload.behavior_code,
                title=payload.title,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            )
            self.mapper.commit()
            return self._required(case_id)
        except TargetEconomicError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetEconomicError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetEconomicError(409, "economic review write conflict; retry") from error
        except Exception:
            self.mapper.rollback()
            raise

    def update(
        self,
        case_id: int,
        payload: TargetEconomicReviewUpdateRequest,
    ) -> TargetEconomicReviewRead:
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
            self._prepare(payload)
            if not self.mapper.replace_draft(
                case_id,
                expected_version=payload.expected_version,
                behavior_code=payload.behavior_code,
                title=payload.title,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(
                    409, "review is not an editable draft or its version changed"
                )
            self.mapper.commit()
            return self._required(case_id)
        except TargetEconomicError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetEconomicError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetEconomicError(409, "economic review write conflict; retry") from error
        except Exception:
            self.mapper.rollback()
            raise

    def confirm(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
        *,
        restore: bool = False,
    ) -> TargetEconomicReviewRead:
        if restore:
            return self.restore(case_id, payload)
        request_json = self._canonical({
            "operation": "CONFIRM",
            "case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, "CONFIRM", request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            current = self._required(case_id)
            if current.status != "PENDING":
                raise TargetEconomicError(
                    409, f"case status is {current.status}; expected PENDING"
                )
            if current.version != payload.expected_version:
                raise TargetEconomicError(409, "review version changed; reload before confirming")
            plan_payload = self.mapper.latest_plan_payload(case_id)
            if plan_payload is None:
                raise TargetEconomicError(409, "review has no allocation plan")
            plan = TargetEconomicReviewCreateRequest.model_validate(plan_payload)
            facts, economics, allocations = self._prepare(plan)
            fact_ids = sorted({row["fact_id"] for row in allocations})
            self.ensure_defaults(fact_ids)
            default_rows = self.mapper.default_allocations(fact_ids)
            default_by_fact = defaultdict(int)
            for row in default_rows:
                default_by_fact[row["transaction_fact_id"]] += row["amount"]
            requested = defaultdict(int)
            for row in allocations:
                requested[row["fact_id"]] += row["amount"]
            unavailable = {
                fact_id: {
                    "requested": amount,
                    "default_available": default_by_fact.get(fact_id, 0),
                }
                for fact_id, amount in requested.items()
                if amount > default_by_fact.get(fact_id, 0)
            }
            if unavailable:
                raise TargetEconomicError(
                    409, f"allocations are no longer available: {unavailable}"
                )
            fact_by_id = {fact.id: fact for fact in facts}
            residuals = [
                (
                    fact_by_id[fact_id],
                    default_by_fact[fact_id] - amount,
                    fact_by_id[fact_id].account_code,
                )
                for fact_id, amount in requested.items()
                if default_by_fact[fact_id] > amount
            ]
            if not self.mapper.confirm_draft(
                case_id,
                expected_version=payload.expected_version,
                default_rows=default_rows,
                residuals=residuals,
                economics=economics,
                allocations=allocations,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review version changed; reload before confirming")
            self._assert_exact(facts)
            self.tags.sync_ledgers(list(self.mapper.active_economic_facts(fact_ids)))
            self.mapper.commit()
            return self._required(case_id)
        except TargetEconomicError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetEconomicError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetEconomicError(409, "economic review write conflict; retry") from error
        except Exception:
            self.mapper.rollback()
            raise

    def revoke(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetEconomicReviewRead:
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
            case = self._required(case_id)
            if case.status != "CONFIRMED":
                raise TargetEconomicError(
                    409, f"case status is {case.status}; expected CONFIRMED"
                )
            if case.version != payload.expected_version:
                raise TargetEconomicError(409, "review version changed; reload before revoking")
            released = defaultdict(int)
            for row in case.allocations:
                released[row.fact_id] += row.amount
            facts = self.mapper.facts(sorted(released))
            fact_by_id = {fact.id: fact for fact in facts}
            if not self.mapper.revoke_case(
                case_id,
                payload.expected_version,
                fact_by_id,
                dict(released),
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review version changed; reload before revoking")
            self._assert_exact(facts)
            self.tags.sync_ledgers(list(self.mapper.active_economic_facts(sorted(released))))
            self.mapper.commit()
            return self._required(case_id)
        except TargetEconomicError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetEconomicError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetEconomicError(409, "economic review write conflict; retry") from error
        except Exception:
            self.mapper.rollback()
            raise

    def restore(
        self,
        case_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetEconomicReviewRead:
        request_json = self._canonical({
            "operation": "RESTORE",
            "case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, "RESTORE", request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            case = self._required(case_id)
            if case.status != "REVOKED":
                raise TargetEconomicError(
                    409, f"case status is {case.status}; expected REVOKED"
                )
            if case.version != payload.expected_version:
                raise TargetEconomicError(409, "review version changed; reload before restoring")
            requested = defaultdict(int)
            for row in case.allocations:
                requested[row.fact_id] += row.amount
            fact_ids = sorted(requested)
            facts = self.mapper.facts(fact_ids)
            fact_by_id = {fact.id: fact for fact in facts}
            default_rows = self.mapper.default_allocations(fact_ids)
            default_by_fact = defaultdict(int)
            for row in default_rows:
                default_by_fact[row["transaction_fact_id"]] += row["amount"]
            unavailable = {
                fact_id: {
                    "requested": amount,
                    "default_available": default_by_fact.get(fact_id, 0),
                }
                for fact_id, amount in requested.items()
                if amount > default_by_fact.get(fact_id, 0)
            }
            if unavailable:
                raise TargetEconomicError(
                    409, f"allocations are no longer available: {unavailable}"
                )
            residuals = [
                (
                    fact_by_id[fact_id],
                    default_by_fact[fact_id] - amount,
                    fact_by_id[fact_id].account_code,
                )
                for fact_id, amount in requested.items()
                if default_by_fact[fact_id] > amount
            ]
            if not self.mapper.restore_case(
                case_id,
                payload.expected_version,
                default_rows,
                residuals,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review version changed; reload before restoring")
            self._assert_exact(facts)
            self.tags.sync_ledgers(list(self.mapper.active_economic_facts(fact_ids)))
            self.mapper.commit()
            return self._required(case_id)
        except TargetEconomicError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetEconomicError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetEconomicError(409, "economic review write conflict; retry") from error
        except Exception:
            self.mapper.rollback()
            raise

    def detail(self, case_id: int) -> TargetEconomicReviewRead:
        return self._required(case_id)

    def _prepare(self, payload: TargetEconomicReviewCreateRequest):
        keys = [item.client_key for item in payload.economics]
        if len(keys) != len(set(keys)):
            raise ValueError("economic client_key values must be unique")
        definitions = {item.client_key: item for item in payload.economics}
        unknown = sorted({row.economic_key for row in payload.allocations} - set(definitions))
        if unknown:
            raise ValueError(f"unknown economic keys: {unknown}")
        unused = sorted(set(definitions) - {row.economic_key for row in payload.allocations})
        if unused:
            raise ValueError(f"economic items require allocations: {unused}")
        fact_ids = sorted({row.fact_id for row in payload.allocations})
        facts = self.mapper.facts(fact_ids)
        fact_by_id = {fact.id: fact for fact in facts}
        missing = sorted(set(fact_ids) - set(fact_by_id))
        if missing:
            raise ValueError(f"unknown transaction_fact IDs: {missing}")
        fact_totals = defaultdict(int)
        grouped = defaultdict(list)
        allocations = []
        for row in payload.allocations:
            fact = fact_by_id[row.fact_id]
            fact_totals[fact.id] += row.amount
            grouped[row.economic_key].append((row, fact))
            allocations.append({
                "fact_id": fact.id,
                "economic_key": row.economic_key,
                "amount": row.amount,
                "currency_code": fact.currency_code,
            })
        exceeded = {
            fact_id: total
            for fact_id, total in fact_totals.items()
            if total > fact_by_id[fact_id].amount
        }
        if exceeded:
            raise ValueError(f"review allocation exceeds fact amounts: {exceeded}")
        economics = []
        for key in keys:
            rows = grouped[key]
            if len(rows) != 1:
                raise ValueError(
                    f"economic {key} must allocate exactly one transaction_fact"
                )
            row, fact = rows[0]
            definition = definitions[key]
            economics.append({
                "client_key": key,
                "entry_type": ECONOMIC_TYPE_IDS[definition.economic_type],
                "direction": fact.cash_direction,
                "amount": row.amount,
                "currency_code": fact.currency_code,
                "account_code": fact.account_code,
                "occurred_time": fact.occurred_time,
            })
        return facts, economics, allocations

    def _assert_exact(self, facts: tuple[TargetReviewFactVO, ...]) -> None:
        coverage = self.mapper.fact_coverage([fact.id for fact in facts])
        invalid = {
            fact.id: {
                "fact": fact.amount,
                "allocated": coverage.get(fact.id, 0),
            }
            for fact in facts
            if coverage.get(fact.id, 0) != fact.amount
        }
        if invalid:
            raise TargetEconomicError(409, f"fact coverage invariant failed: {invalid}")

    def _required(self, case_id: int) -> TargetEconomicReviewRead:
        case = self.mapper.detail(case_id)
        if case is None:
            raise TargetEconomicError(404, "economic review case not found")
        return case

    def _replay(self, key: str, operation: str, request_json: str):
        row = self.mapper.idempotency(key)
        if row is None:
            return None
        stored = json.loads(row["request_json"] or "{}")
        if stored.get("operation") != operation or row["request_json"] != request_json:
            raise TargetEconomicError(
                409, "idempotency key was already used by another command"
            )
        return self._required(row["case_id"])

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
