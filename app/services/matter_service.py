from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import MatterCommandError
from app.mappers.matter_mapper import MatterMapper
from app.money import cents, money
from app.schemas.matter import (
    MatterBalanceRead,
    MatterHistoryRead,
    MatterLineRead,
    MatterPageRead,
    MatterRead,
    MatterSummaryRead,
    MatterUndo,
    MatterWrite,
)


ROLE_LABELS = {
    "expense": "本人支出/费用",
    "income": "本人收入",
    "receivable": "替人垫付/借出",
    "payable": "代管款/借入",
    "repayment_received": "收回垫款/本金",
    "repayment_paid": "归还代管款/本金",
    "transfer": "本人账户转移",
}


class MatterService:
    def __init__(self, db: Session):
        self.db = db
        self.mapper = MatterMapper(db)

    def list(self) -> list[MatterRead]:
        return self._read(self.mapper.matters())

    def page(self, *, page: int, page_size: int) -> MatterPageRead:
        total, matters = self.mapper.page(page=page, page_size=page_size)
        items = []
        for matter in matters:
            items.append(MatterSummaryRead(
                id=matter.matter_id,
                version=matter.version,
                title=matter.title,
                scenarios=json.loads(matter.scenarios),
                own_accounts_confirmed=matter.own_accounts_confirmed,
                balances=[
                    MatterBalanceRead(**balance)
                    for balance in json.loads(matter.balances)
                ],
                status="revoked" if matter.action == "revoke" else "confirmed",
                line_count=matter.line_count,
            ))
        return MatterPageRead(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def get(self, matter_id: int) -> MatterRead:
        matters = self.mapper.matters([matter_id])
        if not matters:
            raise MatterCommandError(404, "事项不存在")
        return self._read(matters)[0]

    def create(self, payload: MatterWrite) -> MatterRead:
        return self._save(payload, matter_id=None, revoke=False)

    def replace(self, matter_id: int, payload: MatterWrite) -> MatterRead:
        return self._save(payload, matter_id=matter_id, revoke=False)

    def undo(self, matter_id: int, payload: MatterUndo) -> MatterRead:
        return self._save(payload, matter_id=matter_id, revoke=True)

    def allocated_bills(self, except_matter: int | None = None) -> dict[int, int]:
        totals: dict[int, int] = {}
        for matter in self.current_snapshots():
            if matter["id"] == except_matter or matter["action"] == "revoke":
                continue
            for line in matter["lines"]:
                bill_id = line["bill_id"]
                totals[bill_id] = totals.get(bill_id, 0) + line["amount_cents"]
        return totals

    def conflicting_matter_ids(self, bill_ids: set[int]) -> list[int]:
        return [
            matter["id"]
            for matter in self.current_snapshots()
            if matter["action"] != "revoke"
            and any(line["bill_id"] in bill_ids for line in matter["lines"])
        ]

    def _save(
        self,
        payload: MatterWrite | MatterUndo,
        *,
        matter_id: int | None,
        revoke: bool,
    ) -> MatterRead:
        self.mapper.begin_immediate()
        try:
            request_payload = json.dumps(
                {"id": matter_id, "revoke": revoke, **payload.model_dump()},
                ensure_ascii=False,
                sort_keys=True,
            )
            previous = self.mapper.revision_by_idempotency_key(payload.idempotency_key)
            if previous:
                current_matter = self.mapper.matter(previous.matter_id)
                if (
                    previous.request_payload != request_payload
                    or not current_matter
                    or current_matter.version != previous.version
                ):
                    raise MatterCommandError(409, "请求已被修改或事项已有后续操作，请刷新后重试")
                self.db.commit()
                return self.get(previous.matter_id)

            matter = self.mapper.matter(matter_id) if matter_id else None
            if matter_id and not matter:
                raise MatterCommandError(404, "事项不存在")
            if payload.expected_version != (matter.version if matter else 0):
                raise MatterCommandError(409, "事项已被其他操作修改，请重新打开最新版本")

            if revoke:
                current = next(
                    (
                        row for row in self.mapper.current_revisions()
                        if row.matter_id == matter_id
                    ),
                    None,
                )
                if not current:
                    raise MatterCommandError(500, "事项当前版本缺少审计快照")
                if current.action == "revoke":
                    raise MatterCommandError(409, "事项已撤销")
                snapshot = json.loads(current.snapshot)
            else:
                snapshot = self._validate(payload, matter_id)

            now = datetime.now()
            if matter:
                version = self.mapper.advance_version(matter.id, matter.version)
                target_id = matter.id
            else:
                created = self.mapper.create_matter(created_at=now)
                target_id = created.id
                version = created.version
            self.mapper.append_revision(
                matter_id=target_id,
                version=version,
                action="revoke" if revoke else "confirm",
                snapshot=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                request_payload=request_payload,
                created_at=now,
            )
            self.db.commit()
            return self.get(target_id)
        except Exception:
            self.db.rollback()
            raise

    def _validate(self, payload: MatterWrite, matter_id: int | None) -> dict:
        used = self.allocated_bills(matter_id)
        bill_ids = list(dict.fromkeys(line.bill_id for line in payload.lines))
        bills = self.mapper.bills(bill_ids)
        for bill_id in bill_ids:
            if bill_id not in bills:
                raise MatterCommandError(404, f"流水 {bill_id} 不存在")
        refund_bill_ids = self.mapper.refund_bill_ids(bill_ids)

        incoming = {"income", "payable", "repayment_received"}
        outgoing = {"expense", "receivable", "repayment_paid"}
        balances: dict[tuple[str, str], int] = {}
        lines = []
        transfer_total = 0
        transfer_accounts = set()
        for submitted in payload.lines:
            bill = bills[submitted.bill_id]
            if bill.aggregate_excluded:
                raise MatterCommandError(409, f"流水 {bill.id} 已被重复或转移决定占用；请先撤销该决定")
            if bill.id in refund_bill_ids:
                raise MatterCommandError(409, f"流水 {bill.id} 涉及退款。当前不能将退款与往来分摊混合，请在退款记录中处理")
            amount = cents(submitted.amount)
            used[bill.id] = used.get(bill.id, 0) + amount
            if used[bill.id] > abs(cents(bill.amount)):
                raise MatterCommandError(409, f"流水 {bill.id} 分配超出可用金额；同一部分不能重复使用")
            if (
                (submitted.role in incoming and bill.amount <= 0)
                or (submitted.role in outgoing and bill.amount >= 0)
            ):
                raise MatterCommandError(422, f"流水 {bill.id} 的收付方向与“{ROLE_LABELS[submitted.role]}”不符")
            party = submitted.party.strip()
            if submitted.role in {"receivable", "payable", "repayment_received", "repayment_paid"}:
                if not party:
                    raise MatterCommandError(422, "往来分配必须填写同一个往来对象名称")
                kind = "receivable" if submitted.role in {"receivable", "repayment_received"} else "payable"
                key = (kind, party)
                balances[key] = balances.get(key, 0) + (
                    amount if submitted.role in {"receivable", "payable"} else -amount
                )
            if submitted.role == "transfer":
                if not payload.own_accounts_confirmed:
                    raise MatterCommandError(422, "请明确确认转移涉及的账户都属于本人")
                if bill.account_name in {"", "未提供账户", "手工未提供账户"}:
                    raise MatterCommandError(422, f"流水 {bill.id} 缺少账户，请先修订账户")
                transfer_accounts.add(bill.account_name)
                transfer_total += amount if bill.amount > 0 else -amount
            lines.append({
                "bill_id": bill.id,
                "amount_cents": amount,
                "role": submitted.role,
                "party": party,
            })
        if transfer_accounts and (len(transfer_accounts) < 2 or transfer_total != 0):
            raise MatterCommandError(422, "本人转移需至少两个不同账户且分配收付金额相等；手续费请另列为本人费用")
        if any(balance < 0 for balance in balances.values()):
            raise MatterCommandError(422, "结算超过该事项和往来对象的应收/应付余额；请核对分配，多余款项另行解释")
        return {
            "title": payload.title,
            "scenarios": payload.scenarios,
            "lines": lines,
            "own_accounts_confirmed": payload.own_accounts_confirmed,
            "balances": [
                {"kind": key[0], "party": key[1], "amount_cents": value}
                for key, value in sorted(balances.items())
            ],
        }

    def _read(self, matters) -> list[MatterRead]:
        if not matters:
            return []
        matter_ids = [matter.id for matter in matters]
        revisions = self.mapper.revisions(matter_ids)
        current_by_matter = {}
        snapshots = {}
        bill_ids = []
        for matter in matters:
            current = next(
                (row for row in revisions.get(matter.id, ()) if row.version == matter.version),
                None,
            )
            if not current:
                raise MatterCommandError(500, f"事项 {matter.id} 当前版本缺少审计快照")
            snapshot = json.loads(current.snapshot)
            current_by_matter[matter.id] = current
            snapshots[matter.id] = snapshot
            bill_ids.extend(line["bill_id"] for line in snapshot["lines"])
        bills = self.mapper.bills(list(dict.fromkeys(bill_ids)))

        result = []
        for matter in matters:
            current = current_by_matter[matter.id]
            snapshot = snapshots[matter.id]
            lines = []
            for line in snapshot["lines"]:
                bill = bills.get(line["bill_id"])
                if not bill:
                    raise MatterCommandError(500, f"事项 {matter.id} 引用的流水 {line['bill_id']} 不存在")
                lines.append(MatterLineRead(
                    **line,
                    amount=money(line["amount_cents"]),
                    merchant=bill.merchant,
                    account_name=bill.account_name,
                    occurred_at=bill.occurred_at,
                    bill_amount=bill.amount,
                ))
            history = [MatterHistoryRead(
                version=row.version,
                action=row.action,
                actor=row.actor,
                reason=row.reason,
                created_at=row.created_at,
                snapshot=json.loads(row.snapshot),
            ) for row in revisions[matter.id]]
            result.append(MatterRead(
                title=snapshot["title"],
                scenarios=snapshot["scenarios"],
                lines=lines,
                own_accounts_confirmed=snapshot["own_accounts_confirmed"],
                balances=[MatterBalanceRead(**balance) for balance in snapshot["balances"]],
                id=matter.id,
                version=matter.version,
                status="revoked" if current.action == "revoke" else "confirmed",
                history=history,
            ))
        return result

    def current_snapshots(self) -> list[dict]:
        return [
            {
                **json.loads(row.snapshot),
                "id": row.matter_id,
                "version": row.version,
                "action": row.action,
            }
            for row in self.mapper.current_revisions()
        ]
