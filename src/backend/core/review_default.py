"""One proven immutable original default, shared by Review and import plans."""
from collections import defaultdict

from backend.error import TargetEconomicError


def original_defaults(facts, allocations, bundle):
    by_fact, by_review = defaultdict(list), defaultdict(list)
    for allocation in allocations:
        by_fact[allocation["transaction_id"]].append(allocation)
    for allocation in bundle["allocations"]:
        by_review[allocation["review_id"]].append(allocation)
    reviews = {row["id"]: row for row in bundle["reviews"]}
    ledgers = {row["id"]: row for row in bundle["ledger_entries"]}
    leg_reviews = {row["review_id"] for row in bundle["position_legs"]}
    defaults, refs = {}, {}
    for fact_id, fact in facts.items():
        candidates = by_fact[fact_id]
        if len(candidates) != 1:
            raise TargetEconomicError(409, "Fact must have one proven original default", code="DEFAULT_IDENTITY_REQUIRED")
        allocation = candidates[0]
        review, ledger = reviews.get(allocation["review_id"]), ledgers.get(allocation["ledger_id"])
        if (review is None or review["behavior_type"] != 0 or ledger is None or
                len(by_review[allocation["review_id"]]) != 1 or allocation["review_id"] in leg_reviews or
                ledger["entry_type"] != 0 or allocation["cash_amount"] != fact["amount"]):
            raise TargetEconomicError(409, "original default structure is damaged", code="DEFAULT_IDENTITY_REQUIRED")
        defaults[fact_id], refs[fact_id] = allocation["review_id"], ledger["account_ref_id"]
    return defaults, refs
