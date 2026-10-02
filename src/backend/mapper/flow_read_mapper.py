"""Canonical cash readers: one Ledger identity, explicit immutable relations."""
from sqlalchemy import case, func, or_, select

from backend.entity import (AutoTagRule, LedgerEntry, LedgerEntryTag, ReviewAllocation, ReviewCase,
    TransactionFact,
    Position, PositionLeg, ReviewLedgerPositionLegAllocation, TagAssignmentRequest, TargetTag, TargetTagView)
from backend.error import TargetEconomicError
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.bounded_query_mapper import page_rows, scan_rows
from backend.schema.flow_read import ECONOMIC_TYPES
from backend.schema.list_query import SorterExpression


def amount_grouped_request(request):
    """Implicit currency grouping is part of the effective sort/cursor hash."""
    if not any(item.key in ("cash_amount", "signed_cash_amount") for item in request.sorter):
        return request
    currency = next((item for item in request.sorter if item.key == "cash_currency_code"),
        SorterExpression(key="cash_currency_code", direction="asc"))
    return request.model_copy(update=dict(sorter=[currency] + [item for item in request.sorter if item.key != "cash_currency_code"]))


class FlowReadMapper:
    def __init__(self, db):
        self.db = db
        l, a, f, r = LedgerEntry, ReviewAllocation, TransactionFact, ReviewCase
        economic_type = case(*[(l.entry_type == code, name) for code, name in enumerate(ECONOMIC_TYPES)])
        direction = case((l.entry_direction == 1, "IN"), else_="OUT")
        signed = case((l.entry_direction == 1, l.amount), else_=-l.amount)
        self.columns = dict(id=l.id, occurred_time=l.occurred_time, economic_type=economic_type,
            cash_direction=direction, cash_currency_code=l.currency_code,
            cash_amount=l.amount, signed_cash_amount=signed, account_ref_id=l.account_ref_id, active=(r.status == 0))
        account = AccountManagementMapper(db)
        for dimension in ("account_id", "party_id"):
            def scope(op, value, dimension=dimension):
                predicate = l.account_ref_id.in_(account.ref_scope(dimension, value))
                return predicate if op == "=" else ~predicate
            self.columns[dimension] = scope

        def tag(op, value):
            predicate = select(LedgerEntryTag.id).join(TargetTag, TargetTag.id == LedgerEntryTag.tag_id).join(
                TargetTagView, TargetTagView.id == TargetTag.view_id).where(LedgerEntryTag.ledger_id == l.id,
                TargetTag.id == value, TargetTag.status == "ACTIVE", TargetTagView.status == "ACTIVE").correlate(l).exists()
            return predicate if op == "=" else ~predicate
        self.columns["tag_id"] = tag
        self.statement = select(l.id, l.entry_type, l.entry_direction, l.amount.label("cash_amount"),
            l.currency_code.label("cash_currency_code"), l.account_ref_id, l.occurred_time, l.created_time, l.updated_time,
            signed.label("signed_cash_amount"), economic_type.label("economic_type"), direction.label("cash_direction"),
            f.summary, f.counterparty_name.label("counterparty"), r.status.label("review_status"),
            a.review_id.label("review_id"), a.transaction_id.label("transaction_id")).select_from(l).join(a, a.ledger_id == l.id).join(
            f, f.id == a.transaction_id).join(r, r.id == a.review_id)

    def page(self, request):
        statement = self.statement.with_only_columns(*[column for column in self.statement.selected_columns
            if column.key not in ("summary", "counterparty")])
        return page_rows(self.db, statement, amount_grouped_request(request), self.columns,
            default=(("occurred_time", "desc"), ("id", "asc")))

    def search(self, request, project):
        return scan_rows(self.db, self.statement, amount_grouped_request(request), self.columns,
            scope="local:ledger-v1:flow", default=(("occurred_time", "desc"), ("id", "asc")), project=project)

    def get(self, ledger_id):
        row = self.db.execute(self.statement.where(LedgerEntry.id == ledger_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def originals(self, ledger_id):
        a, f, r = ReviewAllocation, TransactionFact, ReviewCase
        allocation = dict(self.db.execute(select(a.__table__).where(a.ledger_id == ledger_id)).mappings().one())
        fact = dict(self.db.execute(select(f.__table__).where(f.id == allocation["transaction_id"])).mappings().one())
        review = dict(self.db.execute(select(r.__table__).where(r.id == allocation["review_id"])).mappings().one())
        return allocation, fact, review

    def source_page(self, ledger_id, request):
        a, f, r = ReviewAllocation, TransactionFact, ReviewCase
        statement = select(*a.__table__.columns,
            *[column.label("fact__" + column.name) for column in f.__table__.columns],
            *[column.label("review__" + column.name) for column in r.__table__.columns]).select_from(a).join(
                f, f.id == a.transaction_id).join(r, r.id == a.review_id)
        return page_rows(self.db, statement, request, dict(id=a.id), condition=a.ledger_id == ledger_id)

    def positions(self, ledger_id):
        link, leg, position = ReviewLedgerPositionLegAllocation, PositionLeg, Position
        scope = select(link.position_leg_id).where(link.ledger_id == ledger_id)
        links = self.db.execute(select(link.__table__).where(link.ledger_id == ledger_id).order_by(link.id).limit(4001)).mappings().all()
        legs = self.db.execute(select(leg.__table__).where(leg.id.in_(scope)).order_by(leg.id).limit(4001)).mappings().all()
        position_scope = select(leg.position_id).where(leg.id.in_(scope))
        positions = self.db.execute(select(position.__table__).where(position.id.in_(position_scope)).order_by(position.id).limit(4001)).mappings().all()
        return [dict(row) for row in links], [dict(row) for row in legs], [dict(row) for row in positions]

    def position_page(self, ledger_id, request):
        link, leg, position, review = ReviewLedgerPositionLegAllocation, PositionLeg, Position, ReviewCase
        statement = select(*link.__table__.columns,
            *[column.label("leg__" + column.name) for column in leg.__table__.columns],
            *[column.label("position__" + column.name) for column in position.__table__.columns],
            *[column.label("review__" + column.name) for column in review.__table__.columns]).select_from(link).join(
                leg, leg.id == link.position_leg_id).join(position, position.id == leg.position_id).join(review, review.id == link.review_id)
        return page_rows(self.db, statement, request,
            dict(id=link.id, position_id=leg.position_id, position_leg_id=leg.id), condition=link.ledger_id == ledger_id)

    def ownership(self, ref_id):
        if not ref_id:
            return None, None, None
        manager = AccountManagementMapper(self.db)
        ref = dict(manager.public_get("ref", ref_id))
        account = dict(manager.public_get("account", ref["account_id"])) if ref["account_id"] else None
        party = dict(manager.public_get("party", account["party_id"])) if account else None
        return ref, account, party

    @staticmethod
    def tag_statement():
        relation, tag, view = LedgerEntryTag, TargetTag, TargetTagView
        return select(relation.id, relation.ledger_id, tag.id.label("tag_id"), view.id.label("view_id"),
            tag.name.label("tag_name"), tag.system_name.label("tag_system_name"), tag.status.label("tag_status"),
            view.name.label("view_name"), view.system_name.label("view_system_name"), view.status.label("view_status")
        ).select_from(relation).outerjoin(tag, tag.id == relation.tag_id).outerjoin(view, view.id == tag.view_id)

    def tag_rows(self, ledger_id):
        return [dict(row) for row in self.db.execute(self.tag_statement().where(
            LedgerEntryTag.ledger_id == ledger_id).order_by(LedgerEntryTag.id).limit(4001)).mappings()]

    def tag_page(self, ledger_id, request):
        return page_rows(self.db, self.tag_statement(), request,
            dict(id=LedgerEntryTag.id, view_id=TargetTagView.id, tag_id=TargetTag.id), condition=LedgerEntryTag.ledger_id == ledger_id)

    def tag_guard(self, ledger_id):
        relation, tag, view = LedgerEntryTag, TargetTag, TargetTagView
        orphan = select(relation.id).outerjoin(tag, tag.id == relation.tag_id).outerjoin(view, view.id == tag.view_id).where(
            relation.ledger_id == ledger_id, or_(tag.id.is_(None), view.id.is_(None),
                tag.status.not_in(("ACTIVE", "ARCHIVED")), view.status.not_in(("ACTIVE", "ARCHIVED"))))
        repeated = select(view.id).select_from(relation).join(tag, tag.id == relation.tag_id).join(view, view.id == tag.view_id).where(
            relation.ledger_id == ledger_id, tag.status == "ACTIVE", view.status == "ACTIVE").group_by(view.id).having(func.count(relation.id) > 1)
        default = select(tag.id).where(tag.view_id == view.id, tag.system_name == "unclassified",
            tag.status == "ACTIVE").correlate(view).exists()
        missing_default = select(view.id).where(view.status == "ACTIVE", ~default)
        if self.db.scalar(select(or_(orphan.exists(), repeated.exists(), missing_default.exists()))):
            raise TargetEconomicError(409, "tag relation is damaged", code="TAG_RELATION_BROKEN")

    def approved_sources(self, ledger_id, view_ids):
        # Group all ENABLED sources: only count==1 can prove a current source.
        # No latest-ID heuristic or ambiguous source-to-tag Cartesian expansion.
        request, rule, tag = TagAssignmentRequest, AutoTagRule, TargetTag
        result = {}
        ids = sorted(set(view_ids))
        for offset in range(0, len(ids), 400):
            statement = select(request.view_id, func.count(request.id).label("source_count"),
                func.max(request.id).label("request_id"), func.max(request.rule_id).label("rule_id"),
                func.max(request.rule_revision).label("rule_revision"), func.max(request.proposed_tag_id).label("tag_id"),
                func.max(rule.rule_revision).label("current_revision"), func.max(rule.view_id).label("rule_view_id"),
                func.max(case((or_(rule.id.is_(None), tag.id.is_(None)), 1), else_=0)).label("broken")
            ).select_from(request).outerjoin(rule, rule.id == request.rule_id).outerjoin(tag, tag.id == request.proposed_tag_id).where(
                request.ledger_id == ledger_id, request.status == 2, request.view_id.in_(ids[offset:offset + 400])).group_by(request.view_id)
            for row in self.db.execute(statement).mappings():
                if row["broken"]:
                    raise TargetEconomicError(409, "approved source relation is damaged", code="TAG_RELATION_BROKEN")
                result[row["view_id"]] = dict(row)
        return result
