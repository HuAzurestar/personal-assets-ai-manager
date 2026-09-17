from __future__ import annotations

import json

from sqlalchemy import case, func, literal, select
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    LedgerEntry,
    ReviewCase,
    ReviewRevision,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.mapper.import_file_mapper import ImportFileMapper
from backend.schema.transaction_fact import (
    TransactionFactFilter,
    TransactionFactSorter,
)


ECONOMIC_TYPES = {0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "CLAIM"}
CASH_DIRECTIONS = {1: "IN", 2: "OUT"}


class TransactionFactMapper:
    """Read-only PO queries for Transaction Fact detail surfaces."""

    _SORT_COLUMNS = {
        "occurred_time": TransactionFact.occurred_time,
        "amount_value": TransactionFact.amount_value,
    }

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _fact_columns():
        return (
            TransactionFact.id,
            TransactionFact.fact_key,
            TransactionFact.occurred_time,
            case(
                (TransactionFact.cash_direction == CASH_DIRECTION_IN, "IN"),
                (TransactionFact.cash_direction == CASH_DIRECTION_OUT, "OUT"),
                else_="UNKNOWN",
            ).label("cash_direction"),
            TransactionFact.amount_value,
            TransactionFact.amount_scale,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name.label("counterparty"),
            TransactionFact.summary,
            TransactionFact.created_time,
            TransactionFact.updated_time,
        )

    def page(
        self,
        *,
        page: int,
        page_size: int,
        filter_value: TransactionFactFilter,
        sorter: TransactionFactSorter,
    ) -> tuple[list[dict], int]:
        clauses = []
        if filter_value.id:
            clauses.append(TransactionFact.id == filter_value.id)
        if filter_value.cash_direction:
            clauses.append(TransactionFact.cash_direction == {
                "IN": CASH_DIRECTION_IN,
                "OUT": CASH_DIRECTION_OUT,
            }[filter_value.cash_direction])
        if filter_value.currency_code:
            clauses.append(TransactionFact.currency_code == filter_value.currency_code.upper())
        if filter_value.account_code:
            clauses.append(TransactionFact.account_code == filter_value.account_code)
        if filter_value.amount_scale is not None:
            clauses.append(TransactionFact.amount_scale == filter_value.amount_scale)
        if filter_value.occurred_time_start:
            clauses.append(
                TransactionFact.occurred_time >= filter_value.occurred_time_start
            )
        if filter_value.occurred_time_end:
            clauses.append(
                TransactionFact.occurred_time < filter_value.occurred_time_end
            )
        total = int(self.db.scalar(
            select(func.count(TransactionFact.id)).where(*clauses)
        ) or 0)
        column = self._SORT_COLUMNS[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = TransactionFact.id.asc() if sorter.order == "asc" else TransactionFact.id.desc()
        orders = [order, id_order]
        if (
            sorter.field == "amount_value"
            and not (
                filter_value.currency_code
                and filter_value.amount_scale is not None
            )
        ):
            orders = [
                TransactionFact.currency_code.asc(),
                TransactionFact.amount_scale.asc(),
                order,
                id_order,
            ]
        rows = self.db.execute(select(
            *self._fact_columns(),
        ).where(*clauses).order_by(*orders).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def by_import_file(self, import_file_id: int) -> list[dict]:
        imported_fact_ids = select(TransactionImportRow.transaction_fact_id).where(
            TransactionImportRow.transaction_import_file_id == import_file_id,
            TransactionImportRow.transaction_fact_id > 0,
        ).distinct()
        rows = self.db.execute(select(
            *self._fact_columns(),
        ).where(
            TransactionFact.id.in_(imported_fact_ids),
        ).order_by(
            TransactionFact.occurred_time.desc(),
            TransactionFact.id.desc(),
        )).mappings().all()
        return [dict(row) for row in rows]

    def detail(self, fact_id: int) -> dict | None:
        row = self.db.execute(select(
            *self._fact_columns(),
        ).where(TransactionFact.id == fact_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def import_evidence(self, fact_id: int) -> list[dict]:
        rows = self.db.execute(select(
            TransactionImportRow.id.label("raw_id"),
            TransactionImportRow.transaction_import_file_id.label("import_file_id"),
            TransactionImportRow.source_row_number,
            TransactionImportRow.source_reference,
            case(
                (TransactionImportRow.row_status == 0, "UNKNOWN"),
                (TransactionImportRow.row_status == 1, "SUCCESS"),
                (TransactionImportRow.row_status == 2, "SKIPPED"),
                (TransactionImportRow.row_status == 3, "INVALID"),
                else_="UNKNOWN",
            ).label("parse_status"),
            TransactionImportRow.issue_code,
            TransactionImportFile.filename,
            case(
                *[(TransactionImportFile.source_type == code, name) for name, code in ImportFileMapper._SOURCE_CODES.items()],
                else_="unknown",
            ).label("source_type"),
            literal("").label("institution_code"),
            case(
                *[(TransactionImportFile.file_format == code, name) for name, code in ImportFileMapper._FORMAT_CODES.items()],
                else_="UNKNOWN",
            ).label("file_format"),
            TransactionImportFile.created_time.label("imported_time"),
        ).join(
            TransactionImportFile,
            TransactionImportFile.id == TransactionImportRow.transaction_import_file_id,
        ).where(
            TransactionImportRow.transaction_fact_id == fact_id,
        ).order_by(
            TransactionImportFile.created_time.desc(), TransactionImportRow.id.desc(),
        )).mappings().all()
        return [dict(row) for row in rows]

    def reviews(self, review_ids: list[int]) -> list[dict]:
        if not review_ids:
            return []
        rows = self.db.execute(select(
            ReviewCase.id,
            literal("ECONOMIC").label("review_type"),
            case(
                (ReviewCase.behavior_type == 0, "DEFAULT"),
                (ReviewCase.behavior_type == 1, "BORROW_AND_REPAY"),
                else_="UNKNOWN",
            ).label("behavior_code"),
            case(
                (ReviewCase.status == 0, "CONFIRMED"),
                (ReviewCase.status == 1, "REVOKED"),
                else_="UNKNOWN",
            ).label("status"),
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        ).where(
            ReviewCase.id.in_(review_ids),
        ).order_by(ReviewCase.updated_time.desc(), ReviewCase.id.desc())).mappings().all()
        versions = dict(self.db.execute(select(
            ReviewRevision.review_case_id,
            func.count(ReviewRevision.id),
        ).where(
            ReviewRevision.review_case_id.in_(review_ids),
        ).group_by(ReviewRevision.review_case_id)).all())
        behavior_codes = {}
        revision_rows = self.db.execute(select(
            ReviewRevision.review_case_id,
            ReviewRevision.request_json,
        ).where(
            ReviewRevision.review_case_id.in_(review_ids),
        ).order_by(ReviewRevision.id.desc())).mappings().all()
        for revision in revision_rows:
            payload = json.loads(revision["request_json"] or "{}")
            if (
                revision["review_case_id"] not in behavior_codes
                and payload.get("behavior_code")
            ):
                behavior_codes[revision["review_case_id"]] = payload["behavior_code"]
        return [{
            **dict(row),
            "review_type": behavior_codes.get(row["id"], row["behavior_code"]),
            "behavior_code": behavior_codes.get(row["id"], row["behavior_code"]),
            "version": max(1, int(versions.get(row["id"], 0))),
        } for row in rows]

    def ledgers(self, economic_ids: list[int]) -> list[dict]:
        if not economic_ids:
            return []
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.occurred_time,
        ).where(
            LedgerEntry.id.in_(economic_ids),
        ).order_by(LedgerEntry.occurred_time.desc(), LedgerEntry.id.desc())).mappings().all()
        return [{
            **dict(row),
            "economic_type": ECONOMIC_TYPES[row["entry_type"]],
            "cash_direction": CASH_DIRECTIONS[row["entry_direction"]],
        } for row in rows]
