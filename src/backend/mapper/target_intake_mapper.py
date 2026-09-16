from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import case, func, or_, select, text
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
    BillRaw,
    TransactionFact,
    TransactionImportFile,
)
from backend.smart_import import build_plan, dump
from backend.parser.statement_parser import digest


_IMPORT_SOURCE_BY_NAME = {
    "unknown": IMPORT_SOURCE_UNKNOWN,
    "manual": IMPORT_SOURCE_MANUAL,
    "alipay": IMPORT_SOURCE_ALIPAY,
    "wechat": IMPORT_SOURCE_WECHAT,
    "ccb": IMPORT_SOURCE_CCB_BANK,
    "abc": IMPORT_SOURCE_ABC_BANK,
    "cmb": IMPORT_SOURCE_CMB_BANK,
}
_IMPORT_SOURCE_NAME_BY_CODE = {
    code: name for name, code in _IMPORT_SOURCE_BY_NAME.items()
}
_IMPORT_SOURCE_SEARCH_TERMS = {
    IMPORT_SOURCE_MANUAL: ("manual", "手工"),
    IMPORT_SOURCE_ALIPAY: ("alipay", "支付宝"),
    IMPORT_SOURCE_WECHAT: ("wechat", "微信", "微信支付"),
    IMPORT_SOURCE_CCB_BANK: ("ccb", "建设银行", "建行"),
    IMPORT_SOURCE_ABC_BANK: ("abc", "农业银行", "农行"),
    IMPORT_SOURCE_CMB_BANK: ("cmb", "招商银行", "招行"),
}
_IMPORT_FILE_FORMAT_BY_NAME = {
    "unknown": IMPORT_FILE_FORMAT_UNKNOWN,
    "csv": IMPORT_FILE_FORMAT_CSV,
    "xls": IMPORT_FILE_FORMAT_XLS,
    "xlsx": IMPORT_FILE_FORMAT_XLSX,
    "pdf": IMPORT_FILE_FORMAT_PDF,
}
_IMPORT_FILE_STATUS_NAME_BY_CODE = {
    IMPORT_FILE_STATUS_PENDING: "PENDING",
    IMPORT_FILE_STATUS_IMPORTED: "IMPORTED",
    IMPORT_FILE_STATUS_PARTIAL: "PARTIAL",
    IMPORT_FILE_STATUS_FAILED: "FAILED",
}


def _import_source_code(name: str) -> int:
    try:
        return _IMPORT_SOURCE_BY_NAME[name.casefold()]
    except KeyError as error:
        raise ValueError(f"unknown import source: {name}") from error


def _import_source_name(code: int) -> str:
    try:
        return _IMPORT_SOURCE_NAME_BY_CODE[code]
    except KeyError as error:
        raise ValueError(f"unknown persisted import source: {code}") from error


def _import_file_format_code(name: str) -> int:
    try:
        return _IMPORT_FILE_FORMAT_BY_NAME[name.casefold()]
    except KeyError as error:
        raise ValueError(f"unknown import content format: {name}") from error


def _import_file_status_name(code: int) -> str:
    try:
        return _IMPORT_FILE_STATUS_NAME_BY_CODE[code]
    except KeyError as error:
        raise ValueError(f"unknown persisted import file status: {code}") from error


