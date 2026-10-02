"""All accepted Fact candidates, current complete coverage and original default IDs."""
from sqlalchemy import select, func, case, and_
from backend.entity import TransactionFact, ReviewAllocation, ReviewCase, LedgerEntry
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.bounded_query_mapper import page_rows, scan_rows
from backend.core.import_public_text import masked_summary


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

    def page(self, request):
        return page_rows(self.db, self.statement, request, self.columns, default=(("occurred_time", "desc"), ("id", "asc")))

    def search(self, request):
        return scan_rows(self.db, self.statement, request, self.columns, scope="local:ledger-v1:candidate",
            default=(("occurred_time", "desc"), ("id", "asc")),
            project=lambda row: row | dict(summary=masked_summary(row["summary"]),counterparty=masked_summary(row["counterparty"])))
