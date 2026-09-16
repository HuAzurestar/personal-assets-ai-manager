from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_FORMAT_PDF,
    IMPORT_FILE_FORMAT_UNKNOWN,
    IMPORT_FILE_FORMAT_XLS,
    IMPORT_FILE_FORMAT_XLSX,
    IMPORT_FILE_STATUS_FAILED,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PARTIAL,
    IMPORT_SOURCE_ABC_BANK,
    IMPORT_SOURCE_ALIPAY,
    IMPORT_SOURCE_CCB_BANK,
    IMPORT_SOURCE_CMB_BANK,
    IMPORT_SOURCE_MANUAL,
    IMPORT_SOURCE_UNKNOWN,
    IMPORT_SOURCE_WECHAT,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.parser.statement_parser import digest
from backend.smart_import import dump


_IMPORT_SOURCE_BY_NAME = {
    "unknown": IMPORT_SOURCE_UNKNOWN,
    "manual": IMPORT_SOURCE_MANUAL,
    "alipay": IMPORT_SOURCE_ALIPAY,
    "wechat": IMPORT_SOURCE_WECHAT,
    "ccb": IMPORT_SOURCE_CCB_BANK,
    "abc": IMPORT_SOURCE_ABC_BANK,
    "cmb": IMPORT_SOURCE_CMB_BANK,
}
_IMPORT_FILE_FORMAT_BY_NAME = {
    "unknown": IMPORT_FILE_FORMAT_UNKNOWN,
    "csv": IMPORT_FILE_FORMAT_CSV,
    "xls": IMPORT_FILE_FORMAT_XLS,
    "xlsx": IMPORT_FILE_FORMAT_XLSX,
    "pdf": IMPORT_FILE_FORMAT_PDF,
}


def _import_source_code(name: str) -> int:
    try:
        return _IMPORT_SOURCE_BY_NAME[name.casefold()]
    except KeyError as error:
        raise ValueError(f"unknown import source: {name}") from error


def _import_file_format_code(name: str) -> int:
    try:
        return _IMPORT_FILE_FORMAT_BY_NAME[name.casefold()]
    except KeyError as error:
        raise ValueError(f"unknown import content format: {name}") from error


class TargetIntakeMapper:
    """Set-oriented persistence for the PIRC-9 Fact-layer import path."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def commit_plan(
        self,
        plan: dict[str, object],
        batch_code: str,
    ) -> dict[str, object]:
        documents = [doc for doc in plan["documents"] if not doc.get("duplicate")]
        now = datetime.now()
        new_rows = [
            row
            for doc in documents
            for row in doc["rows"]
            if row["action"] == "new"
        ]
        facts = []
        for row in new_rows:
            amount_minor = int(row["amount_minor"])
            keys = row.get("keys", [])
            fact_key = keys[0] if keys else digest([
                "fact",
                row["source_type"],
                row["account"]["identity"],
                row["occurred_at"],
                amount_minor,
                row["currency"],
                row["raw"],
            ])
            fact = TransactionFact(
                fact_key=fact_key,
                occurred_time=datetime.fromisoformat(row["occurred_at"]),
                cash_direction=(
                    CASH_DIRECTION_IN if amount_minor > 0 else CASH_DIRECTION_OUT
                ),
                amount_value=abs(amount_minor),
                amount_scale=2,
                currency_code=row["currency"],
                account_code=row["account"]["identity"] or "UNKNOWN",
                counterparty_name=row["merchant"],
                counterparty_account_ref="",
                summary=row["note"],
                created_time=now,
                updated_time=now,
            )
            self.db.add(fact)
            facts.append((row, fact))
        self.db.flush()
        new_targets = {row["match"]: fact.id for row, fact in facts}

        import_files = []
        for doc in documents:
            dated = sorted(
                row["occurred_at"]
                for row in doc["rows"]
                if row.get("occurred_at") and not row.get("error")
            )
            success_count = sum(
                row["action"] in {"new", "supplement"} for row in doc["rows"]
            )
            skip_count = sum(row["action"] == "record" for row in doc["rows"])
            issue_count = sum(row["action"] == "error" for row in doc["rows"])
            status = (
                IMPORT_FILE_STATUS_FAILED
                if issue_count and not success_count
                else IMPORT_FILE_STATUS_PARTIAL
                if issue_count
                else IMPORT_FILE_STATUS_IMPORTED
            )
            item = TransactionImportFile(
                batch_code=batch_code,
                source_type=_import_source_code(doc["source_type"]),
                filename=doc["filename"],
                file_format=_import_file_format_code(doc["format"]),
                sha256=doc["sha256"],
                period_start=dated[0] if dated else "",
                period_end=dated[-1] if dated else "",
                total_count=len(doc["rows"]),
                success_count=success_count,
                skip_count=skip_count,
                issue_count=issue_count,
                status=status,
                created_time=now,
                updated_time=now,
            )
            self.db.add(item)
            import_files.append((doc, item))
        self.db.flush()

        for doc, import_file in import_files:
            for row in doc["rows"]:
                target = row.get("match")
                transaction_fact_id = (
                    new_targets.get(target, target) if target is not None else 0
                )
                transaction_fact_id = (
                    transaction_fact_id if isinstance(transaction_fact_id, int) else 0
                )
                action = row["action"]
                row_status = (
                    IMPORT_ROW_STATUS_ACCEPTED
                    if action in {"new", "supplement"}
                    else IMPORT_ROW_STATUS_SKIPPED
                    if action == "record"
                    else IMPORT_ROW_STATUS_INVALID
                )
                raw = row.get("raw", {})
                envelope = {
                    "raw": raw,
                    "normalized": {
                        key: value
                        for key, value in row.items()
                        if key not in {"raw", "candidates", "error"}
                    },
                }
                import_row = TransactionImportRow(
                    transaction_fact_id=transaction_fact_id,
                    transaction_import_file_id=import_file.id,
                    source_row_number=row["row_number"],
                    source_reference=row.get("reference", ""),
                    raw_payload=dump(envelope),
                    raw_hash=digest(raw),
                    row_status=row_status,
                    issue_code=(
                        "FACT_CONFLICT"
                        if action == "error" and row.get("keys")
                        else "PARSE_ERROR"
                        if action == "error"
                        else ""
                    ),
                    issue_message=row.get("error", ""),
                    created_time=now,
                    updated_time=now,
                )
                self.db.add(import_row)
        self.db.flush()
        affected_fact_ids = sorted({
            new_targets.get(row.get("match"), row.get("match"))
            for doc in documents
            for row in doc["rows"]
            if row["action"] in {"new", "supplement"}
        })
        return {
            "counts": plan["counts"],
            "transaction_import_file_ids": [
                item.id for _doc, item in import_files
            ],
            "transaction_fact_ids": sorted(new_targets.values()),
            "affected_fact_ids": affected_fact_ids,
        }

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
