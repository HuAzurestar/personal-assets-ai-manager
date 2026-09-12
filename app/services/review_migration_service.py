from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TypeVar

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    BillFactShadowVO,
    LegacyMatterRevisionVO,
    LegacyMatterVO,
    ReviewCaseBillShadowVO,
    ReviewCaseShadowVO,
    ReviewHistoryShadowVO,
    ReviewShadowReport,
    ShadowTableReport,
)


MATTER_CASE_START = 1 << 40
MATTER_CASE_END = 2 << 40
CHILD_ID_BITS = 10
MAX_CHILD_ID = 1 << CHILD_ID_BITS

ReviewShadowVO = TypeVar(
    "ReviewShadowVO",
    ReviewCaseShadowVO,
    ReviewCaseBillShadowVO,
    ReviewHistoryShadowVO,
)


@dataclass(frozen=True, slots=True)
class _RevisionState:
    revision: LegacyMatterRevisionVO
    review_type: str
    title: str
    result_json: str
    lines: tuple[dict[str, object], ...]
    allocation_status: str
    snapshot_json: str


class ReviewMatterShadowMigrationService:
    """Backfill manual Review matters without changing their legacy source."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> ReviewShadowReport:
        blockers: list[str] = []
        matters = self.mapper.legacy_matters()
        revisions = self.mapper.legacy_matter_revisions()
        parsed_snapshots = self._parse_snapshots(revisions, blockers)
        bill_ids = sorted({
            int(line["bill_id"])
            for snapshot in parsed_snapshots.values()
            for line in snapshot.get("lines", [])
            if isinstance(line, dict) and isinstance(line.get("bill_id"), int)
        })
        facts = self.mapper.target_bill_facts(bill_ids)
        expected_cases, expected_lines, expected_history = self._expected(
            matters,
            revisions,
            parsed_snapshots,
            facts,
            blockers,
        )

        current_cases, current_lines, current_history = self._current()
        case_inserts = self._missing(expected_cases, current_cases)
        line_inserts = self._missing(expected_lines, current_lines)
        history_inserts = self._missing_history(
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
        case_report = self._table_report(expected_cases, actual_cases, len(case_inserts))
        line_report = self._table_report(expected_lines, actual_lines, len(line_inserts))
        history_report = self._table_report(
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
            "case_id_start": MATTER_CASE_START,
            "case_id_end": MATTER_CASE_END,
        }
        return (
            self.mapper.target_review_cases(**query),
            self.mapper.target_review_case_bills(**query),
            self.mapper.target_review_history(**query),
        )

    @classmethod
    def _expected(
        cls,
        matters: tuple[LegacyMatterVO, ...],
        revisions: tuple[LegacyMatterRevisionVO, ...],
        snapshots: dict[int, dict[str, object]],
        facts: tuple[BillFactShadowVO, ...],
        blockers: list[str],
    ) -> tuple[
        tuple[ReviewCaseShadowVO, ...],
        tuple[ReviewCaseBillShadowVO, ...],
        tuple[ReviewHistoryShadowVO, ...],
    ]:
        matter_by_id = {matter.id: matter for matter in matters}
        fact_by_id = {fact.id: fact for fact in facts}
        revisions_by_matter: dict[int, list[LegacyMatterRevisionVO]] = {}
        for revision in revisions:
            if revision.matter_id not in matter_by_id:
                blockers.append(
                    f"matter revision {revision.id}: missing matter {revision.matter_id}"
                )
                continue
            revisions_by_matter.setdefault(revision.matter_id, []).append(revision)

        cases: list[ReviewCaseShadowVO] = []
        case_lines: list[ReviewCaseBillShadowVO] = []
        history: list[ReviewHistoryShadowVO] = []
        for matter in matters:
            if matter.id <= 0 or matter.id >= (1 << 40):
                blockers.append(f"matter {matter.id}: ID exceeds migration namespace")
                continue
            if matter.version <= 0 or matter.version >= MAX_CHILD_ID:
                blockers.append(f"matter {matter.id}: version exceeds migration child range")
                continue
            matter_revisions = revisions_by_matter.get(matter.id, [])
            versions = [revision.version for revision in matter_revisions]
            if versions != list(range(1, matter.version + 1)):
                blockers.append(
                    f"matter {matter.id}: versions {versions} do not match current version {matter.version}"
                )
                continue
            transformed: list[_RevisionState] = []
            valid = True
            for revision in matter_revisions:
                snapshot = snapshots.get(revision.id)
                if snapshot is None:
                    valid = False
                    break
                state = cls._transform_revision(revision, snapshot, fact_by_id, blockers)
                if state is None:
                    valid = False
                    break
                transformed.append(state)
            if not valid or not transformed:
                continue

            case_id = MATTER_CASE_START + matter.id
            current = transformed[-1]
            status = cls._status(current.revision.action, current.revision.id, blockers)
            if status is None:
                continue
            cases.append(ReviewCaseShadowVO(
                id=case_id,
                created_time=matter.created_at,
                updated_time=current.revision.created_at,
                review_type=current.review_type,
                status=status,
                allocation_status=current.allocation_status,
                version=matter.version,
                title=current.title,
                result_json=current.result_json,
            ))
            for index, line in enumerate(current.lines, start=1):
                case_lines.append(ReviewCaseBillShadowVO(
                    id=cls._child_id(case_id, index),
                    created_time=current.revision.created_at,
                    updated_time=current.revision.created_at,
                    case_id=case_id,
                    **line,
                ))

            before_json = "{}"
            for state in transformed:
                revision = state.revision
                operation = (
                    "REVOKE" if revision.action == "revoke"
                    else "CREATE" if revision.version == 1
                    else "UPDATE"
                )
                request_json = cls._canonical_json_text(
                    revision.request_payload,
                    f"matter revision {revision.id} request",
                    blockers,
                )
                if request_json is None:
                    valid = False
                    break
                history_id = cls._child_id(case_id, revision.version)
                history.append(ReviewHistoryShadowVO(
                    id=history_id,
                    created_time=revision.created_at,
                    updated_time=revision.created_at,
                    case_id=case_id,
                    version=revision.version,
                    operation=operation,
                    schema_version=1,
                    request_json=request_json,
                    before_json=before_json,
                    after_json=state.snapshot_json,
                    snapshot_hash=cls._sha256(state.snapshot_json),
                    reverses_history_id=(
                        cls._child_id(case_id, revision.version - 1)
                        if operation == "REVOKE" and revision.version > 1
                        else 0
                    ),
                    actor=revision.actor or "local-user",
                    reason=revision.reason or "",
                    idempotency_key=cls._idempotency_key(revision.idempotency_key),
                ))
                before_json = state.snapshot_json
            if not valid:
                cases.pop()
                del case_lines[-len(current.lines):]
                history[:] = [row for row in history if row.case_id != case_id]
        return tuple(cases), tuple(case_lines), tuple(history)

    @classmethod
    def _transform_revision(
        cls,
        revision: LegacyMatterRevisionVO,
        snapshot: dict[str, object],
        facts: dict[int, BillFactShadowVO],
        blockers: list[str],
    ) -> _RevisionState | None:
        if revision.action not in {"confirm", "revoke"}:
            blockers.append(
                f"matter revision {revision.id}: unsupported action {revision.action}"
            )
            return None
        raw_lines = snapshot.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            blockers.append(f"matter revision {revision.id}: lines must be a non-empty list")
            return None
        scenarios = snapshot.get("scenarios", [])
        if not isinstance(scenarios, list) or any(not isinstance(item, str) for item in scenarios):
            blockers.append(f"matter revision {revision.id}: scenarios are invalid")
            return None
        review_type = cls._review_type(raw_lines, scenarios)
        if review_type is None:
            blockers.append(f"matter revision {revision.id}: mixed or unknown Review semantics")
            return None

        lines: list[dict[str, object]] = []
        allocations: dict[int, int] = {}
        for position, raw_line in enumerate(raw_lines, start=1):
            if not isinstance(raw_line, dict):
                blockers.append(f"matter revision {revision.id}: line {position} is not an object")
                return None
            bill_id = raw_line.get("bill_id")
            amount_cents = raw_line.get("amount_cents")
            legacy_role = raw_line.get("role")
            party = raw_line.get("party", "")
            if (
                not isinstance(bill_id, int)
                or not isinstance(amount_cents, int)
                or amount_cents <= 0
                or not isinstance(legacy_role, str)
                or not isinstance(party, str)
            ):
                blockers.append(f"matter revision {revision.id}: line {position} is invalid")
                return None
            fact = facts.get(bill_id)
            if not fact:
                blockers.append(f"matter revision {revision.id}: missing bill_fact {bill_id}")
                return None
            if fact.currency_code != "CNY":
                blockers.append(
                    f"matter revision {revision.id}: bill {bill_id} has unsupported legacy Review currency {fact.currency_code}"
                )
                return None
            role = cls._role(review_type, legacy_role, fact.cash_direction)
            if role is None:
                blockers.append(
                    f"matter revision {revision.id}: role {legacy_role} does not fit {review_type}"
                )
                return None
            lines.append({
                "bill_id": bill_id,
                "role": role,
                "party": party,
                "amount_value": amount_cents,
                "amount_scale": 2,
                "currency_code": "CNY",
            })
            allocations[bill_id] = allocations.get(bill_id, 0) + amount_cents

        allocation_status = "COMPLETE"
        for bill_id, allocated_cents in allocations.items():
            fact = facts[bill_id]
            fact_cents = cls._fact_cents(fact)
            if fact_cents is None or allocated_cents > fact_cents:
                blockers.append(
                    f"matter revision {revision.id}: allocation for bill {bill_id} conflicts with fact amount"
                )
                allocation_status = "CONFLICT"
            elif allocated_cents < fact_cents and allocation_status != "CONFLICT":
                allocation_status = "PARTIAL"

        title = snapshot.get("title", "")
        if not isinstance(title, str):
            blockers.append(f"matter revision {revision.id}: title is invalid")
            return None
        result = {
            "balances": snapshot.get("balances", []),
            "own_accounts_confirmed": bool(snapshot.get("own_accounts_confirmed", False)),
            "scenarios": scenarios,
        }
        status = "REVOKED" if revision.action == "revoke" else "CONFIRMED"
        aggregate = {
            "allocation_status": allocation_status,
            "lines": lines,
            "result": result,
            "review_type": review_type,
            "status": status,
            "title": title,
            "version": revision.version,
        }
        return _RevisionState(
            revision=revision,
            review_type=review_type,
            title=title,
            result_json=cls._canonical(result),
            lines=tuple(lines),
            allocation_status=allocation_status,
            snapshot_json=cls._canonical(aggregate),
        )

    @staticmethod
    def _review_type(lines: list[object], scenarios: list[str]) -> str | None:
        roles = {
            line.get("role")
            for line in lines
            if isinstance(line, dict) and isinstance(line.get("role"), str)
        }
        if "transfer" in roles:
            return "TRANSFER" if not roles.intersection({
                "receivable", "payable", "repayment_received", "repayment_paid"
            }) else None
        if any("AA" in scenario.upper() for scenario in scenarios):
            return "AA"
        lends = bool(roles.intersection({"receivable", "repayment_received"}))
        borrows = bool(roles.intersection({"payable", "repayment_paid"}))
        if lends and borrows:
            return None
        if lends:
            return "LOAN_LEND"
        if borrows:
            return "LOAN_BORROW"
        return None

    @staticmethod
    def _role(review_type: str, legacy_role: str, direction: str) -> str | None:
        common = {"expense": "EXPENSE", "income": "INCOME"}
        if legacy_role in common:
            return common[legacy_role]
        if review_type == "AA":
            return {
                "receivable": "AA_PAID",
                "repayment_received": "AA_RECEIVED",
                "payable": "AA_RECEIVED",
                "repayment_paid": "AA_PAID",
            }.get(legacy_role)
        if review_type == "LOAN_LEND":
            return {
                "receivable": "LOAN_LENT",
                "repayment_received": "LOAN_RECOVERED",
            }.get(legacy_role)
        if review_type == "LOAN_BORROW":
            return {
                "payable": "LOAN_RECEIVED",
                "repayment_paid": "LOAN_REPAID",
            }.get(legacy_role)
        if review_type == "TRANSFER" and legacy_role == "transfer":
            return "TRANSFER_IN" if direction == "IN" else "TRANSFER_OUT"
        return None

    @staticmethod
    def _fact_cents(fact: BillFactShadowVO) -> int | None:
        if fact.amount_scale == 2:
            return fact.amount_value
        if fact.amount_scale > 2:
            divisor = 10 ** (fact.amount_scale - 2)
            return fact.amount_value // divisor if fact.amount_value % divisor == 0 else None
        return fact.amount_value * (10 ** (2 - fact.amount_scale))

    @staticmethod
    def _status(action: str, revision_id: int, blockers: list[str]) -> str | None:
        if action == "confirm":
            return "CONFIRMED"
        if action == "revoke":
            return "REVOKED"
        blockers.append(f"matter revision {revision_id}: unsupported action {action}")
        return None

    @classmethod
    def _parse_snapshots(
        cls,
        revisions: tuple[LegacyMatterRevisionVO, ...],
        blockers: list[str],
    ) -> dict[int, dict[str, object]]:
        result: dict[int, dict[str, object]] = {}
        for revision in revisions:
            parsed = cls._parse_object(
                revision.snapshot,
                f"matter revision {revision.id} snapshot",
                blockers,
            )
            if parsed is not None:
                result[revision.id] = parsed
        return result

    @staticmethod
    def _parse_object(raw: str, label: str, blockers: list[str]) -> dict[str, object] | None:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            blockers.append(f"{label}: invalid JSON")
            return None
        if not isinstance(parsed, dict):
            blockers.append(f"{label}: expected a JSON object")
            return None
        return parsed

    @classmethod
    def _canonical_json_text(
        cls,
        raw: str,
        label: str,
        blockers: list[str],
    ) -> str | None:
        if not raw:
            return "{}"
        parsed = cls._parse_object(raw, label, blockers)
        return cls._canonical(parsed) if parsed is not None else None

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
        )

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _idempotency_key(cls, legacy_key: str) -> str:
        if not legacy_key:
            return ""
        namespaced = f"matter:{legacy_key}"
        if len(namespaced) <= 120:
            return namespaced
        return f"matter:sha256:{cls._sha256(legacy_key)}"

    @staticmethod
    def _child_id(case_id: int, child: int) -> int:
        if child <= 0 or child >= MAX_CHILD_ID:
            raise ValueError(f"Review child identifier {child} exceeds migration range")
        return (case_id << CHILD_ID_BITS) | child

    @staticmethod
    def _missing(
        expected: tuple[ReviewShadowVO, ...],
        current: tuple[ReviewShadowVO, ...],
    ) -> tuple[ReviewShadowVO, ...]:
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)

    @classmethod
    def _missing_history(
        cls,
        expected: tuple[ReviewHistoryShadowVO, ...],
        current: tuple[ReviewHistoryShadowVO, ...],
        blockers: list[str],
    ) -> tuple[ReviewHistoryShadowVO, ...]:
        current_ids = {row.id for row in current}
        version_owner = {(row.case_id, row.version): row.id for row in current}
        key_owner = {
            row.idempotency_key: row.id
            for row in current
            if row.idempotency_key
        }
        result: list[ReviewHistoryShadowVO] = []
        for row in expected:
            if row.id in current_ids:
                continue
            version_conflict = version_owner.get((row.case_id, row.version))
            key_conflict = key_owner.get(row.idempotency_key) if row.idempotency_key else None
            if version_conflict is not None or key_conflict is not None:
                blockers.append(
                    f"review_history {row.id}: target unique identity is already occupied"
                )
                continue
            result.append(row)
            version_owner[(row.case_id, row.version)] = row.id
            if row.idempotency_key:
                key_owner[row.idempotency_key] = row.id
        return tuple(result)

    @classmethod
    def _table_report(
        cls,
        expected: tuple[ReviewShadowVO, ...],
        actual: tuple[ReviewShadowVO, ...],
        inserted_count: int,
    ) -> ShadowTableReport:
        expected_by_id = {row.id: asdict(row) for row in expected}
        actual_by_id = {row.id: asdict(row) for row in actual}
        ids = expected_by_id.keys() | actual_by_id.keys()
        mismatched = sorted(
            row_id for row_id in ids
            if expected_by_id.get(row_id) != actual_by_id.get(row_id)
        )
        return ShadowTableReport(
            expected_count=len(expected),
            actual_count=len(actual),
            inserted_count=inserted_count,
            expected_digest=cls._sha256(cls._canonical([
                expected_by_id[row_id] for row_id in sorted(expected_by_id)
            ])),
            actual_digest=cls._sha256(cls._canonical([
                actual_by_id[row_id] for row_id in sorted(actual_by_id)
            ])),
            mismatched_ids=mismatched,
        )
