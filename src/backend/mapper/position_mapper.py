"""Independent Position metadata/read adapter over the same publication tables."""
from datetime import timedelta
from dataclasses import asdict
import hashlib
from sqlalchemy import select, update, func
from sqlalchemy.orm import aliased
from backend.entity import Position, PositionLeg, ReviewCase, ReviewLedgerPositionLegAllocation, LedgerAccountParty
from backend.entity.base import utc_now
from backend.core.unit import unit_definition
from backend.error import TargetEconomicError
from backend.mapper.review_command_mapper import ReviewCommandMapper, chunks
from backend.mapper.bounded_query_mapper import page_rows, scan_rows, canonical, query_budget


class PositionMapper(ReviewCommandMapper):
    columns = {column.name: column for column in Position.__table__.columns}

    def get(self, position_id):
        row = self.db.execute(select(Position.__table__).where(Position.id == position_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def party(self, party_id):
        return self.db.execute(select(LedgerAccountParty.__table__).where(LedgerAccountParty.id == party_id)).mappings().one_or_none()

    def create(self, values):
        now = utc_now()
        row = Position(**values, status="ACTIVE", created_time=now, updated_time=now)
        self.db.add(row)
        self.db.flush()
        return self.get(row.id)

    def edit(self, row, values):
        values["updated_time"] = max(utc_now(), row["updated_time"] + timedelta(microseconds=1))
        self.db.execute(update(Position).where(Position.id == row["id"]).values(**values))
        return self.get(row["id"])

    def page(self, request):
        return page_rows(self.db, select(Position.__table__), request, self.columns)

    def search(self, request):
        return scan_rows(self.db, select(Position.__table__), request, self.columns, scope="local:financial-v1:position")

    def quantity(self, row):
        """One bounded provenance stream; token includes every Review/source status."""
        source = aliased(PositionLeg)
        source_review = aliased(ReviewCase)
        with query_budget(self.db, seconds=2, code="AGGREGATION_LIMIT"):
            count = self.db.scalar(select(func.count()).select_from(PositionLeg).where(PositionLeg.position_id == row["id"]))
            if count > 50000:
                raise TargetEconomicError(413, "Position quantity contribution budget exceeded", code="AGGREGATION_LIMIT")
            statement = select(PositionLeg.id, PositionLeg.leg_amount, PositionLeg.leg_direction,
                PositionLeg.source_position_leg_id, PositionLeg.updated_time, ReviewCase.id.label("review_id"),
                ReviewCase.status, ReviewCase.updated_time.label("review_updated_time"),
                source.position_id.label("source_position_id"), source.leg_direction.label("source_direction"),
                source_review.id.label("source_review_id"), source_review.status.label("source_status"),
                source_review.updated_time.label("source_review_updated_time"))
            result = self.db.execute(statement.join(ReviewCase, ReviewCase.id == PositionLeg.review_id).outerjoin(
                source, source.id == PositionLeg.source_position_leg_id).outerjoin(
                source_review, source_review.id == source.review_id).where(PositionLeg.position_id == row["id"])
                .order_by(PositionLeg.id)).mappings()
            token = hashlib.sha256(canonical(dict(position=row, unit=asdict(unit_definition(row["unit_code"])))).encode())
            quantity, evidence, invalid = 0, 0, False
            while batch := result.fetchmany(400):
                for leg in batch:
                    evidence += 1
                    token.update(canonical(dict(leg)).encode())
                    if leg["status"] == 0:
                        quantity += leg["leg_amount"] * (1 if leg["leg_direction"] == "IN" else -1)
                        if leg["source_position_leg_id"] and leg["source_status"] != 0:
                            invalid = True
            if abs(quantity) > 9_000_000_000_000:
                raise TargetEconomicError(413, "Position quantity exceeds exact output range", code="AGGREGATION_LIMIT")
            state = "UNKNOWN" if not evidence else "NEEDS_REVIEW" if invalid else "KNOWN"
            return dict(quantity_state=state, quantity=quantity if state == "KNOWN" else None,
                cost_state="NEEDS_REVIEW" if invalid else "UNKNOWN", source_token=token.hexdigest())

    def leg_page(self, position_id, request):
        columns = {column.name: column for column in PositionLeg.__table__.columns}
        rows, total = page_rows(self.db, select(PositionLeg.__table__), request, columns,
            condition=PositionLeg.position_id == position_id, default=(("occurred_time", "asc"), ("id", "asc")))
        links, reviews = self.leg_relations(rows)
        return rows, total, links, reviews

    def leg_search(self, position_id, request):
        columns = {column.name: column for column in PositionLeg.__table__.columns}
        result = scan_rows(self.db, select(PositionLeg.__table__), request, columns,
            scope=f"local:financial-v1:position:{position_id}:leg", condition=PositionLeg.position_id == position_id,
            default=(("occurred_time", "asc"), ("id", "asc")))
        links, reviews = self.leg_relations(result["items"])
        return result, links, reviews

    def leg_relations(self, rows):
        ids = [row["id"] for row in rows]
        links = self.rows(ReviewLedgerPositionLegAllocation, ReviewLedgerPositionLegAllocation.position_leg_id, ids, limit=4000)
        reviews = self.named_rows("reviews", [row["review_id"] for row in rows])
        return links, reviews
