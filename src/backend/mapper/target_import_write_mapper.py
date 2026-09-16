from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text, update
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
    IMPORT_FILE_STATUS_PENDING,
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


class TargetImportWriteMapper:
    """Batch-write one confirmed import plan inside a caller-owned transaction."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def prepare_files(
        self,
        batch_code: str,
        uploads: list[dict[str, str]],
    ) -> dict[str, dict[str, int]]:
        """Create or reset source-file records before parsing starts."""

        upload_by_hash: dict[str, dict[str, str]] = {}
        for upload in uploads:
            upload_by_hash.setdefault(upload["sha256"], upload)
        if not upload_by_hash:
            return {}

        rows = self.db.execute(select(
            TransactionImportFile.id,
            TransactionImportFile.sha256,
            TransactionImportFile.status,
        ).where(
            TransactionImportFile.sha256.in_(upload_by_hash)
        )).mappings().all()
        result = {
            row["sha256"]: {"id": row["id"], "status": row["status"]}
            for row in rows
        }
        now = datetime.now()
        reset = []
        for sha256, record in result.items():
            if record["status"] not in {
                IMPORT_FILE_STATUS_PENDING,
                IMPORT_FILE_STATUS_FAILED,
            }:
                continue
            upload = upload_by_hash[sha256]
            reset.append({
                "id": record["id"],
                "batch_code": batch_code,
                "source_type": IMPORT_SOURCE_UNKNOWN,
                "filename": upload["filename"],
                "file_format": IMPORT_FILE_FORMAT_UNKNOWN,
                "period_start": "",
                "period_end": "",
                "total_count": 0,
                "success_count": 0,
                "skip_count": 0,
                "issue_count": 0,
                "status": IMPORT_FILE_STATUS_PENDING,
                "updated_time": now,
            })
            record["status"] = IMPORT_FILE_STATUS_PENDING
        if reset:
            self.db.execute(update(TransactionImportFile), reset)

        pending = []
        for sha256, upload in upload_by_hash.items():
            if sha256 in result:
                continue
            item = TransactionImportFile(
                batch_code=batch_code,
                source_type=IMPORT_SOURCE_UNKNOWN,
                filename=upload["filename"],
                file_format=IMPORT_FILE_FORMAT_UNKNOWN,
                sha256=sha256,
                status=IMPORT_FILE_STATUS_PENDING,
                created_time=now,
                updated_time=now,
            )
            self.db.add(item)
            pending.append((sha256, item))
        self.db.flush()
        for sha256, item in pending:
            result[sha256] = {
                "id": item.id,
                "status": IMPORT_FILE_STATUS_PENDING,
            }
        return result

    def update_preview_files(
        self,
        batch_code: str,
        documents: list[dict[str, object]],
    ) -> None:
        """Persist parse metadata while successful files remain PENDING."""

        now = datetime.now()
        mappings: dict[int, dict[str, object]] = {}
        for document in documents:
            file_id = document.get("transaction_import_file_id")
            if not isinstance(file_id, int):
                continue
            failed = bool(document.get("error"))
            mapping = {
                "id": file_id,
                "batch_code": batch_code,
                "source_type": (
                    IMPORT_SOURCE_UNKNOWN
                    if failed
                    else _import_source_code(document["source_type"])
                ),
                "file_format": (
                    IMPORT_FILE_FORMAT_UNKNOWN
                    if failed
                    else _import_file_format_code(document["format"])
                ),
                "status": (
                    IMPORT_FILE_STATUS_FAILED
                    if failed
                    else IMPORT_FILE_STATUS_PENDING
                ),
                "updated_time": now,
            }
            existing = mappings.get(file_id)
            if existing is None or (
                existing["status"] == IMPORT_FILE_STATUS_FAILED and not failed
            ):
                mappings[file_id] = mapping
        if mappings:
            self.db.execute(update(TransactionImportFile), list(mappings.values()))

    def mark_files_failed(self, transaction_import_file_ids: list[int]) -> None:
        file_ids = sorted(set(transaction_import_file_ids))
        if not file_ids:
            return
        now = datetime.now()
        self.db.execute(update(TransactionImportFile), [
            {
                "id": file_id,
                "status": IMPORT_FILE_STATUS_FAILED,
                "updated_time": now,
            }
            for file_id in file_ids
        ])

    def write_plan(
        self,
        plan: dict[str, object],
        batch_code: str,
    ) -> dict[str, object]:
        documents = [doc for doc in plan["documents"] if not doc.get("duplicate")]
        now = datetime.now()
        file_ids = [doc.get("transaction_import_file_id") for doc in documents]
        if any(not isinstance(file_id, int) for file_id in file_ids):
            raise ValueError("confirmed import documents require pending file IDs")
        stored_files = {
            row["id"]: row
            for row in self.db.execute(select(
                TransactionImportFile.id,
                TransactionImportFile.sha256,
                TransactionImportFile.status,
            ).where(
                TransactionImportFile.id.in_(file_ids)
            )).mappings().all()
        }
        invalid_files = [
            file_id
            for document, file_id in zip(documents, file_ids)
            if file_id not in stored_files
            or stored_files[file_id]["sha256"] != document["sha256"]
            or stored_files[file_id]["status"] not in {
                IMPORT_FILE_STATUS_PENDING,
                IMPORT_FILE_STATUS_FAILED,
            }
        ]
        if invalid_files:
            raise ValueError(f"invalid pending import file IDs: {invalid_files}")
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
        file_updates = []
        for doc, import_file_id in zip(documents, file_ids):
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
            file_updates.append({
                "id": import_file_id,
                "batch_code": batch_code,
                "source_type": _import_source_code(doc["source_type"]),
                "file_format": _import_file_format_code(doc["format"]),
                "period_start": dated[0] if dated else "",
                "period_end": dated[-1] if dated else "",
                "total_count": len(doc["rows"]),
                "success_count": success_count,
                "skip_count": skip_count,
                "issue_count": issue_count,
                "status": status,
                "updated_time": now,
            })
            import_files.append((doc, import_file_id))

        for doc, import_file_id in import_files:
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
                    transaction_import_file_id=import_file_id,
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
        if file_updates:
            self.db.execute(update(TransactionImportFile), file_updates)
        affected_fact_ids = sorted({
            new_targets.get(row.get("match"), row.get("match"))
            for doc in documents
            for row in doc["rows"]
            if row["action"] in {"new", "supplement"}
        })
        return {
            "counts": plan["counts"],
            "transaction_import_file_ids": [
                file_id for _doc, file_id in import_files
            ],
            "transaction_fact_ids": sorted(new_targets.values()),
            "affected_fact_ids": affected_fact_ids,
        }

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
