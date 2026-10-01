"""Set-oriented persistence for immutable publication; no replay/history writes."""
from datetime import timedelta
from time import monotonic

from sqlalchemy import and_, case, func, insert, select, update
from sqlalchemy.orm import Session, aliased

from backend.entity import (AutoTagRule, LedgerAccount, LedgerAccountParty, LedgerAccountRef,
                            LedgerEntry, LedgerEntryTag, Position, PositionLeg, ReviewAllocation,
                            ReviewCase, ReviewLedgerPositionLegAllocation, TagAssignmentRequest,
                            TargetTag, TargetTagView, TransactionFact)
from backend.entity.base import utc_now
from backend.error import TargetEconomicError


def chunks(values, size=400):
    values = sorted(set(values))
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


class ReviewCommandMapper:
    def __init__(self, db: Session):
        self.db = db
        self.write_started = None

    def rows(self, entity, column, ids, *, limit=None):
        result = []
        for batch in chunks(ids):
            statement = select(entity.__table__).where(column.in_(batch)).order_by(entity.id)
            if limit is not None:
                statement = statement.limit(limit + 1 - len(result))
            result.extend(dict(row) for row in self.db.execute(
                statement
            ).mappings())
            if limit is not None and len(result) > limit:
                raise TargetEconomicError(413, "immutable relation budget exceeded; use paged relations", code="DETAIL_LIMIT")
        return result

    def named_rows(self, name, ids, key="id"):
        entities = {"facts": TransactionFact, "reviews": ReviewCase, "positions": Position,
                    "legs": PositionLeg, "refs": LedgerAccountRef, "accounts": LedgerAccount,
                    "parties": LedgerAccountParty, "tags": LedgerEntryTag, "requests": TagAssignmentRequest,
                    "rules": AutoTagRule}
        entity = entities[name]
        return self.rows(entity, getattr(entity, key), ids)

    def tag_dictionary(self):
        return [dict(row) for row in self.db.execute(select(
            TargetTag.id, TargetTag.view_id, TargetTag.system_name,
            TargetTag.updated_time, TargetTagView.updated_time.label("view_updated_time")
        ).join(TargetTagView, TargetTagView.id == TargetTag.view_id).where(
            TargetTag.status == "ACTIVE", TargetTagView.status == "ACTIVE"
        ).order_by(TargetTag.id)).mappings()]

    def allocations_for_facts(self, ids, *, active=False, defaults=False):
        result = []
        for batch in chunks(ids):
            statement = select(ReviewAllocation.__table__).join(
                ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id
            ).where(ReviewAllocation.transaction_fact_id.in_(batch))
            if active:
                statement = statement.where(ReviewCase.status == 0)
            if defaults:
                statement = statement.where(ReviewCase.behavior_type == 0)
            result.extend(dict(row) for row in self.db.execute(statement.order_by(ReviewAllocation.id)).mappings())
        return result

    def bundle(self, ids):
        reviews = self.rows(ReviewCase, ReviewCase.id, ids)
        allocations = self.rows(ReviewAllocation, ReviewAllocation.review_case_id, ids, limit=4000)
        legs = self.rows(PositionLeg, PositionLeg.review_id, ids, limit=4000)
        links = self.rows(ReviewLedgerPositionLegAllocation, ReviewLedgerPositionLegAllocation.review_id, ids, limit=4000)
        ledgers = self.rows(LedgerEntry, LedgerEntry.id, [row["ledger_id"] for row in allocations])
        return dict(reviews=reviews, allocations=allocations, ledger_entries=ledgers,
                    position_legs=legs, position_allocations=links)

    def dependents(self, ids):
        return self.rows(PositionLeg, PositionLeg.source_position_leg_id, ids, limit=4000)

    def impact_requests(self, ids):
        result = []
        for batch in chunks(ids):
            result.extend(dict(row) for row in self.db.execute(select(TagAssignmentRequest.__table__).where(
                TagAssignmentRequest.ledger_id.in_(batch), TagAssignmentRequest.status.in_((1, 2))
            ).order_by(TagAssignmentRequest.id).limit(50001 - len(result))).mappings())
            if len(result) > 50000:
                raise TargetEconomicError(413, "tag request effect exceeds budget", code="TAG_IMPACT_LIMIT")
        return result

    def source_consumption(self, ids, states):
        closing = [rid for rid, status in states.items() if status == 1]
        opening = [rid for rid, status in states.items() if status == 0]
        effective = case((ReviewCase.id.in_(closing), False), (ReviewCase.id.in_(opening), True), else_=ReviewCase.status == 0)
        result = []
        for batch in chunks(ids):
            result.extend(dict(row) for row in self.db.execute(select(
                PositionLeg.source_position_leg_id.label("source_id"), func.sum(PositionLeg.leg_amount).label("amount"),
                func.count().label("count"), func.max(PositionLeg.updated_time).label("leg_token"),
                func.max(ReviewCase.updated_time).label("review_token")
            ).join(ReviewCase, ReviewCase.id == PositionLeg.review_id).where(
                PositionLeg.source_position_leg_id.in_(batch), PositionLeg.leg_direction == "OUT", effective
            ).group_by(PositionLeg.source_position_leg_id)).mappings())
        return result

    def position_totals(self, ids, states=None):
        states = states or {}
        source_leg, source_review = aliased(PositionLeg), aliased(ReviewCase)
        def effective(review):
            return case((review.id.in_([rid for rid, value in states.items() if value == 1]), False),
                        (review.id.in_([rid for rid, value in states.items() if value == 0]), True), else_=review.status == 0)
        result = []
        for batch in chunks(ids):
            count = self.db.scalar(select(func.count()).select_from(PositionLeg).where(PositionLeg.position_id.in_(batch)))
            if count > 50000:
                raise TargetEconomicError(413, "quantity aggregation budget exceeded", code="AGGREGATION_LIMIT")
            active = effective(ReviewCase)
            invalid = and_(active, PositionLeg.source_position_leg_id > 0, ~effective(source_review))
            result.extend(dict(row) for row in self.db.execute(select(
                PositionLeg.position_id, func.count().label("evidence_count"),
                func.sum(case((active, case((PositionLeg.leg_direction == "IN", PositionLeg.leg_amount), else_=-PositionLeg.leg_amount)), else_=0)).label("quantity"),
                func.sum(case((invalid, 1), else_=0)).label("invalid_sources"),
                func.max(PositionLeg.id).label("last_leg_id"),
                func.max(ReviewCase.updated_time).label("review_token"),
                func.max(source_review.updated_time).label("source_review_token")
            ).join(ReviewCase, ReviewCase.id == PositionLeg.review_id).outerjoin(
                source_leg, source_leg.id == PositionLeg.source_position_leg_id).outerjoin(
                source_review, source_review.id == source_leg.review_id).where(PositionLeg.position_id.in_(batch))
                .group_by(PositionLeg.position_id)).mappings())
        return result

    def begin_write(self):
        if self.db.new or self.db.dirty or self.db.deleted:
            raise TargetEconomicError(409, "pending writes cannot be included in a Review command", code="WRITE_CONTEXT_INVALID")
        # Preview and command are separate requests, never promote an old read snapshot.
        self.db.rollback()
        connection = self.db.connection()
        connection.exec_driver_sql("PRAGMA busy_timeout=2000")
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        self.write_started = monotonic()
        connection.connection.driver_connection.set_progress_handler(
            lambda: int(monotonic() - self.write_started > 2), 1000
        )
        self.driver = connection.connection.driver_connection

    def end_write(self):
        if getattr(self, "driver", None) is not None:
            self.driver.set_progress_handler(None, 0)
            self.driver = None

    def publish(self, drafts, facts, states, existing_reviews, *, fault=None):
        now = utc_now()
        reviews = [ReviewCase(behavior_type=draft["type"], status=0, title=draft["title"],
                              created_time=now, updated_time=now) for draft in drafts]
        positions = [[Position(**position, status="ACTIVE", created_time=now, updated_time=now)
                      for position in draft["new_positions"]] for draft in drafts]
        self.db.add_all(reviews + [row for group in positions for row in group])
        self.db.flush()
        if fault:
            fault("identities")
        ledger_groups, leg_groups = [], []
        for draft, review, position_group in zip(drafts, reviews, positions):
            ledgers = []
            for split in draft["allocations"]:
                fact = facts[split["transaction_id"]]
                ledgers.append(LedgerEntry(entry_type=split["entry_type"], entry_direction=fact["cash_direction"],
                    amount=split["cash_amount"], currency_code=fact["currency_code"],
                    account_ref_id=split["account_ref_id"], account_code=fact["account_code"],
                    counterparty_account_ref=fact["counterparty_account_ref"], occurred_time=fact["occurred_time"],
                    created_time=now, updated_time=now))
            legs = [PositionLeg(position_id=(leg["existing_position_id"] if leg["existing_position_id"] is not None
                     else position_group[leg["new_position_index"]].id), review_id=review.id,
                     type=leg["type"], leg_amount=leg["leg_amount"], leg_direction=leg["leg_direction"],
                     occurred_time=leg["occurred_time"], source_position_leg_id=leg["source"], basis=leg["basis"],
                     created_time=now, updated_time=now) for leg in draft["legs"]]
            ledger_groups.append(ledgers)
            leg_groups.append(legs)
        self.db.add_all([row for group in ledger_groups + leg_groups for row in group])
        self.db.flush()
        if fault:
            fault("outputs")
        allocations, links = [], []
        for draft, review, ledgers, legs in zip(drafts, reviews, ledger_groups, leg_groups):
            allocations.extend(dict(review_id=review.id, transaction_id=split["transaction_id"], ledger_id=ledger.id,
                cash_amount=split["cash_amount"], cash_currency_code=ledger.currency_code, created_time=now, updated_time=now)
                for split, ledger in zip(draft["allocations"], ledgers))
            links.extend(dict(review_id=review.id, ledger_id=ledgers[link["allocation_index"]].id,
                position_leg_id=legs[link["leg_index"]].id, cash_amount=link["cash_amount"],
                cash_currency_code=link["cash_currency_code"], created_time=now, updated_time=now)
                for link in draft["position_allocations"])
        for table, rows in ((ReviewAllocation.__table__, allocations), (ReviewLedgerPositionLegAllocation.__table__, links)):
            for offset in range(0, len(rows), 400):
                self.db.execute(insert(table), rows[offset:offset + 400])
        updates = [{"id": review_id, "status": status,
                    "updated_time": max(now, existing_reviews[review_id]["updated_time"] + timedelta(microseconds=1))}
                   for review_id, status in states.items() if existing_reviews[review_id]["status"] != status]
        if updates:
            self.db.execute(update(ReviewCase), updates)
        if fault:
            fault("relations")
        return reviews, positions, ledger_groups, leg_groups

    def create_initial_defaults(self, fact_ids, *, account_refs=None):
        """Only called with the truly new Fact IDs inside the intake transaction."""
        facts = {row["id"]: row for row in self.rows(TransactionFact, TransactionFact.id, fact_ids)}
        if len(facts) != len(set(fact_ids)) or self.allocations_for_facts(fact_ids):
            raise TargetEconomicError(409, "initial default requires new unallocated Facts", code="DEFAULT_IDENTITY_REQUIRED")
        account_refs = account_refs or {}
        drafts = [dict(type=0, title="", allocations=[dict(transaction_id=fact_id, entry_type=0,
                       cash_amount=facts[fact_id]["amount"], account_ref_id=account_refs.get(fact_id, 0))],
                       new_positions=[], legs=[], position_allocations=[]) for fact_id in sorted(facts)]
        return self.publish(drafts, facts, {}, {})