class TargetIntakeMapper:
    """Set-oriented persistence for the PIRC-9 Fact-layer import path."""

    def __init__(self, db: Session):
        self.db = db

    def plan(
        self,
        documents: list[dict[str, object]],
        accounts: dict[str, str] | None = None,
        decisions: dict[str, str] | None = None,
    ) -> dict[str, object]:
        known_accounts = self._known_accounts()
        plan = build_plan(
            documents,
            known_accounts,
            {},
            self._planning_history,
            accounts,
            decisions,
        )
        # Persist row-level conflicts as bill_raw evidence. Whole-file parse
        # failures and unresolved identity choices still block confirmation.
        plan["can_confirm"] = not (
            plan["counts"].get("errors", 0)
            or plan["counts"].get("ambiguous", 0)
        )
        plan["version"] = digest({
            key: value for key, value in plan.items() if key != "version"
        })
        return plan

    def _known_accounts(self) -> dict[int, dict[str, object]]:
        ranked = self._ranked_accounts()
        rows = self.db.execute(select(
            ranked.c.account_code,
            ranked.c.raw_payload,
        ).where(
            ranked.c.position == 1,
        ).order_by(ranked.c.account_code)).mappings().all()
        items = self._account_items(rows)
        return {item["id"]: item for item in items}

    @staticmethod
    def _ranked_accounts():
        return select(
            TransactionFact.account_code.label("account_code"),
            BillRaw.raw_payload.label("raw_payload"),
            func.row_number().over(
                partition_by=TransactionFact.account_code,
                order_by=BillRaw.id.desc(),
            ).label("position"),
        ).outerjoin(BillRaw, BillRaw.bill_id == TransactionFact.id).subquery()

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

    def _planning_history(
        self,
        lookup_keys: set[str],
        occurred_values: list[datetime],
        source_types: set[str],
        references: set[str],
        upload_hashes: set[str],
    ) -> dict[str, object]:
        identities = {
            row["fact_key"]: row["id"]
            for row in self.db.execute(select(
                TransactionFact.id,
                TransactionFact.fact_key,
            ).where(TransactionFact.fact_key.in_(lookup_keys))).mappings().all()
        }
        identity_bill_ids = set(identities.values())
        clauses = []
        if occurred_values:
            first_day = datetime.combine(min(occurred_values).date(), time.min)
            last_day = datetime.combine(
                max(occurred_values).date() + timedelta(days=1), time.min
            )
            clauses.append(
                (TransactionFact.occurred_time >= first_day)
                & (TransactionFact.occurred_time < last_day)
            )
        if identity_bill_ids:
            clauses.append(TransactionFact.id.in_(identity_bill_ids))
        query = select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount_value,
            TransactionFact.amount_scale,
            TransactionFact.currency_code,
        )
        query = query.where(or_(*clauses)) if clauses else query.where(False)
        fact_rows = self.db.execute(query).mappings().all()
        bills = {}
        for row in fact_rows:
            signed = Decimal(row["amount_value"]) / (Decimal(10) ** row["amount_scale"])
            if row["cash_direction"] == CASH_DIRECTION_OUT:
                signed = -signed
            bills[row["id"]] = {
                "id": row["id"],
                "amount": signed,
                "currency": row["currency_code"],
                "occurred_at": row["occurred_time"],
            }

        evidence: dict[int, list[dict[str, object]]] = defaultdict(list)
        evidence_rows = self.db.execute(select(
            BillRaw.bill_id,
            BillRaw.raw_payload,
        ).where(BillRaw.bill_id.in_(bills))).mappings().all()
        for row in evidence_rows:
            try:
                envelope = json.loads(row["raw_payload"])
                normalized = envelope.get("normalized")
            except (json.JSONDecodeError, TypeError, AttributeError):
                normalized = None
            if row["bill_id"] and isinstance(normalized, dict):
                evidence[row["bill_id"]].append(normalized)

        matched_references: dict[tuple[str, str], list[int]] = defaultdict(list)
        reference_query = select(
            BillRaw.bill_id,
            BillRaw.source_reference,
            TransactionImportFile.source_type,
        ).join(
            TransactionImportFile,
            BillRaw.import_file_id == TransactionImportFile.id,
        )
        if source_types and references:
            source_codes = [_import_source_code(value) for value in source_types]
            reference_query = reference_query.where(
                TransactionImportFile.source_type.in_(source_codes),
                BillRaw.source_reference.in_(references),
                BillRaw.bill_id > 0,
            )
        else:
            reference_query = reference_query.where(False)
        for row in self.db.execute(reference_query).mappings().all():
            source_name = _import_source_name(row["source_type"])
            matched_references[(source_name, row["source_reference"])].append(
                row["bill_id"]
            )

        seen_files = set(self.db.scalars(select(TransactionImportFile.sha256).where(
            TransactionImportFile.sha256.in_(upload_hashes)
        )).all())
        return {
            "identities": identities,
            "bills": bills,
            "evidence": dict(evidence),
            "references": dict(matched_references),
            "seen_files": seen_files,
        }

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
                bill_id = new_targets.get(target, target) if target is not None else 0
                bill_id = bill_id if isinstance(bill_id, int) else 0
                action = row["action"]
                parse_status = (
                    "SUCCESS"
                    if action in {"new", "supplement"}
                    else "SKIPPED"
                    if action == "record"
                    else "INVALID"
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
                raw_row = BillRaw(
                    bill_id=bill_id,
                    import_file_id=import_file.id,
                    source_row_number=row["row_number"],
                    source_reference=row.get("reference", ""),
                    raw_payload=dump(envelope),
                    raw_hash=digest(raw),
                    parse_status=parse_status,
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
                self.db.add(raw_row)
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
            search = [
                TransactionImportFile.filename.ilike(pattern),
            ]
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
            matching_files = select(BillRaw.import_file_id).join(
                TransactionFact,
                BillRaw.bill_id == TransactionFact.id,
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
                BillRaw.import_file_id,
                TransactionFact.account_code,
            ).join(
                TransactionFact,
                BillRaw.bill_id == TransactionFact.id,
            ).where(
                BillRaw.import_file_id.in_(file_ids),
            ).distinct().order_by(
                BillRaw.import_file_id,
                TransactionFact.account_code,
            )).mappings().all()
            for link in links:
                account_codes[link["import_file_id"]].append(link["account_code"])
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
        import_file_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        batch = self.db.execute(select(
            TransactionImportFile.total_count,
            TransactionImportFile.success_count,
            TransactionImportFile.skip_count,
            TransactionImportFile.issue_count,
        ).where(
            TransactionImportFile.id == import_file_id
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
            BillRaw.id,
            BillRaw.bill_id,
            BillRaw.parse_status,
            BillRaw.raw_payload,
        ).where(BillRaw.import_file_id == import_file_id).order_by(
            BillRaw.source_row_number,
        ).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        items = [{
            "id": row["id"],
            "bill_id": row["bill_id"],
            "disposition": row["parse_status"],
            "record": json.loads(row["raw_payload"]),
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

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
