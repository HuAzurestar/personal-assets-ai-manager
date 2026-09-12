from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import ReviewCommandError
from app.mappers.review_mapper import ReviewMapper
from app.money import cents
from app.schemas import CandidateBatchDecision, CandidateDecision, CandidatePageRead, ReviewCandidateRead
from app.schemas.review import (
    CandidateActionWriteVO,
    DuplicateBillVO,
    ReviewCandidateVO,
    ReviewCommandBillVO,
    ReviewCommandCandidateVO,
    ReviewPageQuery,
)
from app.services.ledger_service import bill_read_from_vo


_AGGREGATION_EFFECTS = {
    "pending": "尚未改变流水或收支汇总。",
    "deferred": "稍后处理；两笔流水仍独立计入收支。",
    "ignored": "已忽略；两笔流水仍独立计入收支。",
    "evidence_insufficient": "账户证据不足，已暂缓；两笔流水仍独立计入收支。",
    "personal_transfer_grouped": "已确认个人账户间转移；两笔保留并追踪资产流向，不计入收入/支出汇总、净额或趋势。手续费等不在本候选两笔内的真实成本仍保留统计。",
    "third_party_transfer_grouped": "已确认他人资产转移/代收代付；两笔原始流水与标签保留并标记为不追踪收支，不计入收入/支出、净额或趋势。",
    "transfer_grouped": "已归入同一转移组；两笔保留但不计入收入/支出汇总。",
    "duplicate_excluded": "已保留指定流水；另一笔保留原始记录但不计入收支汇总。",
    "duplicate_rejected": "已拒绝重复建议；候选组所有原始流水继续计入收入、支出、净额和趋势。",
    "legacy_transfer_excluded": "旧版已按转移排除收支；保留原始流水，但没有可补回的账户证据。",
    "legacy_duplicate_needs_review": "旧版曾标记为已确认，但未保存保留哪一笔；两笔仍独立计入收支。",
}


