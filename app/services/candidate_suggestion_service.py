from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.mappers.candidate_suggestion_mapper import CandidateSuggestionMapper
from app.money import cents
from app.schemas.review import (
    CandidateSuggestionBillVO,
    CandidateSuggestionWriteVO,
    PendingDuplicateSuggestionVO,
)


class CandidateSuggestionService:
    """Generate review suggestions from preloaded fact evidence without SQL loops."""

    def __init__(self, db: Session):
        self.mapper = CandidateSuggestionMapper(db)

    def generate(self, bill_ids: list[int]) -> int:
        target_ids = list(dict.fromkeys(bill_ids))
        if not target_ids:
            return 0
        targets = self.mapper.target_bills(target_ids)
        nearby = self.mapper.nearby_bills(target_ids)
        bills_by_id = {bill.id: bill for bill in (*targets, *nearby)}
        target_by_id = {bill.id: bill for bill in targets}

        duplicate_keys = list(dict.fromkeys(
            (target.merchant, target.amount)
            for target in targets
            if any(
                other.merchant == target.merchant
                and cents(other.amount) == cents(target.amount)
                for other in nearby
            )
        ))
        duplicate_bills = self.mapper.duplicate_bills(duplicate_keys)
        bills_by_id.update({bill.id: bill for bill in duplicate_bills})
        pending = list(self.mapper.pending_duplicates())
        updates: dict[int, PendingDuplicateSuggestionVO] = {}
        writes: list[CandidateSuggestionWriteVO] = []
        new_duplicate_writes: list[CandidateSuggestionWriteVO] = []
        seen_transfer_pairs: set[tuple[int, int]] = set()

        for target_id in target_ids:
            target = target_by_id.get(target_id)
            if not target:
                continue
            matches = [
                other
                for other in bills_by_id.values()
                if other.id != target.id and self._matches(target, other)
            ]
            duplicate_matches = [
                other for other in matches
                if other.merchant == target.merchant
                and cents(other.amount) == cents(target.amount)
            ]
            if duplicate_matches:
                members = self._duplicate_component(target, bills_by_id.values())
                if len(members) >= 2:
                    self._merge_or_create_duplicate(
                        members,
                        pending,
                        updates,
                        writes,
                        new_duplicate_writes,
                    )
                    continue

            for other in matches:
                if cents(target.amount) * cents(other.amount) >= 0:
                    continue
                pair = tuple(sorted((target.id, other.id)))
                if pair in seen_transfer_pairs:
                    continue
                seen_transfer_pairs.add(pair)
                distinct_accounts = self._has_distinct_accounts(target, other)
                writes.append(CandidateSuggestionWriteVO(
                    candidate_type="transfer",
                    bill_id=target.id,
                    related_bill_id=other.id,
                    member_bill_ids="",
                    group_fingerprint="",
                    confidence=0.78 if distinct_accounts else 0.42,
                    reason=(
                        "5 分钟内同额反向、不同账户；可确认个人账户间转移"
                        if distinct_accounts
                        else "5 分钟内同额反向，但缺少两个不同账户证据；仅供人工核验，不能自动认定为个人转移"
                    ),
                    status="pending" if distinct_accounts else "evidence_insufficient",
                    created_at=datetime.now(),
                ))

        self.mapper.save(list(updates.values()), writes)
        return len(writes)

    @staticmethod
    def _matches(first: CandidateSuggestionBillVO, second: CandidateSuggestionBillVO) -> bool:
        return (
            abs((first.occurred_at - second.occurred_at).total_seconds()) <= 300
            and abs(abs(cents(first.amount)) - abs(cents(second.amount))) <= 1
        )

    @staticmethod
    def _duplicate_component(target, bills) -> list[CandidateSuggestionBillVO]:
        matching = sorted(
            (
                bill for bill in bills
                if bill.merchant == target.merchant
                and cents(bill.amount) == cents(target.amount)
            ),
            key=lambda bill: (bill.occurred_at, bill.id),
        )
        components: list[list[CandidateSuggestionBillVO]] = []
        for bill in matching:
            if (
                not components
                or (bill.occurred_at - components[-1][-1].occurred_at).total_seconds() > 300
            ):
                components.append([bill])
            else:
                components[-1].append(bill)
        return next(
            (component for component in components if any(bill.id == target.id for bill in component)),
            [target],
        )

    @staticmethod
    def _merge_or_create_duplicate(
        members: list[CandidateSuggestionBillVO],
        pending: list[PendingDuplicateSuggestionVO],
        updates: dict[int, PendingDuplicateSuggestionVO],
        writes: list[CandidateSuggestionWriteVO],
        new_writes: list[CandidateSuggestionWriteVO],
    ) -> None:
        member_ids = tuple(bill.id for bill in members)
        member_set = set(member_ids)
        reason = f"{len(members)} 笔金额、交易方一致且相邻时间不超过 5 分钟；作为同一重复候选组处理"
        existing = next(
            (candidate for candidate in pending if member_set & set(candidate.member_bill_ids)),
            None,
        )
        if existing:
            existing.bill_id = member_ids[0]
            existing.related_bill_id = member_ids[1]
            existing.member_bill_ids = member_ids
            existing.reason = reason
            if existing.id is not None:
                updates[existing.id] = existing
            return
        new = next(
            (
                candidate for candidate in new_writes
                if member_set & set(json.loads(candidate.member_bill_ids))
            ),
            None,
        )
        if new:
            new.bill_id = member_ids[0]
            new.related_bill_id = member_ids[1]
            new.member_bill_ids = json.dumps(member_ids)
            new.group_fingerprint = "duplicate:" + ":".join(str(item) for item in member_ids)
            new.reason = reason
            return
        created = CandidateSuggestionWriteVO(
            candidate_type="duplicate",
            bill_id=member_ids[0],
            related_bill_id=member_ids[1],
            member_bill_ids=json.dumps(member_ids),
            group_fingerprint="duplicate:" + ":".join(str(item) for item in member_ids),
            confidence=0.92,
            reason=reason,
            status="pending",
            created_at=datetime.now(),
        )
        writes.append(created)
        new_writes.append(created)

    @staticmethod
    def _has_distinct_accounts(first, second) -> bool:
        unknown = {"", "未提供账户", "手工未提供账户"}
        return (
            first.account_name not in unknown
            and second.account_name not in unknown
            and first.account_name != second.account_name
        )
