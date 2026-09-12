from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    BillFactShadowVO,
    LegacyCandidateActionVO,
    LegacyCandidateVO,
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


CANDIDATE_CASE_START = 2 << 40
CANDIDATE_CASE_END = 3 << 40


class CandidateReviewShadowMigrationService:
    """Migrate candidate suggestions and decisions into unified Review rows."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> ReviewShadowReport:
        blockers: list[str] = []
        candidates = self.mapper.legacy_candidates()
        actions = self.mapper.legacy_candidate_actions()
        members_by_candidate = self._members(candidates, blockers)
        bill_ids = sorted({
            bill_id
            for member_ids in members_by_candidate.values()
            for bill_id in member_ids
        })
        facts = self.mapper.target_bill_facts(bill_ids)
        expected_cases, expected_lines, expected_history = self._expected(
            candidates,
            actions,
            members_by_candidate,
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
            "case_id_start": CANDIDATE_CASE_START,
            "case_id_end": CANDIDATE_CASE_END,
        }
        return (
            self.mapper.target_review_cases(**query),
            self.mapper.target_review_case_bills(**query),
            self.mapper.target_review_history(**query),
        )

    @classmethod
    def _expected(
        cls,
        candidates: tuple[LegacyCandidateVO, ...],
        actions: tuple[LegacyCandidateActionVO, ...],
        members_by_candidate: dict[int, tuple[int, ...]],
        facts: tuple[BillFactShadowVO, ...],
        blockers: list[str],
    ):
        candidate_by_id = {candidate.id: candidate for candidate in candidates}
        fact_by_id = {fact.id: fact for fact in facts}
        actions_by_candidate: dict[int, list[LegacyCandidateActionVO]] = {}
        for action in actions:
            if action.candidate_id not in candidate_by_id:
                blockers.append(
                    f"candidate action {action.id}: missing candidate {action.candidate_id}"
                )
                continue
            actions_by_candidate.setdefault(action.candidate_id, []).append(action)

        cases: list[ReviewCaseShadowVO] = []
        lines: list[ReviewCaseBillShadowVO] = []
        histories: list[ReviewHistoryShadowVO] = []
        for candidate in candidates:
            if candidate.id <= 0 or candidate.id >= (1 << 40):
                blockers.append(f"candidate {candidate.id}: ID exceeds migration namespace")
                continue
            review_type = cls._review_type(candidate.candidate_type)
            if review_type is None:
                blockers.append(
                    f"candidate {candidate.id}: unsupported type {candidate.candidate_type}"
                )
                continue
            member_ids = members_by_candidate.get(candidate.id)
            if not member_ids:
                continue
            member_facts = [fact_by_id.get(bill_id) for bill_id in member_ids]
            if any(fact is None for fact in member_facts):
                missing = [
                    bill_id for bill_id, fact in zip(member_ids, member_facts)
                    if fact is None
                ]
                blockers.append(
                    f"candidate {candidate.id}: missing bill_fact rows {missing}"
                )
                continue
            candidate_actions = actions_by_candidate.get(candidate.id, [])
            if len(candidate_actions) + 1 >= MAX_CHILD_ID:
                blockers.append(f"candidate {candidate.id}: action history exceeds migration range")
                continue
            parsed_actions = cls._parse_actions(candidate_actions, candidate.id, blockers)
            if parsed_actions is None:
                continue

            current_state = cls._candidate_state(candidate)
            if parsed_actions:
                final_state = parsed_actions[-1][2].get("candidate")
                if not isinstance(final_state, dict) or not cls._same_current_state(
                    current_state,
                    final_state,
                ):
                    blockers.append(
                        f"candidate {candidate.id}: current row differs from latest action snapshot"
                    )
                    continue
                initial_state = parsed_actions[0][1]
            else:
                initial_state = {"candidate": current_state, "bills": {}}

            case_id = CANDIDATE_CASE_START + candidate.id
            current_status = cls._status(candidate.status)
            if current_status is None:
                blockers.append(
                    f"candidate {candidate.id}: unsupported status {candidate.status}"
                )
                continue
            current_line_values, allocation_status = cls._line_values(
                review_type,
                candidate.status,
                candidate.retained_bill_id,
                member_facts,
                candidate.id,
                blockers,
            )
            if current_line_values is None:
                continue
            result = cls._result(candidate)
            updated_time = (
                candidate_actions[-1].created_at if candidate_actions else candidate.created_at
            )
            version = len(candidate_actions) + 1
            cases.append(ReviewCaseShadowVO(
                id=case_id,
                created_time=candidate.created_at,
                updated_time=updated_time,
                review_type=review_type,
                status=current_status,
                allocation_status=allocation_status,
                version=version,
                title=candidate.reason or candidate.candidate_type,
                result_json=cls._canonical(result),
            ))
            for position, values in enumerate(current_line_values, start=1):
                lines.append(ReviewCaseBillShadowVO(
                    id=cls._child_id(case_id, position),
                    created_time=updated_time,
                    updated_time=updated_time,
                    case_id=case_id,
                    **values,
                ))

            initial_snapshot = cls._history_snapshot(
                candidate,
                review_type,
                initial_state,
                member_facts,
                1,
                blockers,
            )
            if initial_snapshot is None:
                cases.pop()
                del lines[-len(current_line_values):]
                continue
            histories.append(ReviewHistoryShadowVO(
                id=cls._child_id(case_id, 1),
                created_time=candidate.created_at,
                updated_time=candidate.created_at,
                case_id=case_id,
                version=1,
                operation="CREATE",
                schema_version=1,
                request_json="{}",
                before_json="{}",
                after_json=initial_snapshot,
                snapshot_hash=cls._sha256(initial_snapshot),
                reverses_history_id=0,
                actor="system",
                reason=candidate.reason or "Candidate generated",
                idempotency_key="",
            ))
            action_history_ids: dict[int, int] = {}
            previous_after = initial_snapshot
            candidate_valid = True
            for offset, (action, before_state, after_state) in enumerate(parsed_actions, start=2):
                before_snapshot = cls._history_snapshot(
                    candidate,
                    review_type,
                    before_state,
                    member_facts,
                    offset - 1,
                    blockers,
                )
                after_snapshot = cls._history_snapshot(
                    candidate,
                    review_type,
                    after_state,
                    member_facts,
                    offset,
                    blockers,
                )
                if before_snapshot is None or after_snapshot is None:
                    candidate_valid = False
                    break
                if before_snapshot != previous_after:
                    blockers.append(
                        f"candidate action {action.id}: before snapshot breaks the history chain"
                    )
                    candidate_valid = False
                    break
                request_json = cls._canonical_json_text(
                    action.request_payload,
                    f"candidate action {action.id} request",
                    blockers,
                )
                if request_json is None:
                    candidate_valid = False
                    break
                history_id = cls._child_id(case_id, offset)
                reversed_history_id = 0
                if action.action == "undo":
                    if action.reverses_action_id not in action_history_ids:
                        blockers.append(
                            f"candidate action {action.id}: missing reversed action {action.reverses_action_id}"
                        )
                        candidate_valid = False
                        break
                    reversed_history_id = action_history_ids[action.reverses_action_id]
                histories.append(ReviewHistoryShadowVO(
                    id=history_id,
                    created_time=action.created_at,
                    updated_time=action.created_at,
                    case_id=case_id,
                    version=offset,
                    operation=cls._operation(action.action),
                    schema_version=1,
                    request_json=request_json,
                    before_json=before_snapshot,
                    after_json=after_snapshot,
                    snapshot_hash=cls._sha256(after_snapshot),
                    reverses_history_id=reversed_history_id,
                    actor=action.actor or "local-user",
                    reason=action.reason or "",
                    idempotency_key=cls._idempotency_key(action.idempotency_key or ""),
                ))
                action_history_ids[action.id] = history_id
                previous_after = after_snapshot
            if not candidate_valid:
                cases.pop()
                del lines[-len(current_line_values):]
                histories[:] = [row for row in histories if row.case_id != case_id]
                continue
            reversed_actions = {
                action.reverses_action_id
                for action in candidate_actions
                if action.action == "undo" and action.reverses_action_id is not None
            }
            incorrectly_marked = [
                action.id for action in candidate_actions
                if action.undone != (action.id in reversed_actions)
            ]
            if incorrectly_marked:
                blockers.append(
                    f"candidate {candidate.id}: undone flags disagree for actions {incorrectly_marked}"
                )
        return tuple(cases), tuple(lines), tuple(histories)

    @classmethod
    def _members(
        cls,
        candidates: tuple[LegacyCandidateVO, ...],
        blockers: list[str],
    ) -> dict[int, tuple[int, ...]]:
        result: dict[int, tuple[int, ...]] = {}
        for candidate in candidates:
            try:
                stored = json.loads(candidate.member_bill_ids or "[]")
            except (json.JSONDecodeError, TypeError):
                blockers.append(f"candidate {candidate.id}: member_bill_ids is invalid JSON")
                continue
            if not isinstance(stored, list) or any(
                not isinstance(value, int) or value <= 0 for value in stored
            ):
                blockers.append(f"candidate {candidate.id}: member_bill_ids is invalid")
                continue
            member_ids = tuple(dict.fromkeys([
                *stored,
                candidate.bill_id,
                candidate.related_bill_id,
            ]))
            if len(member_ids) < 2:
                blockers.append(f"candidate {candidate.id}: fewer than two distinct members")
                continue
            result[candidate.id] = member_ids
        return result

    @classmethod
    def _parse_actions(cls, actions, candidate_id: int, blockers: list[str]):
        parsed = []
        previous_after = None
        for action in actions:
            before_state = cls._parse_object(
                action.before_state,
                f"candidate action {action.id} before_state",
                blockers,
            )
            after_state = cls._parse_object(
                action.after_state,
                f"candidate action {action.id} after_state",
                blockers,
            )
            if before_state is None or after_state is None:
                return None
            if previous_after is not None and cls._canonical(before_state) != cls._canonical(previous_after):
                blockers.append(
                    f"candidate action {action.id}: raw action snapshots are not contiguous"
                )
                return None
            parsed.append((action, before_state, after_state))
            previous_after = after_state
        return parsed

    @classmethod
    def _history_snapshot(
        cls,
        candidate: LegacyCandidateVO,
        review_type: str,
        state: dict[str, object],
        facts: list[BillFactShadowVO],
        version: int,
        blockers: list[str],
    ) -> str | None:
        state_candidate = state.get("candidate")
        if not isinstance(state_candidate, dict) or not isinstance(
            state_candidate.get("status"), str
        ):
            blockers.append(f"candidate {candidate.id}: history snapshot lacks candidate status")
            return None
        legacy_status = state_candidate["status"]
        status = cls._status(legacy_status)
        if status is None:
            blockers.append(
                f"candidate {candidate.id}: history has unsupported status {legacy_status}"
            )
            return None
        retained = state_candidate.get("retained_bill_id")
        line_values, allocation_status = cls._line_values(
            review_type,
            legacy_status,
            retained if isinstance(retained, int) else None,
            facts,
            candidate.id,
            blockers,
        )
        if line_values is None:
            return None
        return cls._canonical({
            "allocation_status": allocation_status,
            "lines": line_values,
            "result": {
                **cls._result(candidate, state=state_candidate),
                "legacy_state": state,
            },
            "review_type": review_type,
            "status": status,
            "title": candidate.reason or candidate.candidate_type,
            "version": version,
        })

    @staticmethod
    def _line_values(
        review_type: str,
        legacy_status: str,
        retained_bill_id: int | None,
        facts: list[BillFactShadowVO],
        candidate_id: int,
        blockers: list[str],
    ):
        confirmed = CandidateReviewShadowMigrationService._status(legacy_status) == "CONFIRMED"
        allocation_status = "COMPLETE" if confirmed else "PARTIAL"
        if review_type == "DUPLICATE" and confirmed:
            if retained_bill_id not in {fact.id for fact in facts}:
                blockers.append(
                    f"candidate {candidate_id}: confirmed duplicate lacks a valid retained bill"
                )
                return None, "CONFLICT"
            identity = {
                (fact.cash_direction, fact.amount_value, fact.amount_scale, fact.currency_code)
                for fact in facts
            }
            if len(identity) != 1:
                blockers.append(
                    f"candidate {candidate_id}: confirmed duplicate members do not share one amount"
                )
                allocation_status = "CONFLICT"
        if review_type == "TRANSFER" and confirmed:
            totals: dict[tuple[int, str], dict[str, int]] = {}
            for fact in facts:
                key = (fact.amount_scale, fact.currency_code)
                totals.setdefault(key, {"IN": 0, "OUT": 0})[fact.cash_direction] += fact.amount_value
            if len(totals) != 1 or any(value["IN"] != value["OUT"] for value in totals.values()):
                blockers.append(
                    f"candidate {candidate_id}: confirmed transfer is not balanced by scale/currency"
                )
                allocation_status = "CONFLICT"

        values = []
        for fact in facts:
            if review_type == "TRANSFER":
                role = "TRANSFER_IN" if fact.cash_direction == "IN" else "TRANSFER_OUT"
            elif confirmed:
                role = (
                    "DUPLICATE_RETAINED"
                    if fact.id == retained_bill_id
                    else "DUPLICATE_EXCLUDED"
                )
            else:
                role = "DUPLICATE_MEMBER"
            values.append({
                "bill_id": fact.id,
                "role": role,
                "party": "",
                "amount_value": fact.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
            })
        return values, allocation_status

    @staticmethod
    def _review_type(candidate_type: str) -> str | None:
        return {"duplicate": "DUPLICATE", "transfer": "TRANSFER"}.get(candidate_type)

    @staticmethod
    def _status(legacy_status: str) -> str | None:
        if legacy_status in {
            "pending", "deferred", "evidence_insufficient", "legacy_duplicate_needs_review"
        }:
            return "PENDING"
        if legacy_status in {
            "duplicate_excluded",
            "personal_transfer_grouped",
            "third_party_transfer_grouped",
            "transfer_grouped",
            "legacy_transfer_excluded",
        }:
            return "CONFIRMED"
        if legacy_status in {"duplicate_rejected", "ignored"}:
            return "REJECTED"
        if legacy_status == "superseded_duplicate_group":
            return "REVOKED"
        return None

    @staticmethod
    def _operation(action: str) -> str:
        if action == "undo":
            return "RESTORE"
        if action in {"confirm_transfer", "confirm_personal_transfer", "resolve_duplicate"}:
            return "CONFIRM"
        return "UPDATE"

    @staticmethod
    def _candidate_state(candidate: LegacyCandidateVO) -> dict[str, object]:
        return {
            "status": candidate.status,
            "transfer_group_id": candidate.transfer_group_id,
            "transfer_kind": candidate.transfer_kind,
            "retained_bill_id": candidate.retained_bill_id,
            "resolved_at": candidate.resolved_at.isoformat() if candidate.resolved_at else None,
        }

    @staticmethod
    def _same_current_state(expected: dict[str, object], actual: dict[str, object]) -> bool:
        return all(actual.get(key) == value for key, value in expected.items())

    @staticmethod
    def _result(
        candidate: LegacyCandidateVO,
        *,
        state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        state = state or CandidateReviewShadowMigrationService._candidate_state(candidate)
        retained = state.get("retained_bill_id")
        return {
            "confidence": candidate.confidence,
            "group_fingerprint": candidate.group_fingerprint or "",
            "legacy_status": state.get("status", candidate.status),
            "retained_bill_id": retained or 0,
            "superseded_by_case_id": (
                CANDIDATE_CASE_START + candidate.superseded_by_id
                if candidate.superseded_by_id else 0
            ),
            "transfer_group_id": state.get("transfer_group_id") or "",
            "transfer_kind": state.get("transfer_kind") or "",
        }

    @staticmethod
    def _parse_object(raw: str, label: str, blockers: list[str]):
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
    def _canonical_json_text(cls, raw: str, label: str, blockers: list[str]):
        if not raw:
            return "{}"
        parsed = cls._parse_object(raw, label, blockers)
        return cls._canonical(parsed) if parsed is not None else None

    @staticmethod
    def _canonical(value: object) -> str:
        return ReviewMatterShadowMigrationService._canonical(value)

    @staticmethod
    def _sha256(value: str) -> str:
        return ReviewMatterShadowMigrationService._sha256(value)

    @classmethod
    def _idempotency_key(cls, legacy_key: str) -> str:
        if not legacy_key:
            return ""
        namespaced = f"candidate:{legacy_key}"
        return (
            namespaced if len(namespaced) <= 120
            else f"candidate:sha256:{cls._sha256(legacy_key)}"
        )

    @staticmethod
    def _child_id(case_id: int, child: int) -> int:
        return (case_id << CHILD_ID_BITS) | child

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)

    @staticmethod
    def _missing_history(expected, current, blockers):
        return ReviewMatterShadowMigrationService._missing_history(
            expected,
            current,
            blockers,
        )
