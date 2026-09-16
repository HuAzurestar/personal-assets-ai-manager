from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    IMPORT_FILE_STATUS_FAILED,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PARTIAL,
    IMPORT_FILE_STATUS_PENDING,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
    IMPORT_ROW_STATUS_UNKNOWN,
    IMPORT_SOURCE_ABC_BANK,
    IMPORT_SOURCE_ALIPAY,
    IMPORT_SOURCE_CCB_BANK,
    IMPORT_SOURCE_CMB_BANK,
    IMPORT_SOURCE_MANUAL,
    IMPORT_SOURCE_UNKNOWN,
    IMPORT_SOURCE_WECHAT,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)


_IMPORT_SOURCE_NAME_BY_CODE = {
    IMPORT_SOURCE_UNKNOWN: "unknown",
    IMPORT_SOURCE_MANUAL: "manual",
    IMPORT_SOURCE_ALIPAY: "alipay",
    IMPORT_SOURCE_WECHAT: "wechat",
    IMPORT_SOURCE_CCB_BANK: "ccb",
    IMPORT_SOURCE_ABC_BANK: "abc",
    IMPORT_SOURCE_CMB_BANK: "cmb",
}
_IMPORT_SOURCE_SEARCH_TERMS = {
    IMPORT_SOURCE_MANUAL: ("manual", "手工"),
    IMPORT_SOURCE_ALIPAY: ("alipay", "支付宝"),
    IMPORT_SOURCE_WECHAT: ("wechat", "微信", "微信支付"),
    IMPORT_SOURCE_CCB_BANK: ("ccb", "建设银行", "建行"),
    IMPORT_SOURCE_ABC_BANK: ("abc", "农业银行", "农行"),
    IMPORT_SOURCE_CMB_BANK: ("cmb", "招商银行", "招行"),
}
_IMPORT_FILE_STATUS_NAME_BY_CODE = {
    IMPORT_FILE_STATUS_PENDING: "PENDING",
    IMPORT_FILE_STATUS_IMPORTED: "IMPORTED",
    IMPORT_FILE_STATUS_PARTIAL: "PARTIAL",
    IMPORT_FILE_STATUS_FAILED: "FAILED",
}
_IMPORT_ROW_STATUS_NAME_BY_CODE = {
    IMPORT_ROW_STATUS_UNKNOWN: "UNKNOWN",
    IMPORT_ROW_STATUS_ACCEPTED: "ACCEPTED",
    IMPORT_ROW_STATUS_SKIPPED: "SKIPPED",
    IMPORT_ROW_STATUS_INVALID: "INVALID",
}


def _import_source_name(code: int) -> str:
    try:
        return _IMPORT_SOURCE_NAME_BY_CODE[code]
    except KeyError as error:
        raise ValueError(f"unknown persisted import source: {code}") from error


def _import_file_status_name(code: int) -> str:
    try:
        return _IMPORT_FILE_STATUS_NAME_BY_CODE[code]
    except KeyError as error:
        raise ValueError(f"unknown persisted import file status: {code}") from error


def _import_row_status_name(code: int) -> str:
    try:
        return _IMPORT_ROW_STATUS_NAME_BY_CODE[code]
    except KeyError as error:
        raise ValueError(f"unknown persisted import row status: {code}") from error


