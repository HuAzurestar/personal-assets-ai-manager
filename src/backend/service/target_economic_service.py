from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetEconomicError
from backend.mapper.target_economic_mapper import TargetEconomicMapper
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewListItem,
    TargetEconomicReviewPageRead,
    TargetEconomicReviewRead,
    TargetEconomicReviewTransitionRequest,
    TargetFactAllocationCandidatePageRead,
    TargetFactAllocationCandidateRead,
    TargetReviewFactVO,
)
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetEconomicService:
    """Confirmed Fact -> Review -> Ledger allocation use cases."""

    def __init__(self, db: Session):
        self.mapper = TargetEconomicMapper(db)
        self.tags = TargetTagProjectionService(db)

    def ensure_defaults(self, fact_ids: list[int], *, commit: bool = False) -> None:
        """Give every accepted fact exact confirmed INCOME_AND_EXPENSE coverage."""

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
            if allocated > fact.amount_value:
                raise TargetEconomicError(409, f"fact {fact.id} is over-allocated")
            if allocated < fact.amount_value:
                defaults.append((fact, fact.amount_value - allocated, fact.account_code))
        self.mapper.create_defaults(defaults, datetime.now())
        self._assert_exact(facts)
        self.tags.sync_ledgers(list(self.mapper.active_economic_facts(fact_ids)))
        if commit:
            self.mapper.commit()

    def page(
        self,
        page: int,
        page_size: int,
        status: int | None = None,
    ) -> TargetEconomicReviewPageRead:
        rows, total = self.mapper.review_page(page, page_size, status)
        return TargetEconomicReviewPageRead(
            items=[TargetEconomicReviewListItem(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
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
    ) -> TargetFactAllocationCandidatePageRead:
        rows, total = self.mapper.fact_candidate_page(page, page_size)
        return TargetFactAllocationCandidatePageRead(
            items=[TargetFactAllocationCandidateRead(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def create(self, payload: TargetEconomicReviewCreateRequest) -> TargetEconomicReviewRead:
        request_json = self._canonical({
            "operation": "CREATE",
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, 0, request_json)
            if replay is not None:
                self.mapper.commit()
                return replay

            facts, ledger_definitions, allocations = self._prepare(payload)
            fact_ids = sorted({row["transaction_fact_id"] for row in allocations})
            self.ensure_defaults(fact_ids)
            default_rows = self.mapper.default_allocations(fact_ids)
            default_by_fact: dict[int, int] = defaultdict(int)
            for row in default_rows:
                default_by_fact[row["transaction_fact_id"]] += row["amount_value"]
            requested: dict[int, int] = defaultdict(int)
            for row in allocations:
                requested[row["transaction_fact_id"]] += row["amount_value"]
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
                    409,
                    f"allocations are no longer available: {unavailable}",
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
            case_id = self.mapper.create_published(
                behavior_type=payload.behavior_type,
                title=payload.title,
                default_rows=default_rows,
                residuals=residuals,
                ledger_definitions=ledger_definitions,
                allocations=allocations,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            )
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
        payload: TargetEconomicReviewTransitionRequest,
    ) -> TargetEconomicReviewRead:
        request_json = self._canonical({
            "operation": "REVOKE",
            "review_case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, 2, request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            case = self._required(case_id)
            if case.status != 0:
                raise TargetEconomicError(409, "review is already revoked")
            released: dict[int, int] = defaultdict(int)
            for row in case.allocations:
                released[row.transaction_fact_id] += row.amount_value
            facts = self.mapper.facts(sorted(released))
            fact_by_id = {fact.id: fact for fact in facts}
            if not self.mapper.revoke_case(
                case_id,
                fact_by_id,
                dict(released),
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review changed while it was being revoked")
            self._assert_exact(facts)
            self.tags.sync_ledgers(list(
                self.mapper.active_economic_facts(sorted(released))
            ))
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
        payload: TargetEconomicReviewTransitionRequest,
    ) -> TargetEconomicReviewRead:
        request_json = self._canonical({
            "operation": "RESTORE",
            "review_case_id": case_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, 3, request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            case = self._required(case_id)
            if case.status != 1:
                raise TargetEconomicError(409, "review is already confirmed")
            requested: dict[int, int] = defaultdict(int)
            for row in case.allocations:
                requested[row.transaction_fact_id] += row.amount_value
            fact_ids = sorted(requested)
            facts = self.mapper.facts(fact_ids)
            fact_by_id = {fact.id: fact for fact in facts}
            default_rows = self.mapper.default_allocations(fact_ids)
            default_by_fact: dict[int, int] = defaultdict(int)
            for row in default_rows:
                default_by_fact[row["transaction_fact_id"]] += row["amount_value"]
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
                    409,
                    f"allocations are no longer available: {unavailable}",
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
                default_rows,
                residuals,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review changed while it was being restored")
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

    def _prepare(
        self,
        payload: TargetEconomicReviewCreateRequest,
    ) -> tuple[tuple[TargetReviewFactVO, ...], list[dict], list[dict]]:
        keys = [item.client_key for item in payload.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("ledger client_key values must be unique")
        definitions = {item.client_key: item for item in payload.entries}
        unknown = sorted({row.entry_key for row in payload.allocations} - set(definitions))
        if unknown:
            raise ValueError(f"unknown ledger keys: {unknown}")
        unused = sorted(set(definitions) - {row.entry_key for row in payload.allocations})
        if unused:
            raise ValueError(f"ledger entries require allocations: {unused}")
        fact_ids = sorted({row.transaction_fact_id for row in payload.allocations})
        facts = self.mapper.facts(fact_ids)
        fact_by_id = {fact.id: fact for fact in facts}
        missing = sorted(set(fact_ids) - set(fact_by_id))
        if missing:
            raise ValueError(f"unknown transaction_fact IDs: {missing}")

        fact_totals: dict[int, int] = defaultdict(int)
        grouped = defaultdict(list)
        allocations = []
        for row in payload.allocations:
            fact = fact_by_id[row.transaction_fact_id]
            fact_totals[fact.id] += row.amount_value
            grouped[row.entry_key].append((row, fact))
            allocations.append({
                "transaction_fact_id": fact.id,
                "ledger_key": row.entry_key,
                "amount_value": row.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
            })
        exceeded = {
            fact_id: total
            for fact_id, total in fact_totals.items()
            if total > fact_by_id[fact_id].amount_value
        }
        if exceeded:
            raise ValueError(f"review allocation exceeds fact amounts: {exceeded}")

        ledger_definitions = []
        for key in keys:
            definition = definitions[key]
            rows = grouped[key]
            if len(rows) != 1:
                raise ValueError(
                    f"ledger entry {key} must allocate exactly one transaction_fact; "
                    "do not merge multiple facts into one ledger entry"
                )
            row, fact = rows[0]
            ledger_definitions.append({
                "client_key": key,
                "entry_type": definition.entry_type,
                "direction": fact.cash_direction,
                "amount_value": row.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
                "account_code": fact.account_code,
                "occurred_time": fact.occurred_time,
            })
        return facts, ledger_definitions, allocations

    def _assert_exact(self, facts: tuple[TargetReviewFactVO, ...]) -> None:
        coverage = self.mapper.fact_coverage([fact.id for fact in facts])
        invalid = {
            fact.id: {
                "fact": fact.amount_value,
                "allocated": coverage.get(fact.id, 0),
            }
            for fact in facts
            if coverage.get(fact.id, 0) != fact.amount_value
        }
        if invalid:
            raise TargetEconomicError(409, f"fact coverage invariant failed: {invalid}")

    def _required(self, case_id: int) -> TargetEconomicReviewRead:
        case = self.mapper.detail(case_id)
        if case is None:
            raise TargetEconomicError(404, "economic review case not found")
        return case

    def _replay(self, key: str, operation: int, request_json: str):
        row = self.mapper.idempotency(key)
        if row is None:
            return None
        if row["operation"] != operation or row["request_json"] != request_json:
            raise TargetEconomicError(
                409,
                "idempotency key was already used by another command",
            )
        return self._required(row["review_case_id"])

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
