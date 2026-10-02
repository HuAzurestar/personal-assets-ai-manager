"""Pure expansion and validation against a supplied snapshot; never SQL/network."""
from collections import Counter, defaultdict

from backend.core.unit import unit_definition
from backend.error import TargetEconomicError
from backend.schema.review_command import CaseReviewInput


ECONOMIC_IDS = {"TRANSACTION": 0, "ACCOUNT_TRANSFER": 1, "ASSET_LIABILITY": 2, "DUPLICATE": 3}
REVIEW_TYPES = {"NORMAL": 4, "REFUND": 4, "INTERNAL_TRANSFER": 4, "DUPLICATE": 4,
                "BORROW_REPAY": 1, "SHARED_PAYMENT": 3, "POS_OPENING": 4, "POS_POSITION_OPEN": 4,
                "POS_POSITION_SETTLE": 4, "POS_CREDIT_PURCHASE": 2, "POS_CREDIT_REPAY": 2}


def reject(code, message, *, status=422):
    raise TargetEconomicError(status, message, code=code)


def raw_draft(intent):
    core = intent.parameters if isinstance(intent, CaseReviewInput) else intent
    return dict(type=REVIEW_TYPES[intent.case_code], title=intent.title, case_code=intent.case_code,
                new_positions=[row.model_dump() for row in core.new_positions],
                allocations=[{**row.model_dump(), "entry_type": ECONOMIC_IDS[row.economic_type]} for row in core.allocations],
                legs=[row.model_dump() for row in core.legs],
                position_allocations=[row.model_dump() for row in core.position_allocations],
                transaction_ids=list(core.transaction_ids) if isinstance(intent, CaseReviewInput) else [],
                phase=core.phase if isinstance(intent, CaseReviewInput) else None,
                account_bindings=[row.model_dump() for row in intent.account_bindings],
                duplicate_transactions=[row.model_dump() for row in intent.duplicate_transactions])


