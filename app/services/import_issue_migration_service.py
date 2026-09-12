from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    BillFactShadowVO,
    BillRawShadowVO,
    LegacyImportIssueActionVO,
    LegacyImportIssueVO,
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


IMPORT_ISSUE_CASE_START = 6 << 40
IMPORT_ISSUE_CASE_END = 7 << 40


class ImportIssueReviewShadowMigrationService:
    """Migrate import decisions while bill_raw remains the issue source of truth."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> ReviewShadowReport:
        blockers: list[str] = []
        issues = self.mapper.legacy_import_issues()
        actions = self.mapper.legacy_import_issue_actions()
        raw_ids = [self._raw_id(issue) for issue in issues]
        raws = {raw.id: raw for raw in self.mapper.target_bill_raws(raw_ids)}
        bill_ids = sorted({issue.bill_id for issue in issues if issue.bill_id})
        facts = {fact.id: fact for fact in self.mapper.target_bill_facts(bill_ids)}
        expected_cases, expected_lines, expected_history = self._expected(
            issues,
            actions,
            raws,
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
        query = {
            "case_id_start": IMPORT_ISSUE_CASE_START,
            "case_id_end": IMPORT_ISSUE_CASE_END,
        }
        return (
            self.mapper.target_review_cases(**query),
            self.mapper.target_review_case_bills(**query),
            self.mapper.target_review_history(**query),
        )

    @classmethod
    def _expected(cls, issues, actions, raws, facts, blockers):
        issue_by_id = {issue.id: issue for issue in issues}
        actions_by_issue: dict[int, list[LegacyImportIssueActionVO]] = {}
        for action in actions:
            if action.issue_id not in issue_by_id:
                blockers.append(
                    f"import issue action {action.id}: missing issue {action.issue_id}"
                )
                continue
            actions_by_issue.setdefault(action.issue_id, []).append(action)

        cases: list[ReviewCaseShadowVO] = []
        lines: list[ReviewCaseBillShadowVO] = []
        histories: list[ReviewHistoryShadowVO] = []
        for issue in issues:
            if issue.id <= 0 or issue.id >= (1 << 40):
                blockers.append(f"import issue {issue.id}: ID exceeds migration namespace")
                continue
            if issue.created_at is None:
                blockers.append(
                    f"import issue {issue.id}: missing import batch {issue.import_batch_id}"
                )
                continue
            issue_actions = actions_by_issue.get(issue.id, [])
            if len(issue_actions) + 1 >= MAX_CHILD_ID:
                blockers.append(f"import issue {issue.id}: action history exceeds migration range")
                continue
            raw_id = cls._raw_id(issue)
            raw = raws.get(raw_id)
            if not raw:
                blockers.append(f"import issue {issue.id}: missing bill_raw {raw_id}")
                continue
            case_id = IMPORT_ISSUE_CASE_START + issue.id
            title = f"Import issue: {issue.error}"[:160]
            status = "PENDING"
            resolution: dict[str, object] = {}
            current_bill_id = 0
            current_fact: BillFactShadowVO | None = None
            version = 1
            initial_snapshot = cls._snapshot(
                title,
                raw,
                issue.error,
                status,
                resolution,
                current_fact,
                version,
            )
            case_history = [ReviewHistoryShadowVO(
                id=cls._child_id(case_id, version),
                created_time=issue.created_at,
                updated_time=issue.created_at,
                case_id=case_id,
                version=version,
                operation="CREATE",
                schema_version=1,
                request_json="{}",
                before_json="{}",
                after_json=initial_snapshot,
                snapshot_hash=cls._sha256(initial_snapshot),
                reverses_history_id=0,
                actor="system",
                reason="",
                idempotency_key="",
            )]
            last_dismiss_history_id = 0
            valid = True
            for action in issue_actions:
                request_json = cls._canonical_json_text(
                    action.payload,
                    f"import issue action {action.id} payload",
                    blockers,
                )
                if request_json is None:
                    valid = False
                    break
                payload = ReviewMatterShadowMigrationService._parse_object(
                    request_json,
                    f"import issue action {action.id} payload",
                    blockers,
                )
                if payload is None:
                    valid = False
                    break
                before_json = case_history[-1].after_json
                reverses_history_id = 0
                if action.action == "resolve" and status == "PENDING":
                    if not issue.bill_id or issue.bill_id not in facts:
                        blockers.append(
                            f"import issue action {action.id}: resolve has no accepted bill_fact"
                        )
                        valid = False
                        break
                    status = "CONFIRMED"
                    resolution = payload
                    current_bill_id = issue.bill_id
                    current_fact = facts[issue.bill_id]
                    operation = "CONFIRM"
                elif action.action == "dismiss" and status == "PENDING":
                    status = "REJECTED"
                    resolution = payload
                    current_bill_id = 0
                    current_fact = None
                    operation = "CONFIRM"
                elif action.action == "reopen" and status == "REJECTED":
                    status = "PENDING"
                    current_bill_id = 0
                    current_fact = None
                    operation = "RESTORE"
                    reverses_history_id = last_dismiss_history_id
                    if not reverses_history_id:
                        blockers.append(
                            f"import issue action {action.id}: reopen has no dismiss to reverse"
                        )
                        valid = False
                        break
                else:
                    blockers.append(
                        f"import issue action {action.id}: {action.action} is invalid from {status}"
                    )
                    valid = False
                    break

                version += 1
                after_json = cls._snapshot(
                    title,
                    raw,
                    issue.error,
                    status,
                    resolution,
                    current_fact,
                    version,
                )
                history_id = cls._child_id(case_id, version)
                case_history.append(ReviewHistoryShadowVO(
                    id=history_id,
                    created_time=action.created_at,
                    updated_time=action.created_at,
                    case_id=case_id,
                    version=version,
                    operation=operation,
                    schema_version=1,
                    request_json=request_json,
                    before_json=before_json,
                    after_json=after_json,
                    snapshot_hash=cls._sha256(after_json),
                    reverses_history_id=reverses_history_id,
                    actor=action.actor or "local-user",
                    reason=payload.get("reason", "") if isinstance(payload.get("reason", ""), str) else "",
                    idempotency_key="",
                ))
                if action.action == "dismiss":
                    last_dismiss_history_id = history_id

            if not valid:
                continue
            expected_status = (
                "PENDING" if issue.resolved_at is None
                else "CONFIRMED" if issue.bill_id else "REJECTED"
            )
            expected_resolution = cls._canonical_json_text(
                issue.resolution,
                f"import issue {issue.id} resolution",
                blockers,
            )
            if expected_resolution is None:
                continue
            if issue_actions and cls._canonical(resolution) != expected_resolution:
                blockers.append(f"import issue {issue.id}: resolution differs from action history")
                continue
            if not issue_actions and expected_status != "PENDING":
                blockers.append(f"import issue {issue.id}: resolved state has no action history")
                continue
            if status != expected_status or current_bill_id != (issue.bill_id or 0):
                blockers.append(f"import issue {issue.id}: current row differs from action history")
                continue
            if not cls._raw_matches(raw, expected_status, issue.bill_id or 0):
                blockers.append(f"import issue {issue.id}: bill_raw state differs from review state")
                continue

            updated_time = issue_actions[-1].created_at if issue_actions else issue.created_at
            result = cls._result(raw.id, issue.error, resolution)
            cases.append(ReviewCaseShadowVO(
                id=case_id,
                created_time=issue.created_at,
                updated_time=updated_time,
                review_type="FACT_CONFLICT",
                status=status,
                allocation_status="CONFLICT" if status == "PENDING" else "COMPLETE",
                version=version,
                title=title,
                result_json=cls._canonical(result),
            ))
            if current_fact:
                lines.append(cls._line(case_id, updated_time, current_fact))
            histories.extend(case_history)
        return tuple(cases), tuple(lines), tuple(histories)

    @classmethod
    def _snapshot(cls, title, raw, error, status, resolution, fact, version):
        line_values = []
        if fact:
            line_values.append(cls._line_values(fact))
        return cls._canonical({
            "allocation_status": "CONFLICT" if status == "PENDING" else "COMPLETE",
            "lines": line_values,
            "result": cls._result(raw.id, error, resolution),
            "review_type": "FACT_CONFLICT",
            "status": status,
            "title": title,
            "version": version,
        })

    @staticmethod
    def _result(raw_id: int, error: str, resolution: dict[str, object]):
        return {
            "bill_raw_id": raw_id,
            "issue_code": "LEGACY_IMPORT_ISSUE",
            "issue_message": error,
            "resolution": resolution,
        }

    @classmethod
    def _line(cls, case_id, updated_time, fact):
        return ReviewCaseBillShadowVO(
            id=cls._child_id(case_id, 1),
            created_time=updated_time,
            updated_time=updated_time,
            case_id=case_id,
            **cls._line_values(fact),
        )

    @staticmethod
    def _line_values(fact):
        return {
            "bill_id": fact.id,
            "role": "FACT_ACCEPTED",
            "party": "",
            "amount_value": fact.amount_value,
            "amount_scale": fact.amount_scale,
            "currency_code": fact.currency_code,
        }

    @staticmethod
    def _raw_matches(raw: BillRawShadowVO, status: str, bill_id: int) -> bool:
        if status == "CONFIRMED":
            return raw.parse_status == "SUCCESS" and raw.bill_id == bill_id and bill_id > 0
        if status == "REJECTED":
            return raw.parse_status == "SKIPPED" and raw.bill_id == 0
        return raw.parse_status in {"INVALID", "CONFLICT"}

    @staticmethod
    def _raw_id(issue: LegacyImportIssueVO) -> int:
        return (issue.import_batch_id << 32) | issue.source_row_number

    @staticmethod
    def _canonical_json_text(raw: str, label: str, blockers: list[str]):
        return ReviewMatterShadowMigrationService._canonical_json_text(raw, label, blockers)

    @staticmethod
    def _canonical(value: object) -> str:
        return ReviewMatterShadowMigrationService._canonical(value)

    @staticmethod
    def _sha256(value: str) -> str:
        return ReviewMatterShadowMigrationService._sha256(value)

    @staticmethod
    def _child_id(case_id: int, child: int) -> int:
        return (case_id << CHILD_ID_BITS) | child

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)
