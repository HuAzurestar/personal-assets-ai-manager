from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.mapper.target_economic_mapper import TargetEconomicMapper
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewListItem,
    TargetEconomicReviewPageRead,
    TargetEconomicReviewRead,
    TargetEconomicReviewUpdateRequest,
    TargetFactAllocationCandidateRead,
    TargetReviewFactVO,
    TargetReviewTransitionRequest,
)
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetEconomicError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class TargetEconomicService:
    """Strict Fact -> Review -> Economic allocation use cases."""

    def __init__(self, db: Session):
        self.mapper = TargetEconomicMapper(db)
        self.tags = TargetTagProjectionService(db)

    def ensure_defaults(self, fact_ids: list[int], *, commit: bool = False) -> None:
        """Give every accepted fact exact confirmed TRANSACTION coverage."""

        fact_ids = sorted(set(fact_ids))
        if not fact_ids:
            return
        facts = self.mapper.facts(fact_ids)
        if len(facts) != len(fact_ids):
            missing = sorted(set(fact_ids) - {fact.id for fact in facts})
            raise TargetEconomicError(409, f"unknown bill_fact IDs: {missing}")
        coverage = self.mapper.fact_coverage(fact_ids)
        legacy = self.mapper.legacy_ledger_ids(fact_ids)
        now = datetime.now()
        defaults = []
        for fact in facts:
            allocated = coverage.get(fact.id, 0)
            if allocated > fact.amount_value:
                raise TargetEconomicError(409, f"fact {fact.id} is over-allocated")
            if allocated < fact.amount_value:
                defaults.append((
                    fact,
                    fact.amount_value - allocated,
                    legacy.get(fact.id, 0) if allocated == 0 else 0,
                ))
        self.mapper.create_defaults(defaults, now)
        self._assert_exact(facts)
        self.tags.sync_economics(self.mapper.active_economic_facts(fact_ids))
        if commit:
            self.mapper.commit()

    def backfill_defaults(self) -> None:
        """Advance facts from an older database into the strict v2 invariant."""

        try:
            self.mapper.begin_write()
            fact_ids = self.mapper.all_fact_ids()
            if not fact_ids:
                self.mapper.commit()
                return
            self.ensure_defaults(fact_ids)
            self.mapper.commit()
        except Exception:
            self.mapper.rollback()
            raise

    def page(self, page: int, page_size: int, status: str = "") -> TargetEconomicReviewPageRead:
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

    def create(self, payload: TargetEconomicReviewCreateRequest) -> TargetEconomicReviewRead:
        request_json = self._canonical({"operation": "CREATE", **payload.model_dump(mode="json")})
        try:
            self.mapper.begin_write()
            replay = self._replay(payload.idempotency_key, "CREATE", request_json)
            if replay is not None:
                self.mapper.commit()
                return replay
            facts, economics, allocations = self._prepare(payload)
            self.ensure_defaults([fact.id for fact in facts])
            now = datetime.now()
            case_id = self.mapper.create_draft(
                behavior_code=payload.behavior_code,
                title=payload.title,
                result_json=self._canonical(payload.result),
                economics=economics,
                allocations=allocations,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=now,
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
            case = self._required(case_id)
            expected_status = "REVOKED" if restore else "PENDING"
            if case.status != expected_status:
                raise TargetEconomicError(409, f"case status is {case.status}; expected {expected_status}")
            if case.version != payload.expected_version:
                raise TargetEconomicError(409, "review version changed; reload before confirming")
            self._validate_reversals([
                {
                    "id": item.id,
                    "economic_type": item.economic_type,
                    "direction": item.cash_direction,
                    "amount_value": item.amount_value,
                    "amount_scale": item.amount_scale,
                    "currency_code": item.currency_code,
                    "reversal_of_id": item.reversal_of_id,
                }
                for item in case.economics
            ])
            fact_ids = sorted({row.fact_id for row in case.allocations})
            facts = self.mapper.facts(fact_ids)
            fact_by_id = {fact.id: fact for fact in facts}
            defaults = self.mapper.default_allocations(fact_ids)
            default_by_fact: dict[int, int] = defaultdict(int)
            for row in defaults:
                default_by_fact[row["bill_id"]] += row["amount_value"]
            requested: dict[int, int] = defaultdict(int)
            for row in case.allocations:
                requested[row.fact_id] += row.amount_value
            unavailable = {
                fact_id: {"requested": amount, "default_available": default_by_fact.get(fact_id, 0)}
                for fact_id, amount in requested.items()
                if amount > default_by_fact.get(fact_id, 0)
            }
            if unavailable:
                raise TargetEconomicError(409, f"allocations are no longer available: {unavailable}")
            residuals = [
                (fact_by_id[fact_id], default_by_fact[fact_id] - amount)
                for fact_id, amount in requested.items()
                if default_by_fact[fact_id] > amount
            ]
            if not self.mapper.activate_case(
                case_id,
                payload.expected_version,
                defaults,
                residuals,
                operation=operation,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review version changed; reload before confirming")
            self._assert_exact(facts)
            self.tags.sync_economics(self.mapper.active_economic_facts(fact_ids))
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
            current = self._required(case_id)
            if current.status != "PENDING":
                raise TargetEconomicError(409, "only a pending economic review can be edited")
            if current.version != payload.expected_version:
                raise TargetEconomicError(409, "review version changed; reload before editing")
            facts, economics, allocations = self._prepare(payload)
            self.ensure_defaults([fact.id for fact in facts])
            if not self.mapper.replace_draft(
                case_id,
                expected_version=payload.expected_version,
                behavior_code=payload.behavior_code,
                title=payload.title,
                result_json=self._canonical(payload.result),
                economics=economics,
                allocations=allocations,
                request_json=request_json,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                now=datetime.now(),
            ):
                raise TargetEconomicError(409, "review version changed; reload before editing")
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

    def revoke(self, case_id: int, payload: TargetReviewTransitionRequest) -> TargetEconomicReviewRead:
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
            if case.behavior_code == "DEFAULT":
                raise TargetEconomicError(409, "default coverage cannot be revoked directly")
            if case.status != "CONFIRMED":
                raise TargetEconomicError(409, f"case status is {case.status}; expected CONFIRMED")
            if case.version != payload.expected_version:
                raise TargetEconomicError(409, "review version changed; reload before revoking")
            released: dict[int, int] = defaultdict(int)
            for row in case.allocations:
                released[row.fact_id] += row.amount_value
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
            self.tags.sync_economics(self.mapper.active_economic_facts(sorted(released)))
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
            raise ValueError(f"unknown bill_fact IDs: {missing}")
        fact_totals: dict[int, int] = defaultdict(int)
        grouped = defaultdict(list)
        allocations = []
        for row in payload.allocations:
            fact = fact_by_id[row.fact_id]
            fact_totals[fact.id] += row.amount_value
            grouped[row.economic_key].append((row, fact))
            allocations.append({
                "fact_id": fact.id,
                "economic_key": row.economic_key,
                "amount_value": row.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
                "role": row.role,
            })
        exceeded = {
            fact_id: total for fact_id, total in fact_totals.items()
            if total > fact_by_id[fact_id].amount_value
        }
        if exceeded:
            raise ValueError(f"review allocation exceeds fact amounts: {exceeded}")
        economics = []
        for key in keys:
            definition = definitions[key]
            rows = grouped[key]
            directions = {fact.cash_direction for _, fact in rows}
            currencies = {fact.currency_code for _, fact in rows}
            if len(directions) != 1 or len(currencies) != 1:
                raise ValueError(f"economic {key} cannot mix fact direction or currency")
            if definition.economic_type == "CLAIM":
                if not definition.claim_key or definition.claim_side == "UNKNOWN":
                    raise ValueError(f"CLAIM economic {key} requires claim_key and claim_side")
            elif definition.claim_key or definition.claim_side != "UNKNOWN":
                raise ValueError(f"non-CLAIM economic {key} cannot carry claim state")
            scale = max(fact.amount_scale for _, fact in rows)
            amount = sum(
                row.amount_value * (10 ** (scale - fact.amount_scale))
                for row, fact in rows
            )
            contributing = [fact for _, fact in rows]
            accounts = {fact.account_code for fact in contributing}
            economics.append({
                "client_key": key,
                "economic_type": definition.economic_type,
                "direction": next(iter(directions)),
                "amount_value": amount,
                "amount_scale": scale,
                "currency_code": next(iter(currencies)),
                "title": definition.title or payload.title or contributing[0].counterparty or contributing[0].summary,
                "start_time": min(fact.occurred_time for fact in contributing),
                "end_time": max(fact.occurred_time for fact in contributing),
                "account_code": next(iter(accounts)) if len(accounts) == 1 else "MULTIPLE",
                "claim_key": definition.claim_key,
                "claim_side": definition.claim_side,
                "reversal_of_id": definition.reversal_of_id,
            })
        self._validate_reversals(economics)
        return facts, economics, allocations

    def _validate_reversals(self, economics: list[dict]) -> None:
        reversing = [item for item in economics if item.get("reversal_of_id", 0)]
        if not reversing:
            return
        target_ids = sorted({item["reversal_of_id"] for item in reversing})
        targets = self.mapper.economic_flows(target_ids)
        missing = sorted(set(target_ids) - set(targets))
        if missing:
            raise ValueError(f"unknown reversal economic IDs: {missing}")
        usage = self.mapper.reversal_usage(
            target_ids,
            exclude_ids=[item["id"] for item in reversing if item.get("id")],
        )
        requested = defaultdict(list)
        for item in reversing:
            target = targets[item["reversal_of_id"]]
            if item["economic_type"] != "TRANSACTION" or target["economic_type"] != "TRANSACTION":
                raise ValueError("only TRANSACTION economics can form a reversal")
            if target["reversal_of_id"]:
                raise ValueError("a reversal cannot reverse another reversal")
            if item["currency_code"] != target["currency_code"]:
                raise ValueError("reversal currency must equal the original transaction")
            if item["direction"] == target["cash_direction"]:
                raise ValueError("reversal direction must oppose the original transaction")
            requested[target["id"]].append((item["amount_value"], item["amount_scale"]))
        for target_id, values in requested.items():
            target = targets[target_id]
            all_values = list(values)
            if target_id in usage:
                all_values.append(usage[target_id])
            scale = max([target["amount_scale"], *[value[1] for value in all_values]])
            total = sum(value * 10 ** (scale - item_scale) for value, item_scale in all_values)
            maximum = target["amount_value"] * 10 ** (scale - target["amount_scale"])
            if total > maximum:
                raise ValueError(f"reversals exceed original economic {target_id} amount")

    def _assert_exact(self, facts: tuple[TargetReviewFactVO, ...]) -> None:
        coverage = self.mapper.fact_coverage([fact.id for fact in facts])
        invalid = {
            fact.id: {"fact": fact.amount_value, "allocated": coverage.get(fact.id, 0)}
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

    def _replay(self, key: str, operation: str, request_json: str):
        row = self.mapper.idempotency(key)
        if row is None:
            return None
        if row["operation"] != operation or row["request_json"] != request_json:
            raise TargetEconomicError(409, "idempotency key was already used by another command")
        return self._required(row["case_id"])

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
