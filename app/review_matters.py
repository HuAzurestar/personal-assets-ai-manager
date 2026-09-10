"""Manual money allocation foundation. No candidate inference lives here."""
import json
from datetime import datetime
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import Bill, RefundAllocation, RefundDesignation, ReviewMatter, ReviewMatterRevision
from app.money import cents, money

Role = Literal["expense", "income", "receivable", "payable", "repayment_received", "repayment_paid", "transfer"]
ROLE_LABELS = {"expense": "本人支出/费用", "income": "本人收入", "receivable": "替人垫付/借出", "payable": "代管款/借入", "repayment_received": "收回垫款/本金", "repayment_paid": "归还代管款/本金", "transfer": "本人账户转移"}


class MatterLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bill_id: int = Field(gt=0)
    amount: float = Field(gt=0)
    role: Role
    party: str = Field(default="", max_length=120)

    @field_validator("amount")
    @classmethod
    def exact_amount(cls, value):
        cents(value)
        return value


class MatterWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    scenarios: list[str] = Field(default_factory=list, max_length=12)
    lines: list[MatterLine] = Field(min_length=1, max_length=100)
    own_accounts_confirmed: bool = False
    expected_version: int = Field(default=0, ge=0)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("请填写事项名称")
        return value.strip()

    @field_validator("scenarios")
    @classmethod
    def clean_scenarios(cls, values):
        if any(not value.strip() or len(value) > 40 for value in values):
            raise ValueError("场景标签需为 1–40 个字符")
        return list(dict.fromkeys(value.strip() for value in values))


class MatterUndo(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)


def current_matters(db: Session) -> list[dict]:
    revisions = db.scalars(select(ReviewMatterRevision).join(ReviewMatter, (ReviewMatter.id == ReviewMatterRevision.matter_id) & (ReviewMatter.version == ReviewMatterRevision.version)).order_by(ReviewMatterRevision.matter_id)).all()
    return [dict(json.loads(row.snapshot), id=row.matter_id, version=row.version, action=row.action) for row in revisions if row.action != "revoke"]


def allocated_bills(db: Session, except_matter: int | None = None) -> dict[int, int]:
    totals = {}
    for matter in current_matters(db):
        if matter["id"] == except_matter:
            continue
        for line in matter["lines"]:
            totals[line["bill_id"]] = totals.get(line["bill_id"], 0) + line["amount_cents"]
    return totals


def assert_no_matters(db: Session, bill_ids: set[int]) -> None:
    conflicts = [matter["id"] for matter in current_matters(db) if any(line["bill_id"] in bill_ids for line in matter["lines"])]
    if conflicts:
        raise HTTPException(409, f"流水已用于手工事项 {conflicts}；请先修改或撤销相关分配")


def validate_matter(db: Session, payload: MatterWrite, matter_id: int | None) -> dict:
    used = allocated_bills(db, matter_id)
    incoming = {"income", "payable", "repayment_received"}
    outgoing = {"expense", "receivable", "repayment_paid"}
    balances = {}
    lines = []
    transfer_total = 0
    transfer_accounts = set()
    for submitted in payload.lines:
        bill = db.get(Bill, submitted.bill_id)
        if not bill:
            raise HTTPException(404, f"流水 {submitted.bill_id} 不存在")
        if bill.aggregate_excluded:
            raise HTTPException(409, f"流水 {bill.id} 已被重复或转移决定占用；请先撤销该决定")
        # Refunds have a separate nature/relationship lifecycle. Do not silently
        # mix an AA responsibility split with the old whole-expense refund path.
        refund = db.scalar(select(RefundAllocation.id).where(RefundAllocation.status == "confirmed", (RefundAllocation.refund_bill_id == bill.id) | (RefundAllocation.expense_bill_id == bill.id)))
        if db.get(RefundDesignation, bill.id) or refund:
            raise HTTPException(409, f"流水 {bill.id} 涉及退款。当前不能将退款与往来分摊混合，请在退款记录中处理")
        amount = cents(submitted.amount)
        used[bill.id] = used.get(bill.id, 0) + amount
        if used[bill.id] > abs(cents(bill.amount)):
            raise HTTPException(409, f"流水 {bill.id} 分配超出可用金额；同一部分不能重复使用")
        if (submitted.role in incoming and bill.amount <= 0) or (submitted.role in outgoing and bill.amount >= 0):
            raise HTTPException(422, f"流水 {bill.id} 的收付方向与“{ROLE_LABELS[submitted.role]}”不符")
        party = submitted.party.strip()
        if submitted.role in {"receivable", "payable", "repayment_received", "repayment_paid"}:
            if not party:
                raise HTTPException(422, "往来分配必须填写同一个往来对象名称")
            kind = "receivable" if submitted.role in {"receivable", "repayment_received"} else "payable"
            key = (kind, party)
            balances[key] = balances.get(key, 0) + (amount if submitted.role in {"receivable", "payable"} else -amount)
        if submitted.role == "transfer":
            if not payload.own_accounts_confirmed:
                raise HTTPException(422, "请明确确认转移涉及的账户都属于本人")
            if bill.account_name in {"", "未提供账户", "手工未提供账户"}:
                raise HTTPException(422, f"流水 {bill.id} 缺少账户，请先修订账户")
            transfer_accounts.add(bill.account_name)
            transfer_total += amount if bill.amount > 0 else -amount
        lines.append({"bill_id": bill.id, "amount_cents": amount, "role": submitted.role, "party": party})
    if transfer_accounts and (len(transfer_accounts) < 2 or transfer_total != 0):
        raise HTTPException(422, "本人转移需至少两个不同账户且分配收付金额相等；手续费请另列为本人费用")
    if any(balance < 0 for balance in balances.values()):
        raise HTTPException(422, "结算超过该事项和往来对象的应收/应付余额；请核对分配，多余款项另行解释")
    return {"title": payload.title, "scenarios": payload.scenarios, "lines": lines, "own_accounts_confirmed": payload.own_accounts_confirmed, "balances": [{"kind": key[0], "party": key[1], "amount_cents": value} for key, value in sorted(balances.items())]}


