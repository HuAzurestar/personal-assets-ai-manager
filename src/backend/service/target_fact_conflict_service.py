from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
)
from backend.error import TargetReviewError
from backend.mapper.import_fact_conflict_mapper import (
    CONFLICT_STATUS_CODES,
    ImportFactConflictMapper,
)
from backend.schema.list_query import BetweenValue, iter_filter_fields
from backend.schema.target_review import (
    TargetFactConflictFilter,
    TargetFactConflictListBody,
    TargetFactConflictListRequest,
    TargetFactConflictResolveRequest,
    TargetFactConflictSorter,
    TargetReviewCaseRead,
    TargetReviewTransitionRequest,
    parse_review_time,
)
from backend.service.target_economic_read_service import TargetEconomicReadService
from backend.service.target_economic_service import TargetEconomicService


class TargetFactConflictService:
    """Manage import conflicts directly on their DB-owned import-row PO."""

    def __init__(self, db: Session):
        self.mapper = ImportFactConflictMapper(db)
        self.economic = TargetEconomicService(db)

    def page(
        self,
        *,
        request: TargetFactConflictListRequest,
    ) -> TargetFactConflictListBody:
        filter_value = self._mapper_filter(request)
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = TargetFactConflictSorter(
            field=sorter_expression.key if sorter_expression else "updated_time",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        items = [self._read(row) for row in self.mapper.rows()]
        if filter_value.id:
            items = [item for item in items if item.id == filter_value.id]
        if filter_value.status:
            items = [item for item in items if item.status == filter_value.status]
        if filter_value.created_time_start:
            items = [
                item for item in items
                if item.created_time >= filter_value.created_time_start
            ]
        if filter_value.created_time_end:
            items = [
                item for item in items
                if item.created_time < filter_value.created_time_end
            ]
        if filter_value.updated_time_start:
            items = [
                item for item in items
                if item.updated_time >= filter_value.updated_time_start
            ]
        if filter_value.updated_time_end:
            items = [
                item for item in items
                if item.updated_time < filter_value.updated_time_end
            ]
        reverse = sorter.order == "desc"
        items.sort(
            key=lambda item: (getattr(item, sorter.field), item.id),
            reverse=reverse,
        )
        total = len(items)
        offset = (request.page_index - 1) * request.page_size
        return TargetFactConflictListBody(
            items=items[offset:offset + request.page_size],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    @staticmethod
    def _mapper_filter(request: TargetFactConflictListRequest) -> TargetFactConflictFilter:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key in {"created_time", "updated_time"}:
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values[f"{expression.key}_start"] = parse_review_time(
                        between.start,
                        expression.key,
                    )
                    values[f"{expression.key}_end"] = parse_review_time(
                        between.end,
                        expression.key,
                    )
                elif expression.op == ">=":
                    values[f"{expression.key}_start"] = parse_review_time(
                        expression.val,
                        expression.key,
                    )
                else:
                    values[f"{expression.key}_end"] = parse_review_time(
                        expression.val,
                        expression.key,
                    )
            else:
                values[expression.key] = expression.val
        return TargetFactConflictFilter.model_validate(values)

    def detail(self, conflict_id: int) -> TargetReviewCaseRead:
        row = self.mapper.row(conflict_id)
        if row is None:
            raise TargetReviewError(404, "Fact conflict not found")
        return self._read(row)

    def resolve(
        self,
        conflict_id: int,
        payload: TargetFactConflictResolveRequest,
    ) -> TargetReviewCaseRead:
        try:
            self.mapper.begin_write()
            row = self._current(conflict_id, payload.expected_version, "PENDING")
            if payload.resolution_type == "LINK_EXISTING":
                if payload.existing_bill_id <= 0:
                    raise TargetReviewError(422, "LINK_EXISTING requires existing_bill_id")
                fact = self.mapper.fact(payload.existing_bill_id)
                if fact is None:
                    raise TargetReviewError(404, "transaction fact not found")
                fact_id = fact.id
            else:
                if payload.existing_bill_id:
                    raise TargetReviewError(422, "CREATE_NEW does not accept existing_bill_id")
                fact_id = self._create_fact(row)
            if not self.mapper.update_status(
                conflict_id,
                previous_updated_time=row["updated_time"],
                row_status=IMPORT_ROW_STATUS_ACCEPTED,
                transaction_fact_id=fact_id,
                now=datetime.now(),
            ):
                raise TargetReviewError(409, "conflict changed; reload before writing")
            self.economic.ensure_defaults([fact_id])
            self.mapper.commit()
            return self.detail(conflict_id)
        except TargetReviewError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetReviewError(409, "conflict resolution write conflict") from error
        except Exception:
            self.mapper.rollback()
            raise

    def dismiss(
        self,
        conflict_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetReviewCaseRead:
        return self._transition(
            conflict_id, payload, "PENDING", IMPORT_ROW_STATUS_SKIPPED
        )

    def reopen(
        self,
        conflict_id: int,
        payload: TargetReviewTransitionRequest,
    ) -> TargetReviewCaseRead:
        return self._transition(
            conflict_id, payload, "REJECTED", IMPORT_ROW_STATUS_INVALID
        )

    def _transition(self, conflict_id, payload, expected_status, row_status):
        try:
            self.mapper.begin_write()
            row = self._current(conflict_id, payload.expected_version, expected_status)
            if not self.mapper.update_status(
                conflict_id,
                previous_updated_time=row["updated_time"],
                row_status=row_status,
                transaction_fact_id=0,
                now=datetime.now(),
            ):
                raise TargetReviewError(409, "conflict changed; reload before writing")
            self.mapper.commit()
            return self.detail(conflict_id)
        except TargetReviewError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetReviewError(409, "conflict state write conflict") from error
        except Exception:
            self.mapper.rollback()
            raise

    def _current(self, conflict_id: int, expected_version: int, status: str) -> dict:
        row = self.mapper.row(conflict_id)
        if row is None:
            raise TargetReviewError(404, "Fact conflict not found")
        current = self._read(row)
        if current.status != status:
            raise TargetReviewError(409, f"conflict status is {current.status}; expected {status}")
        if current.version != expected_version:
            raise TargetReviewError(409, "conflict changed; reload before writing")
        return row

    def _create_fact(self, row: dict) -> int:
        envelope = json.loads(row["raw_payload"] or "{}")
        normalized = envelope["normalized"]
        amount = int(normalized["amount_minor"])
        if not amount:
            raise TargetReviewError(422, "conflict amount cannot be zero")
        account = normalized.get("account", {})
        account_code = (
            account.get("identity", "UNKNOWN")
            if isinstance(account, dict)
            else "UNKNOWN"
        )
        return self.mapper.create_fact({
            "fact_key": hashlib.sha256(
                f"resolved-conflict:{row['id']}:{row['raw_hash']}".encode()
            ).hexdigest(),
            "occurred_time": datetime.fromisoformat(normalized["occurred_at"]),
            "cash_direction": CASH_DIRECTION_IN if amount > 0 else CASH_DIRECTION_OUT,
            "amount_value": abs(amount),
            "amount_scale": 2,
            "currency_code": normalized.get("currency", "CNY"),
            "account_code": account_code or "UNKNOWN",
            "counterparty_name": normalized.get("merchant", ""),
            "counterparty_account_ref": "",
            "summary": normalized.get("note", ""),
        }, datetime.now())

    @staticmethod
    def _read(row: dict) -> TargetReviewCaseRead:
        status = {
            IMPORT_ROW_STATUS_INVALID: "PENDING",
            IMPORT_ROW_STATUS_SKIPPED: "REJECTED",
            IMPORT_ROW_STATUS_ACCEPTED: "CONFIRMED",
        }.get(row["row_status"], "PENDING")
        return TargetReviewCaseRead(
            id=row["id"],
            review_type="FACT_CONFLICT",
            status=status,
            allocation_status="CONFLICT" if status == "PENDING" else "COMPLETE",
            version=TargetEconomicReadService.projection_version(row["updated_time"]),
            title=row["issue_message"] or "Fact conflict",
            result={
                "transaction_import_row_id": row["id"],
                "transaction_fact_id": row["transaction_fact_id"],
                "issue_code": row["issue_code"],
                "issue_message": row["issue_message"],
            },
            lines=[],
            history=[],
            created_time=row["created_time"],
            updated_time=row["updated_time"],
        )
