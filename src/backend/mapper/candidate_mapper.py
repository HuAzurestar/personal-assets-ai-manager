"""All accepted Fact candidates, current complete coverage and original default IDs."""
from sqlalchemy import select, func, case, and_
from backend.entity import TransactionFact, ReviewAllocation, ReviewCase, LedgerEntry
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.bounded_query_mapper import page_rows, scan_rows
from backend.core.import_public_text import masked_summary
from backend.error import TargetEconomicError

MAX_CURRENT_REVIEW_SUMMARIES = 4000


class CandidateMapper:
    def __init__(self, db):
        self.db = db
        fact, allocation, review, ledger = TransactionFact, ReviewAllocation, ReviewCase, LedgerEntry
        def scalar(column, *, default=False):
            query = select(column).select_from(allocation).join(review, review.id == allocation.review_case_id)
            query = query.where(allocation.transaction_fact_id == fact.id)
            query = query.where(review.behavior_type == 0) if default else query.where(review.status == 0)
            return query.correlate(fact).scalar_subquery()
        allocated = func.coalesce(scalar(func.sum(allocation.amount)), 0)
        default_count = scalar(func.count(allocation.id), default=True)
        default_review = scalar(func.max(allocation.review_case_id), default=True)
        default_ledger = scalar(func.max(allocation.ledger_entry_id), default=True)
        current_refs = select(func.count(func.distinct(ledger.account_ref_id))).select_from(allocation).join(
            review, review.id == allocation.review_case_id).join(ledger, ledger.id == allocation.ledger_entry_id)
        current_refs = current_refs.where(allocation.transaction_fact_id == fact.id, review.status == 0).correlate(fact)
        refs_count = current_refs.scalar_subquery()
        ref_id = case((refs_count == 1, current_refs.with_only_columns(func.max(ledger.account_ref_id)).scalar_subquery()), else_=0)
        coverage_state = case((allocated == fact.amount, "FULL"), (allocated == 0, "UNRESOLVED"), else_="PARTIAL")
        direction = case((fact.cash_direction == 1, "IN"), else_="OUT")
        self.columns = dict(id=fact.id, occurred_time=fact.occurred_time, cash_direction=direction,
            cash_currency_code=fact.currency_code, coverage_state=coverage_state)
        account_mapper = AccountManagementMapper(db)
        def account_condition(dimension):
            def condition(op, value):
                statement = select(allocation.id).join(review, review.id == allocation.review_case_id).join(
                    ledger, ledger.id == allocation.ledger_entry_id).where(
                    allocation.transaction_fact_id == fact.id, review.status == 0)
                if dimension == "account_ref_id" and value == 0:
                    statement = statement.where(ledger.account_ref_id == 0)
                else:
                    statement = statement.where(ledger.account_ref_id.in_(account_mapper.ref_scope(dimension, value)))
                exists = statement.correlate(fact).exists()
                return exists if op == "=" else ~exists
            return condition
        for dimension in ("account_ref_id", "account_id", "party_id"):
            self.columns[dimension] = account_condition(dimension)
        self.statement = select(fact.id, fact.occurred_time, direction.label("cash_direction"),
            fact.amount.label("cash_amount"), fact.currency_code.label("cash_currency_code"),
            fact.summary, fact.counterparty_name.label("counterparty"), allocated.label("allocated_cash_amount"),
            refs_count.label("ref_count"), ref_id.label("account_ref_id"), default_count.label("default_count"),
            default_review.label("default_review_id"), default_ledger.label("default_ledger_id"),
            coverage_state.label("coverage_state"))

    def page(self, request, *, review_id=None):
        condition = True if review_id is None else TransactionFact.id.in_(select(ReviewAllocation.transaction_id)
            .where(ReviewAllocation.review_id == review_id))
        return page_rows(self.db, self.statement, request, self.columns, condition=condition,
            default=(("id", "asc"),) if review_id is not None else (("occurred_time", "desc"), ("id", "asc")))

    def review_exists(self, review_id):
        return self.db.scalar(select(ReviewCase.id).where(ReviewCase.id == review_id)) is not None

    def current_reviews(self, rows):
        """One bounded page-wide read; counts cover whole groups, not this page.

        The nested ID set stays in SQL; at most 100 page Fact IDs enter IN.
        Multiple splits within one group produce one summary per Fact/Review.
        """
        ids = [row['id'] for row in rows]
        if not ids:
            return []
        a, r = ReviewAllocation, ReviewCase
        affected = select(a.review_id).join(r,r.id == a.review_id).where(a.transaction_id.in_(ids),r.status == 0)
        members = select(a.review_id,func.count(func.distinct(a.transaction_id)).label('member_count')).where(
            a.review_id.in_(affected)).group_by(a.review_id).subquery()
        statement = select(a.transaction_id,r.id,r.behavior_type,r.status,r.title,r.created_time,r.updated_time,
            members.c.member_count,func.sum(a.amount).label('allocated_cash_amount')).join(r,r.id == a.review_id).join(
                members,members.c.review_id == a.review_id).where(a.transaction_id.in_(ids),r.status == 0).group_by(
                    a.transaction_id,r.id).order_by(a.transaction_id,r.id).limit(MAX_CURRENT_REVIEW_SUMMARIES + 1)
        result = [dict(row) for row in self.db.execute(statement).mappings()]
        if len(result) > MAX_CURRENT_REVIEW_SUMMARIES:
            raise TargetEconomicError(413,'current Review summaries exceed page budget; use a smaller page',code='DETAIL_LIMIT')
        return result

    def search(self, request):
        return scan_rows(self.db, self.statement, request, self.columns, scope="local:ledger-v1:candidate",
            default=(("occurred_time", "desc"), ("id", "asc")),
            project=lambda row: row | dict(summary=masked_summary(row["summary"]),counterparty=masked_summary(row["counterparty"])))
