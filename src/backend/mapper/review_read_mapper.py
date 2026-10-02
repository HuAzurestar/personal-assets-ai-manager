"""Bounded original Review relations; never an alternate publication path."""
from sqlalchemy import case, select
from backend.entity import ReviewCase, ReviewAllocation, LedgerEntry, Position, PositionLeg, ReviewLedgerPositionLegAllocation
from backend.mapper.bounded_query_mapper import page_rows, scan_rows
from backend.schema.review_query import REVIEW_TYPES


class ReviewReadMapper:
    def __init__(self, db):
        self.db = db
        r = ReviewCase
        type_column = case(*[(r.behavior_type == code, name) for code, name in enumerate(REVIEW_TYPES)])
        status_column = case((r.status == 0, "CONFIRMED"), else_="REVOKED")
        self.columns = dict(id=r.id, type=type_column, status=status_column, created_time=r.created_time, updated_time=r.updated_time)
        self.statement = select(*r.__table__.columns, type_column.label("type"), status_column.label("public_status"))

    def page(self, request):
        return page_rows(self.db, self.statement, request, self.columns, default=(("updated_time", "desc"), ("id", "asc")))

    def search(self, request, project):
        return scan_rows(self.db, self.statement, request, self.columns, scope="local:ledger-v1:review",
            default=(("updated_time", "desc"), ("id", "asc")), project=project)

    def get(self, review_id):
        row = self.db.execute(select(*ReviewCase.__table__.columns).where(ReviewCase.id == review_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    @staticmethod
    def relation_statement(review_id, kind):
        a, l, leg, link, p = ReviewAllocation, LedgerEntry, PositionLeg, ReviewLedgerPositionLegAllocation, Position
        if kind == "allocation":
            return select(*a.__table__.columns).where(a.review_id == review_id), a.id
        if kind == "flow":
            return select(*l.__table__.columns).where(l.id.in_(select(a.ledger_id).where(a.review_id == review_id))), l.id
        if kind == "position_leg":
            return select(*leg.__table__.columns, p.unit_code).join(p, p.id == leg.position_id).where(leg.review_id == review_id), leg.id
        if kind == "position_allocation":
            return select(*link.__table__.columns).where(link.review_id == review_id), link.id
        if kind == "position":
            return select(*p.__table__.columns).where(p.id.in_(select(leg.position_id).where(leg.review_id == review_id))), p.id
        raise ValueError("unsupported internal Review relation")

    def relations(self, review_id, kind):
        statement, identity = self.relation_statement(review_id, kind)
        return [dict(row) for row in self.db.execute(statement.order_by(identity).limit(4001)).mappings()]

    def relation_page(self, review_id, kind, request):
        statement, identity = self.relation_statement(review_id, kind)
        return page_rows(self.db, statement, request, dict(id=identity))