def expand(raw, facts, positions, refs, parties, sources, source_reviews, default_refs,
           *, defer_duplicate_source=False):
    draft = {**raw, "allocations": [dict(row) for row in raw["allocations"]]}
    code = draft["case_code"]
    bindings = {row["transaction_id"]: row["account_ref_id"] for row in draft["account_bindings"]}
    if len(bindings) != len(draft["account_bindings"]):
        reject("INVALID_ACCOUNT_BINDING", "duplicate binding")
    duplicate_ids = {row["transaction_id"] for row in draft["duplicate_transactions"]}
    if len(duplicate_ids) != len(draft["duplicate_transactions"]):
        reject("INVALID_DUPLICATE", "duplicate evidence selection")
    allowed_binding_ids = duplicate_ids | set(draft["transaction_ids"])
    if set(bindings) - allowed_binding_ids:
        reject("INVALID_ACCOUNT_BINDING", "binding is unrelated to this preset")
    if draft["transaction_ids"]:
        if draft["allocations"] or code not in {"NORMAL", "REFUND", "INTERNAL_TRANSFER", "DUPLICATE"}:
            reject("INVALID_CASE_CODE", "this preset requires explicit splits")
        if len(set(draft["transaction_ids"])) != len(draft["transaction_ids"]):
            reject("INVALID_REVIEW", "duplicate Fact selection")
        entry_type = {"NORMAL": 0, "REFUND": 0, "INTERNAL_TRANSFER": 1, "DUPLICATE": 3}[code]
        for fact_id in draft["transaction_ids"]:
            if fact_id not in facts:
                reject("FACT_NOT_FOUND", "unknown Fact", status=409)
            draft["allocations"].append(dict(transaction_id=fact_id, economic_type=list(ECONOMIC_IDS)[entry_type],
                entry_type=entry_type, cash_amount=facts[fact_id]["amount"],
                account_ref_id=bindings.get(fact_id, default_refs.get(fact_id, 0))))
    # A Position preset may include B evidence, but it must be an explicit full duplicate split.
    existing = {row["transaction_id"] for row in draft["allocations"]}
    for duplicate in draft["duplicate_transactions"]:
        fact_id, kept_id = duplicate["transaction_id"], duplicate["kept_transaction_id"]
        if fact_id not in facts or kept_id not in facts or fact_id == kept_id or fact_id not in bindings:
            reject("INVALID_DUPLICATE", "two accepted Facts and explicit B binding are required")
        excluded, kept = facts[fact_id], facts[kept_id]
        kept_ref = default_refs.get(kept_id, 0)
        if not defer_duplicate_source and (not kept_ref or bindings[fact_id] == kept_ref):
            reject("INVALID_DUPLICATE", "unknown or same source cannot be marked cross-source duplicate")
        if any(excluded[key] != kept[key] for key in ("amount", "currency_code", "cash_direction", "occurred_time")):
            reject("INVALID_DUPLICATE", "duplicate evidence does not match kept Fact")
        if fact_id not in existing:
            draft["allocations"].append(dict(transaction_id=fact_id, entry_type=3, economic_type="DUPLICATE",
                cash_amount=excluded["amount"], account_ref_id=bindings[fact_id]))
    sums = Counter()
    for split in draft["allocations"]:
        fact_id = split["transaction_id"]
        if fact_id not in facts:
            reject("FACT_NOT_FOUND", "unknown Fact", status=409)
        sums[fact_id] += split["cash_amount"]
        if split["account_ref_id"] and (split["account_ref_id"] not in refs or refs[split["account_ref_id"]]["status"] != "ACTIVE"):
            reject("ACCOUNT_RELATION_BROKEN", "unknown or inactive source ref", status=409)
        if split["entry_type"] == 3 and (fact_id not in duplicate_ids or split["account_ref_id"] != bindings.get(fact_id)):
            reject("INVALID_DUPLICATE", "duplicate needs explicit kept evidence and source binding")
        if fact_id in duplicate_ids and split["entry_type"] != 3:
            reject("INVALID_DUPLICATE", "B cannot also contribute cash")
    if any(amount != facts[fact_id]["amount"] for fact_id, amount in sums.items()):
        reject("FACT_COVERAGE_REQUIRED", "each selected Fact must be fully allocated in this Review")
    if not sums and not draft["legs"]:
        reject("INVALID_REVIEW", "Review has no money or quantity evidence")
    if code in {"NORMAL", "REFUND", "INTERNAL_TRANSFER", "DUPLICATE"}:
        expected = {"NORMAL": 0, "REFUND": 0, "INTERNAL_TRANSFER": 1, "DUPLICATE": 3}[code]
        if draft["legs"] or draft["new_positions"] or draft["position_allocations"] or any(row["entry_type"] != expected for row in draft["allocations"]):
            reject("INVALID_CASE_CODE", "cash-only preset does not match its output structure")
    for position in draft["new_positions"]:
        if position["party_id"] not in parties or parties[position["party_id"]]["status"] != "ACTIVE":
            reject("POSITION_PARTY_REQUIRED", "Position requires an active managed person")
        unit_definition(position["unit_code"])
    used_new = set()
    leg_positions = []
    for leg in draft["legs"]:
        if leg["existing_position_id"] is not None:
            position = positions.get(leg["existing_position_id"])
            if position is None:
                reject("REFERENCE_NOT_FOUND", "Position not found", status=404)
            if position["status"] != "ACTIVE":
                reject("POSITION_NOT_ACTIVE", "new leg requires ACTIVE Position", status=409)
        else:
            index = leg["new_position_index"]
            if index >= len(draft["new_positions"]):
                reject("INVALID_POSITION_REFERENCE", "new Position index out of range")
            position = draft["new_positions"][index]
            used_new.add(index)
        leg_positions.append(position)
        if leg["leg_direction"] == "IN" and leg["source"]:
            reject("INVALID_POSITION_SOURCE", "IN records new evidence and must not reference an old source")
        if leg["type"] == "OPENING" and (leg["source"] or leg["leg_direction"] != "IN"):
            reject("INVALID_POSITION_SOURCE", "opening is IN without a source leg")
        if leg["leg_direction"] == "OUT" and not leg["source"]:
            reject("POSITION_SOURCE_REQUIRED", "OUT needs explicit original source evidence")
        if leg["source"]:
            source = sources.get(leg["source"])
            if source is None or source["leg_direction"] != "IN" or source["position_id"] != leg["existing_position_id"]:
                reject("INVALID_POSITION_SOURCE", "source must be an IN leg of this Position")
            source_review = source_reviews.get(source["review_id"])
            if source_review is None or source_review["status"] != 0:
                reject("POSITION_SOURCE_INVALID", "source is not effective", status=409)
    if used_new != set(range(len(draft["new_positions"]))):
        reject("INVALID_POSITION_REFERENCE", "new Position must participate in this Review")
    totals = Counter()
    pairs = set()
    linked = defaultdict(list)
    for link in draft["position_allocations"]:
        ai, li = link["allocation_index"], link["leg_index"]
        if ai >= len(draft["allocations"]) or li >= len(draft["legs"]) or (ai, li) in pairs:
            reject("INVALID_POSITION_ALLOCATION", "invalid or repeated relation indices")
        pairs.add((ai, li))
        split = draft["allocations"][ai]
        fact = facts[split["transaction_id"]]
        if split["entry_type"] == 3:
            reject("INVALID_POSITION_ALLOCATION", "duplicate cannot have a quantity cash link")
        if link["cash_currency_code"] != fact["currency_code"]:
            reject("UNIT_MISMATCH", "cash link currency differs from its Ledger")
        totals[ai] += link["cash_amount"]
        linked[li].append((split, fact, link))
    if any(amount > draft["allocations"][index]["cash_amount"] for index, amount in totals.items()):
        reject("LEDGER_POSITION_ALLOCATION_OVERFLOW", "linked cash exceeds Ledger")
    if code == "POS_OPENING" and (draft["allocations"] or any(leg["type"] != "OPENING" for leg in draft["legs"])):
        reject("INVALID_CASE_CODE", "opening must be cash-free opening evidence")
    if code in {"POS_POSITION_OPEN", "POS_POSITION_SETTLE"}:
        if not draft["legs"] and any(row["entry_type"] != 3 for row in draft["allocations"]):
            reject("INVALID_CASE_CODE", "Position event requires quantity evidence")
        direction = "IN" if code == "POS_POSITION_OPEN" else "OUT"
        if any(leg["leg_direction"] != direction for leg in draft["legs"]):
            reject("INVALID_CASE_CODE", "Position event direction mismatch")
    if code in {"BORROW_REPAY", "SHARED_PAYMENT", "POS_CREDIT_PURCHASE", "POS_CREDIT_REPAY"}:
        if not draft["legs"]:
            reject("INVALID_CASE_CODE", "preset requires explicit Position evidence")
        for index, (leg, position) in enumerate(zip(draft["legs"], leg_positions)):
            unit = unit_definition(position["unit_code"])
            cash_links = linked[index]
            if unit.dimension != "CURRENCY" or not cash_links:
                reject("INVALID_CASE_CODE", "preset requires same-unit principal cash links")
            if sum(link["cash_amount"] for _, _, link in cash_links) != leg["leg_amount"]:
                reject("INVALID_PRINCIPAL", "principal links and quantity must agree exactly")
            for split, fact, _ in cash_links:
                if unit.code != fact["currency_code"]:
                    reject("UNIT_MISMATCH", "cash and quantity currencies differ")
                expected_direction = 2 if (position["type"] == "ASSET") == (leg["leg_direction"] == "IN") else 1
                expected_type = 2
                if code == "POS_CREDIT_PURCHASE":
                    if position["type"] != "LIABILITY" or leg["leg_direction"] != "IN":
                        reject("INVALID_CASE_CODE", "credit purchase increases a liability")
                    expected_direction, expected_type = 2, 0
                elif code == "POS_CREDIT_REPAY":
                    if position["type"] != "LIABILITY" or leg["leg_direction"] != "OUT":
                        reject("INVALID_CASE_CODE", "credit principal reduces a liability")
                elif code == "SHARED_PAYMENT":
                    phase = draft["phase"]
                    expected_phase = {("ASSET", "IN"): "ADVANCE_OUT", ("ASSET", "OUT"): "COLLECT_IN",
                                      ("LIABILITY", "IN"): "RECEIVE_IN", ("LIABILITY", "OUT"): "PAY_OUT"}
                    if phase != expected_phase[(position["type"], leg["leg_direction"])]:
                        reject("INVALID_CASE_CODE", "shared phase and Position direction mismatch")
                if split["entry_type"] != expected_type or fact["cash_direction"] != expected_direction:
                    reject("INVALID_CASE_CODE", "principal cash structure does not match intent")
        # Every dedicated principal split needs complete attribution, not just
        # one link. Unlinked TRANSACTION fees and generic Position events keep
        # their separate semantics; a linked credit purchase is all principal.
        if any((split["entry_type"] == 2 or
                (code == "POS_CREDIT_PURCHASE" and index in totals))
               and totals[index] != split["cash_amount"]
               for index, split in enumerate(draft["allocations"])):
            reject("INVALID_PRINCIPAL", "principal cash cannot have an unexplained residual")
    return draft


