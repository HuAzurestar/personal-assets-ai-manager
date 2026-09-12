from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, TypeVar

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    BillFactShadowVO,
    BillRawShadowVO,
    FactShadowReport,
    ImportFileShadowVO,
    LegacyBillVO,
    LegacyImportArtifactVO,
    LegacyImportBatchVO,
    LegacyImportIssueVO,
    LegacyOriginVO,
    ShadowTableReport,
)


ShadowVO = TypeVar("ShadowVO", ImportFileShadowVO, BillRawShadowVO, BillFactShadowVO)


class FactShadowMigrationService:
    """Idempotently populate and compare the new fact tables.

    The service never updates or deletes target facts. Existing target rows are
    treated as immutable comparison subjects, so drift is reported rather than
    silently repaired.
    """

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> FactShadowReport:
        blockers: list[str] = []
        bills = self.mapper.legacy_bills()
        batches = self.mapper.legacy_import_batches()
        artifacts = self.mapper.legacy_import_artifacts()
        origins = self.mapper.legacy_origins()
        issues = self.mapper.legacy_import_issues()

        expected_facts = self._facts(bills, origins, blockers)
        expected_raws = self._raws(batches, origins, issues, expected_facts, blockers)
        expected_files = self._import_files(
            batches,
            artifacts,
            expected_raws,
            expected_facts,
            blockers,
        )

        current_files = self.mapper.target_import_files()
        current_raws = self.mapper.target_bill_raws()
        current_facts = self.mapper.target_bill_facts()

        file_inserts = self._missing_rows(
            expected_files,
            current_files,
            blockers,
            unique_key=lambda row: (row.source_type, row.sha256) if row.sha256 else None,
            label="import_file",
        )
        fact_inserts = self._missing_rows(
            expected_facts,
            current_facts,
            blockers,
            unique_key=lambda row: row.fact_key,
            label="bill_fact",
        )
        raw_inserts = self._missing_rows(
            expected_raws,
            current_raws,
            blockers,
            unique_key=lambda row: (row.import_file_id, row.source_row_number),
            label="bill_raw",
        )

        now = datetime.now()
        try:
            self.mapper.insert_import_files(self._insert_values(file_inserts, now))
            self.mapper.insert_bill_facts(self._insert_values(fact_inserts, now))
            self.mapper.insert_bill_raws(self._insert_values(raw_inserts, now))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        actual_files = self.mapper.target_import_files()
        actual_raws = self.mapper.target_bill_raws()
        actual_facts = self.mapper.target_bill_facts()
        file_report = self._table_report(expected_files, actual_files, len(file_inserts))
        raw_report = self._table_report(expected_raws, actual_raws, len(raw_inserts))
        fact_report = self._table_report(expected_facts, actual_facts, len(fact_inserts))
        return FactShadowReport(
            matched=(
                not blockers
                and not file_report.mismatched_ids
                and not raw_report.mismatched_ids
                and not fact_report.mismatched_ids
                and file_report.expected_digest == file_report.actual_digest
                and raw_report.expected_digest == raw_report.actual_digest
                and fact_report.expected_digest == fact_report.actual_digest
            ),
            import_file=file_report,
            bill_raw=raw_report,
            bill_fact=fact_report,
            blockers=blockers,
        )

    @classmethod
    def _facts(
        cls,
        bills: tuple[LegacyBillVO, ...],
        origins: tuple[LegacyOriginVO, ...],
        blockers: list[str],
    ) -> tuple[BillFactShadowVO, ...]:
        origin_by_bill = {origin.bill_id: origin for origin in origins}
        facts: list[BillFactShadowVO] = []
        for bill in bills:
            amount = cls._amount(bill.amount)
            if amount is None:
                blockers.append(f"bill {bill.id}: amount is not a finite decimal")
                continue
            amount_value, amount_scale = amount
            if amount_value == 0:
                blockers.append(f"bill {bill.id}: zero amount has no legacy cash direction")
                continue
            origin = origin_by_bill.get(bill.id)
            identity = {
                "legacy_id": bill.id,
                "source_type": origin.source_type if origin else "manual",
                "source_reference": origin.source_reference if origin else "",
                "occurred_time": bill.occurred_at.isoformat(),
                "amount_value": amount_value,
                "amount_scale": amount_scale,
                "currency_code": (bill.currency or "CNY").upper(),
            }
            facts.append(BillFactShadowVO(
                id=bill.id,
                fact_key=f"legacy:{bill.id}:{cls._digest(identity)[:32]}",
                occurred_time=bill.occurred_at,
                cash_direction="IN" if bill.amount > 0 else "OUT",
                amount_value=amount_value,
                amount_scale=amount_scale,
                currency_code=(bill.currency or "CNY").upper(),
                account_code=cls._account_code(bill.account_name),
                counterparty=bill.merchant or "",
                summary=bill.note or "",
            ))
        return tuple(facts)

    @staticmethod
    def _account_code(value: str) -> str:
        normalized = (value or "").strip()
        if normalized in {"", "未提供账户", "手工未提供账户"}:
            return "UNKNOWN"
        return normalized

    @classmethod
    def _raws(
        cls,
        batches: tuple[LegacyImportBatchVO, ...],
        origins: tuple[LegacyOriginVO, ...],
        issues: tuple[LegacyImportIssueVO, ...],
        facts: tuple[BillFactShadowVO, ...],
        blockers: list[str],
    ) -> tuple[BillRawShadowVO, ...]:
        batch_ids = {batch.id for batch in batches}
        fact_ids = {fact.id for fact in facts}
        origins_by_key: dict[tuple[int, int], LegacyOriginVO] = {}
        issues_by_key: dict[tuple[int, int], LegacyImportIssueVO] = {}

        for origin in origins:
            if origin.import_batch_id is None or origin.source_row_number is None:
                if origin.import_batch_id is not None or origin.source_row_number is not None:
                    blockers.append(f"origin {origin.id}: incomplete import row identity")
                continue
            key = (origin.import_batch_id, origin.source_row_number)
            if origin.import_batch_id not in batch_ids:
                blockers.append(f"origin {origin.id}: missing import batch {origin.import_batch_id}")
                continue
            if key in origins_by_key:
                blockers.append(f"import row {key}: multiple accepted origins")
                continue
            origins_by_key[key] = origin

        for issue in issues:
            key = (issue.import_batch_id, issue.source_row_number)
            if issue.import_batch_id not in batch_ids:
                blockers.append(f"issue {issue.id}: missing import batch {issue.import_batch_id}")
                continue
            if key in issues_by_key:
                blockers.append(f"import row {key}: multiple issue records")
                continue
            issues_by_key[key] = issue

        raws: list[BillRawShadowVO] = []
        for batch_id, row_number in sorted(origins_by_key.keys() | issues_by_key.keys()):
            if batch_id >= 2**31 or row_number < 0 or row_number >= 2**32:
                blockers.append(f"import row {(batch_id, row_number)}: identity exceeds target ID range")
                continue
            origin = origins_by_key.get((batch_id, row_number))
            issue = issues_by_key.get((batch_id, row_number))
            if origin and origin.bill_id not in fact_ids:
                blockers.append(f"origin {origin.id}: missing accepted fact {origin.bill_id}")
                continue
            if issue and issue.bill_id and issue.bill_id not in fact_ids:
                blockers.append(f"issue {issue.id}: missing accepted fact {issue.bill_id}")
                continue
            if origin and issue and issue.bill_id and issue.bill_id != origin.bill_id:
                blockers.append(
                    f"import row {(batch_id, row_number)}: origin and issue link different bills"
                )
                continue
            payload = issue.raw_payload if issue else origin.raw_payload
            if origin and issue and cls._raw_hash(origin.raw_payload) != cls._raw_hash(issue.raw_payload):
                blockers.append(f"import row {(batch_id, row_number)}: conflicting raw payloads")
                continue
            if issue and issue.resolved_at is None:
                parse_status = "CONFLICT" if origin else "INVALID"
            elif issue and not issue.bill_id:
                parse_status = "SKIPPED"
            else:
                parse_status = "SUCCESS"
            bill_id = (issue.bill_id if issue and issue.bill_id else origin.bill_id if origin else 0)
            raws.append(BillRawShadowVO(
                id=(batch_id << 32) | row_number,
                bill_id=bill_id,
                import_file_id=batch_id,
                source_row_number=row_number,
                source_reference=origin.source_reference if origin else "",
                raw_payload=payload or "{}",
                raw_hash=cls._raw_hash(payload or "{}"),
                parse_status=parse_status,
                issue_code="LEGACY_IMPORT_ISSUE" if parse_status in {"INVALID", "CONFLICT"} else "",
                issue_message=issue.error if issue else "",
            ))
        return tuple(raws)

    @classmethod
    def _import_files(
        cls,
        batches: tuple[LegacyImportBatchVO, ...],
        artifacts: tuple[LegacyImportArtifactVO, ...],
        raws: tuple[BillRawShadowVO, ...],
        facts: tuple[BillFactShadowVO, ...],
        blockers: list[str],
    ) -> tuple[ImportFileShadowVO, ...]:
        artifacts_by_batch: dict[int, list[LegacyImportArtifactVO]] = {}
        batch_ids = {batch.id for batch in batches}
        for artifact in artifacts:
            if artifact.import_batch_id not in batch_ids:
                blockers.append(
                    f"artifact {artifact.id}: missing import batch {artifact.import_batch_id}"
                )
                continue
            artifacts_by_batch.setdefault(artifact.import_batch_id, []).append(artifact)

        raws_by_batch: dict[int, list[BillRawShadowVO]] = {}
        for raw in raws:
            raws_by_batch.setdefault(raw.import_file_id, []).append(raw)
        fact_by_id = {fact.id: fact for fact in facts}

        files: list[ImportFileShadowVO] = []
        for batch in batches:
            batch_artifacts = artifacts_by_batch.get(batch.id, [])
            if len(batch_artifacts) > 1:
                blockers.append(
                    f"import batch {batch.id}: {len(batch_artifacts)} artifacts cannot map to one import_file"
                )
                continue
            artifact = batch_artifacts[0] if batch_artifacts else None
            batch_raws = raws_by_batch.get(batch.id, [])
            success_count = sum(raw.parse_status == "SUCCESS" for raw in batch_raws)
            explicit_skip_count = sum(raw.parse_status in {"SKIPPED", "DUPLICATE"} for raw in batch_raws)
            issue_count = sum(raw.parse_status in {"INVALID", "CONFLICT"} for raw in batch_raws)
            accounted = success_count + explicit_skip_count + issue_count
            skip_count = explicit_skip_count + max(0, batch.row_count - accounted)
            if accounted > batch.row_count:
                blockers.append(
                    f"import batch {batch.id}: {accounted} current rows exceed row_count {batch.row_count}"
                )
            if success_count != batch.imported_count:
                blockers.append(
                    f"import batch {batch.id}: accepted row count {success_count} "
                    f"does not match imported_count {batch.imported_count}"
                )
            times = sorted(
                fact_by_id[raw.bill_id].occurred_time
                for raw in batch_raws
                if raw.bill_id in fact_by_id and raw.parse_status == "SUCCESS"
            )
            source = (artifact.source_type if artifact else batch.source_type or "UNKNOWN").upper()
            status = (
                "FAILED" if issue_count and success_count == 0
                else "PARTIAL" if issue_count
                else "IMPORTED"
            )
            files.append(ImportFileShadowVO(
                id=batch.id,
                batch_code=batch.batch_token or "",
                source_type=source,
                institution_code=source,
                filename=(artifact.filename if artifact else batch.filename) or "",
                file_format=(artifact.file_format if artifact else "UNKNOWN").upper(),
                sha256=artifact.sha256 if artifact else "",
                period_start=times[0].isoformat() if times else "",
                period_end=times[-1].isoformat() if times else "",
                total_count=batch.row_count,
                success_count=success_count,
                skip_count=skip_count,
                issue_count=issue_count,
                status=status,
            ))
        return tuple(files)

    @staticmethod
    def _amount(value: float) -> tuple[int, int] | None:
        try:
            decimal = Decimal(str(abs(value)))
        except (InvalidOperation, ValueError):
            return None
        if not decimal.is_finite():
            return None
        scale = max(2, -decimal.as_tuple().exponent)
        atomic = decimal * (Decimal(10) ** scale)
        if atomic != atomic.to_integral_value():
            return None
        return int(atomic), scale

    @classmethod
    def _raw_hash(cls, payload: str) -> str:
        try:
            canonical: object = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            canonical = payload
        return cls._digest(canonical)

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _missing_rows(
        cls,
        expected: tuple[ShadowVO, ...],
        current: tuple[ShadowVO, ...],
        blockers: list[str],
        *,
        unique_key,
        label: str,
    ) -> tuple[ShadowVO, ...]:
        current_ids = {row.id for row in current}
        unique_owners = {
            unique_key(row): row.id
            for row in current
            if unique_key(row) is not None
        }
        missing: list[ShadowVO] = []
        for row in expected:
            if row.id in current_ids:
                continue
            key = unique_key(row)
            owner = unique_owners.get(key) if key is not None else None
            if owner is not None:
                blockers.append(
                    f"{label} {row.id}: unique identity is already owned by target row {owner}"
                )
                continue
            missing.append(row)
            if key is not None:
                unique_owners[key] = row.id
        return tuple(missing)

    @staticmethod
    def _insert_values(rows: tuple[ShadowVO, ...], now: datetime) -> list[dict[str, object]]:
        return [dict(asdict(row), created_time=now, updated_time=now) for row in rows]

    @classmethod
    def _table_report(
        cls,
        expected: tuple[ShadowVO, ...],
        actual: tuple[ShadowVO, ...],
        inserted_count: int,
    ) -> ShadowTableReport:
        expected_by_id = {row.id: asdict(row) for row in expected}
        actual_by_id = {row.id: asdict(row) for row in actual}
        all_ids = expected_by_id.keys() | actual_by_id.keys()
        mismatched = sorted(
            row_id for row_id in all_ids
            if expected_by_id.get(row_id) != actual_by_id.get(row_id)
        )
        return ShadowTableReport(
            expected_count=len(expected),
            actual_count=len(actual),
            inserted_count=inserted_count,
            expected_digest=cls._digest([expected_by_id[row_id] for row_id in sorted(expected_by_id)]),
            actual_digest=cls._digest([actual_by_id[row_id] for row_id in sorted(actual_by_id)]),
            mismatched_ids=mismatched,
        )
