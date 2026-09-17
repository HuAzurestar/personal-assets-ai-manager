from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_OUT,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PARTIAL,
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
from backend.parser.statement_parser import digest
from backend.smart_import import build_plan


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


class TargetImportMatchMapper:
    """Build import plans from bounded candidate and source-evidence reads."""

    def __init__(self, db: Session):
        self.db = db

    def plan(
        self,
        documents: list[dict[str, object]],
        known_accounts: dict[int, dict[str, object]],
        accounts: dict[str, str] | None = None,
        decisions: dict[str, str] | None = None,
    ) -> dict[str, object]:
        plan = build_plan(
            documents,
            known_accounts,
            {},
            self.load_history,
            accounts,
            decisions,
        )
        # Row conflicts remain persistable evidence. Whole-file parse failures
        # and unresolved identity choices still block confirmation.
        plan["can_confirm"] = not (
            plan["counts"].get("errors", 0)
            or plan["counts"].get("ambiguous", 0)
        )
        plan["version"] = digest({
            key: value for key, value in plan.items() if key != "version"
        })
        return plan

    def load_history(
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
        identity_fact_ids = set(identities.values())
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
        if identity_fact_ids:
            clauses.append(TransactionFact.id.in_(identity_fact_ids))
        query = select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount,
            TransactionFact.amount_scale,
            TransactionFact.currency_code,
        )
        query = query.where(or_(*clauses)) if clauses else query.where(False)
        fact_rows = self.db.execute(query).mappings().all()
        facts = {}
        for row in fact_rows:
            signed = Decimal(row["amount"]) / (
                Decimal(10) ** row["amount_scale"]
            )
            if row["cash_direction"] == CASH_DIRECTION_OUT:
                signed = -signed
            facts[row["id"]] = {
                "id": row["id"],
                "amount": signed,
                "currency": row["currency_code"],
                "occurred_at": row["occurred_time"],
            }

        evidence: dict[int, list[dict[str, object]]] = defaultdict(list)
        evidence_rows = self.db.execute(select(
            TransactionImportRow.transaction_fact_id,
            TransactionImportRow.raw_payload,
        ).where(
            TransactionImportRow.transaction_fact_id.in_(facts)
        )).mappings().all()
        for row in evidence_rows:
            try:
                envelope = json.loads(row["raw_payload"])
                normalized = envelope.get("normalized")
            except (json.JSONDecodeError, TypeError, AttributeError):
                normalized = None
            if row["transaction_fact_id"] and isinstance(normalized, dict):
                evidence[row["transaction_fact_id"]].append(normalized)

        matched_references: dict[tuple[str, str], list[int]] = defaultdict(list)
        reference_query = select(
            TransactionImportRow.transaction_fact_id,
            TransactionImportRow.source_reference,
            TransactionImportFile.source_type,
        ).join(
            TransactionImportFile,
            TransactionImportRow.transaction_import_file_id
            == TransactionImportFile.id,
        )
        if source_types and references:
            source_codes = [_import_source_code(value) for value in source_types]
            reference_query = reference_query.where(
                TransactionImportFile.source_type.in_(source_codes),
                TransactionImportRow.source_reference.in_(references),
                TransactionImportRow.transaction_fact_id > 0,
            )
        else:
            reference_query = reference_query.where(False)
        for row in self.db.execute(reference_query).mappings().all():
            source_name = _import_source_name(row["source_type"])
            matched_references[(source_name, row["source_reference"])].append(
                row["transaction_fact_id"]
            )

        seen_files = set(self.db.scalars(select(TransactionImportFile.sha256).where(
            TransactionImportFile.sha256.in_(upload_hashes),
            TransactionImportFile.status.in_((
                IMPORT_FILE_STATUS_IMPORTED,
                IMPORT_FILE_STATUS_PARTIAL,
            )),
        )).all())
        return {
            "identities": identities,
            "facts": facts,
            "evidence": dict(evidence),
            "references": dict(matched_references),
            "seen_files": seen_files,
        }