class ReviewService:
    def __init__(self, db: Session):
        self.db = db
        self.mapper = ReviewMapper(db)

    def page(self, query: ReviewPageQuery) -> CandidatePageRead:
        self._consolidate_and_commit()
        page = self.mapper.page(query)
        return CandidatePageRead(
            items=[self._read(candidate) for candidate in page.items],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
        )

    def all(self) -> list[ReviewCandidateRead]:
        self._consolidate_and_commit()
        return [self._read(candidate) for candidate in self.mapper.all()]

    def by_ids(self, candidate_ids: list[int]) -> list[ReviewCandidateRead]:
        return [self._read(candidate) for candidate in self.mapper.by_ids(candidate_ids)]

    def decide_batch(self, payload: CandidateBatchDecision) -> list[ReviewCandidateRead]:
        candidate_ids = [item.candidate_id for item in payload.items]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ReviewCommandError(422, "Each candidate can be processed only once per batch")

        self.mapper.begin_immediate()
        try:
            candidates = self.mapper.command_candidates(candidate_ids)
            missing_candidates = [candidate_id for candidate_id in candidate_ids if candidate_id not in candidates]
            if missing_candidates:
                raise ReviewCommandError(404, f"Candidate {missing_candidates[0]} not found")

            bill_ids = list(dict.fromkeys(
                bill_id
                for candidate in candidates.values()
                for bill_id in candidate.member_bill_ids
            ))
            bills = self.mapper.command_bills(bill_ids)
            idempotency_keys = [
                item.idempotency_key for item in payload.items if item.idempotency_key
            ]
            existing_actions = self.mapper.existing_actions(idempotency_keys)
            latest_action_ids = self.mapper.latest_action_ids(candidate_ids)
            active_owners = self.mapper.active_candidate_owners()
            matter_owners = self.mapper.current_matter_owners()
            refund_bill_ids = self.mapper.refund_bill_ids(bill_ids)

            candidate_updates: list[dict] = []
            changed_bill_ids: set[int] = set()
            action_writes: list[CandidateActionWriteVO] = []
            seen_idempotency_keys: set[str] = set()

            for item in payload.items:
                decision = CandidateDecision(
                    action=item.action,
                    retained_bill_id=item.retained_bill_id,
                    idempotency_key=item.idempotency_key,
                    expected_action_id=item.expected_action_id,
                    expected_member_ids=item.expected_member_ids,
                    reason=item.reason,
                )
                candidate = candidates[item.candidate_id]
                changed = self._apply_batch_decision(
                    candidate=candidate,
                    decision=decision,
                    bills=bills,
                    existing_actions=existing_actions,
                    latest_action_id=latest_action_ids.get(candidate.id, 0),
                    active_owners=active_owners,
                    matter_owners=matter_owners,
                    refund_bill_ids=refund_bill_ids,
                    seen_idempotency_keys=seen_idempotency_keys,
                    changed_bill_ids=changed_bill_ids,
                    action_writes=action_writes,
                )
                if changed:
                    candidate_updates.append({
                        "id": candidate.id,
                        "status": candidate.status,
                        "transfer_group_id": candidate.transfer_group_id,
                        "transfer_kind": candidate.transfer_kind,
                        "retained_bill_id": candidate.retained_bill_id,
                        "resolved_at": candidate.resolved_at,
                    })

            bill_updates = [{
                "id": bill_id,
                "aggregate_excluded": bills[bill_id].aggregate_excluded,
                "transfer_group_id": bills[bill_id].transfer_group_id,
                "duplicate_of_id": bills[bill_id].duplicate_of_id,
            } for bill_id in changed_bill_ids]
            self.mapper.save_batch(candidate_updates, bill_updates, action_writes)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.by_ids(candidate_ids)

    def _consolidate_and_commit(self) -> None:
        if self.consolidate_duplicates():
            self.db.commit()

    def consolidate_duplicates(self) -> bool:
        candidates = self.mapper.mergeable_duplicate_candidates()
        if not candidates:
            return False
        seed_ids = list(dict.fromkeys(
            bill_id
            for candidate in candidates
            for bill_id in candidate.member_bill_ids
        ))
        bills = self.mapper.duplicate_bills(seed_ids)
        components = self._duplicate_components(bills)
        bill_by_id = {bill.id: bill for bill in bills}

        groups: list[set[int]] = []
        grouped_candidates: list[list] = []
        for candidate in candidates:
            expanded: set[int] = set()
            for bill_id in candidate.member_bill_ids:
                expanded.update(components.get(bill_id, (bill_id,)))
            related = [index for index, bill_ids in enumerate(groups) if expanded & bill_ids]
            if not related:
                groups.append(expanded)
                grouped_candidates.append([candidate])
                continue
            target = related[0]
            groups[target].update(expanded)
            grouped_candidates[target].append(candidate)
            for index in reversed(related[1:]):
                groups[target].update(groups.pop(index))
                grouped_candidates[target].extend(grouped_candidates.pop(index))

        canonical_updates: list[dict] = []
        superseded_updates: list[dict] = []
        for bill_ids, group_candidates in zip(groups, grouped_candidates):
            members = sorted(
                (bill_by_id[bill_id] for bill_id in bill_ids if bill_id in bill_by_id),
                key=lambda bill: (bill.occurred_at, bill.id),
            )
            if len(members) < 2:
                continue
            canonical = group_candidates[0]
            member_ids = [member.id for member in members]
            fingerprint = "duplicate:" + ":".join(str(member_id) for member_id in member_ids)
            if (
                canonical.bill_id != member_ids[0]
                or canonical.related_bill_id != member_ids[1]
                or canonical.member_bill_ids != tuple(member_ids)
                or canonical.group_fingerprint != fingerprint
            ):
                canonical_updates.append({
                    "id": canonical.id,
                    "bill_id": member_ids[0],
                    "related_bill_id": member_ids[1],
                    "member_bill_ids": json.dumps(member_ids),
                    "group_fingerprint": fingerprint,
                })
            superseded_updates.extend({
                "id": duplicate.id,
                "status": "superseded_duplicate_group",
                "superseded_by_id": canonical.id,
                "group_fingerprint": fingerprint,
            } for duplicate in group_candidates[1:])
        self.mapper.update_duplicate_groups(canonical_updates, superseded_updates)
        return bool(canonical_updates or superseded_updates)

    def _apply_batch_decision(
        self,
        *,
        candidate: ReviewCommandCandidateVO,
        decision: CandidateDecision,
        bills: dict[int, ReviewCommandBillVO],
        existing_actions,
        latest_action_id: int,
        active_owners: dict[int, list[int]],
        matter_owners: dict[int, list[int]],
        refund_bill_ids: set[int],
        seen_idempotency_keys: set[str],
        changed_bill_ids: set[int],
        action_writes: list[CandidateActionWriteVO],
    ) -> bool:
        if decision.action == "confirm_third_party_transfer":
            raise ReviewCommandError(422, "代收代付不能整笔排除。请建立手工事项，填写往来对象并分配本金、回款和费用")

        request_payload = json.dumps(
            decision.model_dump(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if decision.idempotency_key:
            previous = existing_actions.get(decision.idempotency_key)
            if previous:
                if (
                    previous.candidate_id != candidate.id
                    or previous.request_payload != request_payload
                    or previous.undone
                ):
                    raise ReviewCommandError(409, "Idempotency key conflicts with another candidate decision or state")
                return False
            if decision.idempotency_key in seen_idempotency_keys:
                raise ReviewCommandError(409, "Idempotency key conflicts with another candidate decision or state")
            seen_idempotency_keys.add(decision.idempotency_key)

        if decision.expected_action_id is not None and decision.expected_action_id != latest_action_id:
            raise ReviewCommandError(409, "候选已发生变化，请重新打开后处理")
        if (
            decision.expected_member_ids is not None
            and set(decision.expected_member_ids) != set(candidate.member_bill_ids)
        ):
            raise ReviewCommandError(409, "候选成员已有变化，请重新核对全部流水后确认")
        if (
            candidate.status == "duplicate_excluded"
            and decision.action == "resolve_duplicate"
            and candidate.retained_bill_id == decision.retained_bill_id
        ):
            return False
        if candidate.status == "duplicate_rejected" and decision.action == "reject_duplicate":
            return False
        if candidate.status not in {"pending", "evidence_insufficient", "legacy_duplicate_needs_review"}:
            raise ReviewCommandError(409, "Candidate has already been handled; undo it before applying another decision")

        members = sorted(
            (bills[bill_id] for bill_id in candidate.member_bill_ids if bill_id in bills),
            key=lambda bill: (bill.occurred_at, bill.id),
        )
        first = bills.get(candidate.bill_id)
        second = bills.get(candidate.related_bill_id)
        if not first or not second or len(members) < 2:
            raise ReviewCommandError(409, "Candidate evidence is incomplete")

        if decision.action in {"confirm_transfer", "confirm_personal_transfer", "resolve_duplicate"}:
            member_ids = {bill.id for bill in members}
            matter_ids = sorted({
                matter_id
                for bill_id in member_ids
                for matter_id in matter_owners.get(bill_id, ())
            })
            if matter_ids:
                raise ReviewCommandError(409, f"流水已用于手工事项 {matter_ids}；请先修改或撤销相关分配")
            conflict_ids = sorted({
                owner_id
                for bill_id in member_ids
                for owner_id in active_owners.get(bill_id, ())
                if owner_id != candidate.id
            })
            if conflict_ids:
                raise ReviewCommandError(409, f"金额已用于候选 {conflict_ids[0]} 的有效决定；请先撤销或修改该决定")
            if member_ids & refund_bill_ids:
                raise ReviewCommandError(409, "流水涉及退款性质或有效退款分配，请先在退款记录中处理")

        before_state = self._command_snapshot(candidate, members)
        now = datetime.now()
        if decision.action in {"confirm_transfer", "confirm_personal_transfer"}:
            if candidate.candidate_type != "transfer":
                raise ReviewCommandError(422, "Only transfer candidates can be grouped as transfers")
            if cents(first.amount) == 0 or cents(first.amount) != -cents(second.amount):
                raise ReviewCommandError(422, "两笔转移必须同额反向；分次付款和手续费请使用手工事项分配")
            if not self._has_distinct_accounts(first, second):
                raise ReviewCommandError(422, "Transfer confirmation requires two distinct transaction accounts")
            candidate.transfer_group_id = f"transfer-{candidate.id}"
            candidate.transfer_kind = "personal"
            candidate.status = "personal_transfer_grouped"
            for bill in (first, second):
                bill.transfer_group_id = candidate.transfer_group_id
                bill.aggregate_excluded = True
                changed_bill_ids.add(bill.id)
        elif decision.action == "resolve_duplicate":
            if candidate.candidate_type != "duplicate":
                raise ReviewCommandError(422, "Only duplicate candidates can resolve a retained bill")
            if len({cents(bill.amount) for bill in members}) != 1:
                raise ReviewCommandError(422, "重复组的金额或方向不一致，请重新核对来源")
            if decision.retained_bill_id not in {bill.id for bill in members}:
                raise ReviewCommandError(422, "Select one of the duplicate-group bills to retain")
            candidate.retained_bill_id = decision.retained_bill_id
            candidate.status = "duplicate_excluded"
            for bill in members:
                if bill.id != decision.retained_bill_id:
                    bill.aggregate_excluded = True
                    bill.duplicate_of_id = decision.retained_bill_id
                    changed_bill_ids.add(bill.id)
        elif decision.action == "reject_duplicate":
            if candidate.candidate_type != "duplicate":
                raise ReviewCommandError(422, "Only duplicate candidates can reject a duplicate suggestion")
            candidate.status = "duplicate_rejected"
        else:
            candidate.status = decision.action

        candidate.resolved_at = now
        if candidate.status in {
            "duplicate_excluded",
            "personal_transfer_grouped",
            "third_party_transfer_grouped",
            "transfer_grouped",
            "legacy_transfer_excluded",
        }:
            for bill in members:
                active_owners.setdefault(bill.id, []).append(candidate.id)
        action_writes.append(CandidateActionWriteVO(
            candidate_id=candidate.id,
            action=decision.action,
            before_state=before_state,
            after_state=self._command_snapshot(candidate, members),
            actor="local-user",
            reason=decision.reason or candidate.reason,
            idempotency_key=decision.idempotency_key,
            request_payload=request_payload,
            created_at=now,
        ))
        return True

    @staticmethod
    def _command_snapshot(
        candidate: ReviewCommandCandidateVO,
        members: list[ReviewCommandBillVO],
    ) -> str:
        return json.dumps({
            "candidate": {
                "status": candidate.status,
                "transfer_group_id": candidate.transfer_group_id,
                "transfer_kind": candidate.transfer_kind,
                "retained_bill_id": candidate.retained_bill_id,
                "resolved_at": candidate.resolved_at.isoformat() if candidate.resolved_at else None,
            },
            "bills": {
                str(bill.id): {
                    "aggregate_excluded": bill.aggregate_excluded,
                    "transfer_group_id": bill.transfer_group_id,
                    "duplicate_of_id": bill.duplicate_of_id,
                } for bill in members
            },
        }, ensure_ascii=False)

    @staticmethod
    def _has_distinct_accounts(first: ReviewCommandBillVO, second: ReviewCommandBillVO) -> bool:
        unknown_accounts = {"", "未提供账户", "手工未提供账户"}
        return (
            first.account_name not in unknown_accounts
            and second.account_name not in unknown_accounts
            and first.account_name != second.account_name
        )

    @staticmethod
    def _duplicate_components(bills: tuple[DuplicateBillVO, ...]) -> dict[int, tuple[int, ...]]:
        by_key: dict[tuple[str, float], list[DuplicateBillVO]] = defaultdict(list)
        for bill in bills:
            by_key[(bill.merchant, bill.amount)].append(bill)
        result: dict[int, tuple[int, ...]] = {}
        for matches in by_key.values():
            components: list[list[DuplicateBillVO]] = []
            for bill in sorted(matches, key=lambda item: (item.occurred_at, item.id)):
                if not components or (bill.occurred_at - components[-1][-1].occurred_at).total_seconds() > 300:
                    components.append([bill])
                else:
                    components[-1].append(bill)
            for component in components:
                ids = tuple(item.id for item in component)
                for bill_id in ids:
                    result[bill_id] = ids
        return result

    @staticmethod
    def _read(candidate: ReviewCandidateVO) -> ReviewCandidateRead:
        return ReviewCandidateRead(
            id=candidate.id,
            current_action_id=candidate.current_action_id,
            candidate_type=candidate.candidate_type,
            confidence=candidate.confidence,
            reason=candidate.reason,
            status=candidate.status,
            member_bills=[bill_read_from_vo(bill) for bill in candidate.member_bills],
            transfer_group_id=candidate.transfer_group_id,
            transfer_kind=candidate.transfer_kind,
            retained_bill_id=candidate.retained_bill_id,
            resolved_at=candidate.resolved_at,
            undo_available=candidate.undo_available,
            aggregation_effect=_AGGREGATION_EFFECTS[candidate.status],
            created_at=candidate.created_at,
            bill=bill_read_from_vo(candidate.bill),
            related_bill=bill_read_from_vo(candidate.related_bill),
        )
