from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    BillFactShadowVO,
    LegacyAccountRevisionVO,
    ReviewCaseBillShadowVO,
    ReviewCaseShadowVO,
    ReviewHistoryShadowVO,
    ReviewShadowReport,
)
from app.services.review_migration_service import (
    CHILD_ID_BITS,
    MAX_CHILD_ID,
    ReviewMatterShadowMigrationService,
)


ACCOUNT_CASE_START = 4 << 40
ACCOUNT_CASE_END = 5 << 40


class AccountReviewShadowMigrationService:
    """Migrate explicit account corrections; source account evidence stays raw."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> ReviewShadowReport:
        blockers: list[str] = []
        revisions = self.mapper.legacy_account_revisions()
        bill_ids = sorted({revision.bill_id for revision in revisions})
        bills = {
            bill.id: bill for bill in self.mapper.legacy_account_bills(bill_ids)
        }
        facts = {
            fact.id: fact for fact in self.mapper.target_bill_facts(bill_ids)
        }
        expected_cases, expected_lines, expected_history = self._expected(
            revisions,
            bills,
            facts,
            blockers,
        )
        current_cases, current_lines, current_history = self._current()
        case_inserts = self._missing(expected_cases, current_cases)
        line_inserts = self._missing(expected_lines, current_lines)
        history_inserts = ReviewMatterShadowMigrationService._missing_history(
            expected_history,
            current_history,
            blockers,
        )
        try:
            self.mapper.insert_review_cases([asdict(row) for row in case_inserts])
            self.mapper.insert_review_case_bills([asdict(row) for row in line_inserts])
            self.mapper.insert_review_history([asdict(row) for row in history_inserts])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        actual_cases, actual_lines, actual_history = self._current()
        support = ReviewMatterShadowMigrationService
        case_report = support._table_report(expected_cases, actual_cases, len(case_inserts))
        line_report = support._table_report(expected_lines, actual_lines, len(line_inserts))
        history_report = support._table_report(
            expected_history,
            actual_history,
            len(history_inserts),
        )
        return ReviewShadowReport(
            matched=(
                not blockers
                and not case_report.mismatched_ids
                and not line_report.mismatched_ids
                and not history_report.mismatched_ids
            ),
            review_case=case_report,
            review_case_bill=line_report,
            review_history=history_report,
            blockers=blockers,
        )

    def _current(self):
        query = {"case_id_start": ACCOUNT_CASE_START, "case_id_end": ACCOUNT_CASE_END}
        return (
            self.mapper.target_review_cases(**query),
            self.mapper.target_review_case_bills(**query),
            self.mapper.target_review_history(**query),
        )

    @classmethod
    def _expected(cls, revisions, bills, facts, blockers):
        revisions_by_bill: dict[int, list[LegacyAccountRevisionVO]] = {}
        for revision in revisions:
            revisions_by_bill.setdefault(revision.bill_id, []).append(revision)

        cases: list[ReviewCaseShadowVO] = []
        lines: list[ReviewCaseBillShadowVO] = []
        histories: list[ReviewHistoryShadowVO] = []
        for bill_id, bill_revisions in sorted(revisions_by_bill.items()):
            if bill_id <= 0 or bill_id >= (1 << 40):
                blockers.append(f"account bill {bill_id}: ID exceeds migration namespace")
                continue
            if len(bill_revisions) >= MAX_CHILD_ID:
                blockers.append(f"account bill {bill_id}: revision history exceeds migration range")
                continue
            bill = bills.get(bill_id)
            fact = facts.get(bill_id)
            if not bill or not fact:
                blockers.append(f"account bill {bill_id}: missing bill or bill_fact")
                continue
            active_confirmations: set[int] = set()
            revision_history_ids: dict[int, int] = {}
            current_account = bill_revisions[0].before_account
            case_id = ACCOUNT_CASE_START + bill_id
            case_history: list[ReviewHistoryShadowVO] = []
            valid = True
            for version, revision in enumerate(bill_revisions, start=1):
                if revision.action not in {"confirm", "undo"}:
                    blockers.append(
                        f"account revision {revision.id}: unsupported action {revision.action}"
                    )
                    valid = False
                    break
                if revision.before_account != current_account:
                    blockers.append(
                        f"account revision {revision.id}: before account breaks the history chain"
                    )
                    valid = False
                    break
                before_status = "CONFIRMED" if active_confirmations else "REVOKED"
                before_json = cls._snapshot(
                    fact,
                    current_account,
                    before_status,
                    version - 1,
                )
                reverses_history_id = 0
                if revision.action == "confirm":
                    active_confirmations.add(revision.id)
                    operation = "CREATE" if version == 1 else "UPDATE"
                else:
                    reversed_id = revision.reverses_revision_id
                    if reversed_id not in active_confirmations:
                        blockers.append(
                            f"account revision {revision.id}: invalid reversed revision {reversed_id}"
                        )
                        valid = False
                        break
                    active_confirmations.remove(reversed_id)
                    reverses_history_id = revision_history_ids.get(reversed_id, 0)
                    if not reverses_history_id:
                        blockers.append(
                            f"account revision {revision.id}: reversed history is missing"
                        )
                        valid = False
                        break
                    operation = "RESTORE"
                current_account = revision.after_account
                after_status = "CONFIRMED" if active_confirmations else "REVOKED"
                after_json = cls._snapshot(fact, current_account, after_status, version)
                request_json = cls._canonical_json_text(
                    revision.request_payload,
                    f"account revision {revision.id} request",
                    blockers,
                )
                if request_json is None:
                    valid = False
                    break
                history_id = cls._child_id(case_id, version)
                case_history.append(ReviewHistoryShadowVO(
                    id=history_id,
                    created_time=revision.created_at,
                    updated_time=revision.created_at,
                    case_id=case_id,
                    version=version,
                    operation=operation,
                    schema_version=1,
                    request_json=request_json,
                    before_json=before_json,
                    after_json=after_json,
                    snapshot_hash=cls._sha256(after_json),
                    reverses_history_id=reverses_history_id,
                    actor=revision.actor or "local-user",
                    reason=revision.reason or "",
                    idempotency_key=cls._idempotency_key(revision.idempotency_key or ""),
                ))
                revision_history_ids[revision.id] = history_id
            if not valid:
                continue
            reversed_ids = {
                revision.reverses_revision_id
                for revision in bill_revisions
                if revision.action == "undo" and revision.reverses_revision_id is not None
            }
            inconsistent = [
                revision.id for revision in bill_revisions
                if revision.undone != (revision.id in reversed_ids)
            ]
            if inconsistent:
                blockers.append(
                    f"account bill {bill_id}: undone flags disagree for revisions {inconsistent}"
                )
            if current_account != bill.account_name:
                blockers.append(
                    f"account bill {bill_id}: current bill account differs from revision history"
                )
                continue
            status = "CONFIRMED" if active_confirmations else "REVOKED"
            updated_time = bill_revisions[-1].created_at
            cases.append(ReviewCaseShadowVO(
                id=case_id,
                created_time=bill_revisions[0].created_at,
                updated_time=updated_time,
                review_type="ACCOUNT",
                status=status,
                allocation_status="COMPLETE",
                version=len(bill_revisions),
                title=f"Account: {fact.counterparty}",
                result_json=cls._canonical({"account_name": current_account}),
            ))
            lines.append(ReviewCaseBillShadowVO(
                id=cls._child_id(case_id, 1),
                created_time=updated_time,
                updated_time=updated_time,
                case_id=case_id,
                bill_id=bill_id,
                role="ACCOUNT",
                party="",
                amount_value=fact.amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
            ))
            histories.extend(case_history)
        return tuple(cases), tuple(lines), tuple(histories)

    @classmethod
    def _snapshot(
        cls,
        fact: BillFactShadowVO,
        account_name: str,
        status: str,
        version: int,
    ) -> str:
        return cls._canonical({
            "allocation_status": "COMPLETE",
            "lines": [{
                "bill_id": fact.id,
                "role": "ACCOUNT",
                "party": "",
                "amount_value": fact.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
            }],
            "result": {"account_name": account_name},
            "review_type": "ACCOUNT",
            "status": status,
            "title": f"Account: {fact.counterparty}",
            "version": version,
        })

    @staticmethod
    def _canonical_json_text(raw: str, label: str, blockers: list[str]):
        return ReviewMatterShadowMigrationService._canonical_json_text(raw, label, blockers)

    @staticmethod
    def _canonical(value: object) -> str:
        return ReviewMatterShadowMigrationService._canonical(value)

    @staticmethod
    def _sha256(value: str) -> str:
        return ReviewMatterShadowMigrationService._sha256(value)

    @classmethod
    def _idempotency_key(cls, source: str) -> str:
        if not source:
            return ""
        value = f"account:{source}"
        return value if len(value) <= 120 else f"account:sha256:{cls._sha256(source)}"

    @staticmethod
    def _child_id(case_id: int, child: int) -> int:
        return (case_id << CHILD_ID_BITS) | child

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)