def matter_read(db: Session, matter_id: int) -> dict:
    matter = db.get(ReviewMatter, matter_id)
    if not matter:
        raise HTTPException(404, "事项不存在")
    revisions = db.scalars(select(ReviewMatterRevision).where(ReviewMatterRevision.matter_id == matter_id).order_by(ReviewMatterRevision.version)).all()
    current = next(row for row in revisions if row.version == matter.version)
    snapshot = json.loads(current.snapshot)
    lines = []
    for line in snapshot["lines"]:
        bill = db.get(Bill, line["bill_id"])
        lines.append({**line, "amount": money(line["amount_cents"]), "merchant": bill.merchant, "account_name": bill.account_name, "occurred_at": bill.occurred_at, "bill_amount": bill.amount})
    return {**snapshot, "lines": lines, "id": matter.id, "version": matter.version, "status": "revoked" if current.action == "revoke" else "confirmed", "history": [{"version": row.version, "action": row.action, "actor": row.actor, "reason": row.reason, "created_at": row.created_at, "snapshot": json.loads(row.snapshot)} for row in revisions]}


def register_matter_routes(app, get_db):
    def begin(db):
        try:
            db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as error:
            db.rollback()
            raise HTTPException(409, "账本正在写入，请稍后重试") from error

    def save(db, payload, matter_id=None, revoke=False):
        begin(db)
        request_payload = json.dumps({"id": matter_id, "revoke": revoke, **payload.model_dump()}, ensure_ascii=False, sort_keys=True)
        previous = db.scalar(select(ReviewMatterRevision).where(ReviewMatterRevision.idempotency_key == payload.idempotency_key))
        if previous:
            if previous.request_payload != request_payload or db.get(ReviewMatter, previous.matter_id).version != previous.version:
                raise HTTPException(409, "请求已被修改或事项已有后续操作，请刷新后重试")
            db.commit()
            return matter_read(db, previous.matter_id)
        matter = db.get(ReviewMatter, matter_id) if matter_id else None
        if matter_id and not matter:
            raise HTTPException(404, "事项不存在")
        if payload.expected_version != (matter.version if matter else 0):
            raise HTTPException(409, "事项已被其他操作修改，请重新打开最新版本")
        if revoke:
            old = db.scalar(select(ReviewMatterRevision).where(ReviewMatterRevision.matter_id == matter_id, ReviewMatterRevision.version == matter.version))
            if old.action == "revoke":
                raise HTTPException(409, "事项已撤销")
            snapshot = json.loads(old.snapshot)
        else:
            snapshot = validate_matter(db, payload, matter_id)
        if matter:
            matter.version += 1
        else:
            matter = ReviewMatter(version=1, created_at=datetime.now())
            db.add(matter)
            db.flush()
        db.add(ReviewMatterRevision(matter_id=matter.id, version=matter.version, action="revoke" if revoke else "confirm", snapshot=json.dumps(snapshot, ensure_ascii=False, sort_keys=True), reason=payload.reason, actor="local-user", idempotency_key=payload.idempotency_key, request_payload=request_payload, created_at=datetime.now()))
        db.commit()
        return matter_read(db, matter.id)

    @app.get("/api/review-matters")
    def list_matters(db: Session = Depends(get_db)):
        return [matter_read(db, matter_id) for matter_id in db.scalars(select(ReviewMatter.id).order_by(ReviewMatter.id.desc())).all()]

    @app.get("/api/review-matters/{matter_id}")
    def get_matter(matter_id: int, db: Session = Depends(get_db)):
        return matter_read(db, matter_id)

    @app.post("/api/review-matters", status_code=201)
    def create_matter(payload: MatterWrite, db: Session = Depends(get_db)):
        return save(db, payload)

    @app.put("/api/review-matters/{matter_id}")
    def replace_matter(matter_id: int, payload: MatterWrite, db: Session = Depends(get_db)):
        return save(db, payload, matter_id)

    @app.post("/api/review-matters/{matter_id}/undo")
    def undo_matter(matter_id: int, payload: MatterUndo, db: Session = Depends(get_db)):
        return save(db, payload, matter_id, revoke=True)