def validate_duplicate_keepers(decisions, final_outputs, facts):
    """Validate the complete final cash set, not each draft's initial state.

    Every explicit B must remain fully excluded while its A is fully covered
    by real cash on a different known source. This rejects cycles and supports
    an explicit atomic swap, without persisting a receipt or inferred relation.
    """
    for decision in decisions:
        excluded_id, kept_id = decision["transaction_id"], decision["kept_transaction_id"]
        if excluded_id == kept_id or excluded_id not in facts or kept_id not in facts:
            reject("INVALID_DUPLICATE", "two different accepted Facts are required")
        excluded, kept = facts[excluded_id], facts[kept_id]
        if any(excluded[key] != kept[key] for key in ("amount", "currency_code", "cash_direction", "occurred_time")):
            reject("INVALID_DUPLICATE", "duplicate evidence does not match kept Fact")
        b_rows, a_rows = final_outputs[excluded_id], final_outputs[kept_id]
        if (not b_rows or any(row["entry_type"] != 3 for row in b_rows)
            or sum(row["cash_amount"] for row in b_rows) != excluded["amount"]
            or not a_rows or any(row["entry_type"] == 3 for row in a_rows)
            or sum(row["cash_amount"] for row in a_rows) != kept["amount"]):
            reject("INVALID_DUPLICATE", "kept Fact must retain complete non-duplicate cash after this whole change")
        b_refs = {row["account_ref_id"] for row in b_rows}
        a_refs = {row["account_ref_id"] for row in a_rows}
        if len(a_refs) != 1 or len(b_refs) != 1 or 0 in a_refs | b_refs or a_refs == b_refs:
            reject("INVALID_DUPLICATE", "final A and B require different, unambiguous known sources")
