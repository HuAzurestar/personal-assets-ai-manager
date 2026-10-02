"""Snapshot guards for canonical ledger reads and Review publication."""
from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from backend.entity import (LedgerAccount, LedgerAccountParty, LedgerAccountRef, LedgerEntry,
                            Position, PositionLeg, ReviewAllocation, ReviewCase,
                            ReviewLedgerPositionLegAllocation, TransactionFact)
from backend.error import TargetEconomicError
from backend.core.unit import SUPPORTED_UNIT_CODES


class TrustedRelationMapper:
    def __init__(self, db: Session):
        self.db = db

    def read_snapshot(self):
        connection = self.db.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")

    def validate(self):
        a, l, f, r = ReviewAllocation, LedgerEntry, TransactionFact, ReviewCase
        invalid = select(a.id).outerjoin(l, l.id == a.ledger_entry_id).outerjoin(
            f, f.id == a.transaction_fact_id).outerjoin(r, r.id == a.review_case_id).where(or_(
                l.id.is_(None), f.id.is_(None), r.id.is_(None), a.amount <= 0,
                a.amount != l.amount, a.currency_code != l.currency_code,
                a.currency_code != f.currency_code, l.entry_direction != f.cash_direction,
                l.occurred_time != f.occurred_time, l.entry_type.not_in((0, 1, 2, 3)),
                f.cash_direction.not_in((1, 2)), f.amount <= 0, r.status.not_in((0, 1)),
                r.behavior_type.not_in((0, 1, 2, 3, 4)),
            )).limit(1)
        uncovered = select(l.id).outerjoin(a, a.ledger_entry_id == l.id).group_by(l.id).having(func.count(a.id) != 1).limit(1)
        ref, account, party = LedgerAccountRef, LedgerAccount, LedgerAccountParty
        broken_account = select(l.id).outerjoin(ref, ref.id == l.account_ref_id).outerjoin(
            account, account.id == ref.account_id).outerjoin(party, party.id == account.party_id).where(or_(
                and_(l.account_ref_id > 0, ref.id.is_(None)),
                and_(ref.account_id > 0, account.id.is_(None)),
                and_(account.id.is_not(None), party.id.is_(None)),
            )).limit(1)
        orphan_account = select(account.id).outerjoin(party, party.id == account.party_id).where(or_(
            party.id.is_(None), account.status.not_in(("ACTIVE", "CLOSED")),
            account.statement_interval_months < 0, account.snapshot_interval_months < 0,
        )).limit(1)
        orphan_ref = select(ref.id).outerjoin(account, account.id == ref.account_id).where(or_(
            ref.account_id < 0, and_(ref.account_id > 0, account.id.is_(None)),
            ref.status.not_in(("ACTIVE", "CLOSED")), ref.identity_strength.not_in((0, 1, 2)),
            and_(ref.identity_strength == 1, or_(ref.source_namespace == "", ref.source_identity == "")),
        )).limit(1)
        invalid_party = select(party.id).where(party.status.not_in(("ACTIVE", "CLOSED"))).limit(1)
        leg, position, link = PositionLeg, Position, ReviewLedgerPositionLegAllocation
        orphan_position = select(position.id).outerjoin(party, party.id == position.party_id).where(or_(
            party.id.is_(None), position.type.not_in(("ASSET", "LIABILITY")),
            position.status.not_in(("ACTIVE", "ARCHIVED", "SETTLED")),
            position.unit_code.not_in(sorted(SUPPORTED_UNIT_CODES)),
            position.usage_scenario.not_in(("GENERAL", "PERSONAL-LENDING", "SHARED-SETTLEMENT", "STORED-VALUE",
                "DEPOSIT-PLEDGE", "REIMBURSEMENT", "CREDIT-CARD", "FORMAL-LOAN", "INVESTMENT")),
        )).limit(1)
        broken_leg = select(leg.id).outerjoin(position, position.id == leg.position_id).outerjoin(
            r, r.id == leg.review_id).outerjoin(party, party.id == position.party_id).where(or_(
                position.id.is_(None), r.id.is_(None), party.id.is_(None), leg.leg_amount <= 0,
                leg.leg_amount > 9_000_000_000_000, leg.leg_direction.not_in(("IN", "OUT")),
                leg.type.not_in(("OPENING", "MOVEMENT")), position.type.not_in(("ASSET", "LIABILITY")),
                position.status.not_in(("ACTIVE", "ARCHIVED", "SETTLED")),
                and_(leg.leg_direction == "OUT", leg.source_position_leg_id == 0),
                and_(leg.leg_direction == "IN", leg.source_position_leg_id != 0),
            )).limit(1)
        broken_link = select(link.id).outerjoin(leg, leg.id == link.position_leg_id).outerjoin(
            l, l.id == link.ledger_id).outerjoin(a, a.ledger_entry_id == l.id).where(or_(
                leg.id.is_(None), l.id.is_(None), a.id.is_(None), link.review_id != leg.review_id,
                link.review_id != a.review_case_id, link.cash_currency_code != l.currency_code,
                link.cash_amount <= 0, link.cash_amount > l.amount, l.entry_type == 3,
            )).limit(1)
        over = select(link.ledger_id).join(l, l.id == link.ledger_id).group_by(link.ledger_id).having(
            func.sum(link.cash_amount) > func.max(l.amount)).limit(1)
        source = aliased(PositionLeg)
        broken_source = select(leg.id).outerjoin(source, source.id == leg.source_position_leg_id).where(or_(
            and_(leg.source_position_leg_id > 0, source.id.is_(None)),
            and_(leg.source_position_leg_id > 0, or_(source.position_id != leg.position_id, source.leg_direction != "IN")),
            and_(leg.source_position_leg_id > 0, leg.source_position_leg_id >= leg.id),
        )).limit(1)
        probes = [(invalid, "RELATION_BROKEN"), (uncovered, "RELATION_BROKEN"),
                  (broken_account, "ACCOUNT_RELATION_BROKEN"), (orphan_account, "ACCOUNT_RELATION_BROKEN"),
                  (orphan_ref, "ACCOUNT_RELATION_BROKEN"), (invalid_party, "ACCOUNT_RELATION_BROKEN"),
                  (orphan_position, "RELATION_BROKEN"), (broken_leg, "RELATION_BROKEN"),
                  (broken_link, "RELATION_BROKEN"), (over, "RELATION_BROKEN"), (broken_source, "RELATION_BROKEN")]
        code = self.db.scalar(union_all(*[
            select(literal(code).label("code")).where(probe.exists()) for probe, code in probes
        ]).limit(1))
        if code is not None:
            raise TargetEconomicError(409, "账务引用或金额关系损坏", code=code)