class TargetImportReadMapper:
    """Explicit-column reads for imported files, rows, and account summaries."""

    def __init__(self, db: Session):
        self.db = db

    def known_accounts(self) -> dict[int, dict[str, object]]:
        ranked = self._ranked_accounts()
        rows = self.db.execute(select(
            ranked.c.account_code,
            ranked.c.raw_payload,
        ).where(
            ranked.c.position == 1,
        ).order_by(ranked.c.account_code)).mappings().all()
        items = self._account_items(rows)
        return {item["id"]: item for item in items}

    def history(
        self,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        account_code: str = "",
    ) -> dict[str, object]:
        clauses = []
        if q:
            pattern = f"%{q}%"
            search = [TransactionImportFile.filename.ilike(pattern)]
            query_text = q.casefold()
            source_codes = [
                code
                for code, terms in _IMPORT_SOURCE_SEARCH_TERMS.items()
                if any(query_text in term.casefold() for term in terms)
            ]
            if source_codes:
                search.append(TransactionImportFile.source_type.in_(source_codes))
            numeric_query = q.removeprefix("#")
            if numeric_query.isdigit():
                search.append(TransactionImportFile.id == int(numeric_query))
            clauses.append(or_(*search))
        if account_code:
            matching_files = select(
                TransactionImportRow.transaction_import_file_id
            ).join(
                TransactionFact,
                TransactionImportRow.transaction_fact_id == TransactionFact.id,
            ).where(
                TransactionFact.account_code == account_code,
            ).distinct()
            clauses.append(TransactionImportFile.id.in_(matching_files))

        summary = self.db.execute(select(
            func.count(TransactionImportFile.id).label("batch_count"),
            func.coalesce(func.sum(case(
                (TransactionImportFile.status == IMPORT_FILE_STATUS_IMPORTED, 1),
                else_=0,
            )), 0).label("complete_count"),
            func.coalesce(func.sum(TransactionImportFile.success_count), 0).label(
                "imported_count"
            ),
        ).where(*clauses)).mappings().one()
        total = int(summary["batch_count"])
        rows = self.db.execute(select(
            TransactionImportFile.id,
            TransactionImportFile.filename,
            TransactionImportFile.source_type,
            TransactionImportFile.total_count,
            TransactionImportFile.success_count,
            TransactionImportFile.status,
            TransactionImportFile.created_time,
        ).where(*clauses).order_by(TransactionImportFile.id.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        file_ids = [row["id"] for row in rows]
        account_codes: dict[int, list[str]] = defaultdict(list)
        if file_ids:
            links = self.db.execute(select(
                TransactionImportRow.transaction_import_file_id,
                TransactionFact.account_code,
            ).join(
                TransactionFact,
                TransactionImportRow.transaction_fact_id == TransactionFact.id,
            ).where(
                TransactionImportRow.transaction_import_file_id.in_(file_ids),
            ).distinct().order_by(
                TransactionImportRow.transaction_import_file_id,
                TransactionFact.account_code,
            )).mappings().all()
            for link in links:
                account_codes[link["transaction_import_file_id"]].append(
                    link["account_code"]
                )
        items = [{
            "id": row["id"],
            "filename": row["filename"],
            "source_type": _import_source_name(row["source_type"]),
            "account_codes": account_codes[row["id"]],
            "row_count": row["total_count"],
            "imported_count": row["success_count"],
            "status": _import_file_status_name(row["status"]),
            "imported_at": row["created_time"].isoformat(),
        } for row in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "summary": {
                "batch_count": total,
                "complete_count": int(summary["complete_count"]),
                "imported_count": int(summary["imported_count"]),
            },
            "filters": {"q": q, "account_code": account_code},
        }

    def rows(
        self,
        transaction_import_file_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        batch = self.db.execute(select(
            TransactionImportFile.total_count,
            TransactionImportFile.success_count,
            TransactionImportFile.skip_count,
            TransactionImportFile.issue_count,
        ).where(
            TransactionImportFile.id == transaction_import_file_id
        )).mappings().one_or_none()
        if batch is None:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "summary": {"success": 0, "skipped": 0, "invalid": 0},
            }
        rows = self.db.execute(select(
            TransactionImportRow.id,
            TransactionImportRow.transaction_fact_id,
            TransactionImportRow.row_status,
            TransactionImportRow.raw_payload,
        ).where(
            TransactionImportRow.transaction_import_file_id
            == transaction_import_file_id
        ).order_by(
            TransactionImportRow.source_row_number,
        ).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        items = [{
            "id": row["id"],
            "transaction_fact_id": row["transaction_fact_id"],
            "disposition": _import_row_status_name(row["row_status"]),
            "record": json.loads(row["raw_payload"]) if row["raw_payload"] else None,
        } for row in rows]
        return {
            "items": items,
            "total": int(batch["total_count"]),
            "page": page,
            "page_size": page_size,
            "summary": {
                "success": int(batch["success_count"]),
                "skipped": int(batch["skip_count"]),
                "invalid": int(batch["issue_count"]),
            },
        }

    def accounts(self, page: int, page_size: int) -> dict[str, object]:
        ranked = self._ranked_accounts()
        condition = ranked.c.position == 1
        total = self.db.scalar(select(func.count()).select_from(ranked).where(
            condition,
        )) or 0
        offset = (page - 1) * page_size
        rows = self.db.execute(select(
            ranked.c.account_code,
            ranked.c.raw_payload,
        ).where(condition).order_by(ranked.c.account_code).offset(
            offset,
        ).limit(page_size)).mappings().all()
        return {
            "items": self._account_items(rows, offset + 1),
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def _ranked_accounts():
        return select(
            TransactionFact.account_code.label("account_code"),
            TransactionImportRow.raw_payload.label("raw_payload"),
            func.row_number().over(
                partition_by=TransactionFact.account_code,
                order_by=TransactionImportRow.id.desc(),
            ).label("position"),
        ).outerjoin(
            TransactionImportRow,
            TransactionImportRow.transaction_fact_id == TransactionFact.id,
        ).subquery()

    @staticmethod
    def _account_items(rows, start_id: int = 1) -> list[dict[str, object]]:
        accounts: list[dict[str, object]] = []
        for position, row in enumerate(rows, start_id):
            account = None
            if row["raw_payload"]:
                try:
                    envelope = json.loads(row["raw_payload"])
                    normalized = envelope.get("normalized", {})
                    account = normalized.get("account")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    account = None
            if not isinstance(account, dict):
                account = {
                    "identity": row["account_code"],
                    "provider": "UNKNOWN",
                    "display_name": row["account_code"],
                    "number": "",
                    "owner": "",
                }
            accounts.append({"id": position, **account})
        return accounts
