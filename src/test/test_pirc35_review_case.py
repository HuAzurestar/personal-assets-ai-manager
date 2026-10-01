"""DEV-05 business examples over the real immutable publisher."""
import copy
from datetime import datetime, timezone
import pytest
from backend.entity import TransactionFact, Position
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.schema.review_command import ReviewChangeInput
from backend.service.review_command_service import ReviewCommandService
from test_pirc35_review_command import db, execute


def position(title, nature="ASSET", unit="CNY"):
    return dict(title=title, type=nature, usage_scenario="SHARED-SETTLEMENT", party_id=1,
                counterparty="Mock same-name debtor", unit_code=unit)


def leg(amount, direction, *, new=None, existing=None, source=0):
    return dict(**({"new_position_index": new} if new is not None else {"existing_position_id": existing}),
        type="MOVEMENT", leg_amount=amount, leg_direction=direction, source=source,
        occurred_time="2024-02-01T00:00:00Z", basis="Mock documented third-person payment")


def split(fid, amount, kind="ASSET_LIABILITY"):
    return dict(transaction_id=fid, economic_type=kind, cash_amount=amount, account_ref_id=0)


def link(ai, li, amount, currency="CNY"):
    return dict(allocation_index=ai, leg_index=li, cash_amount=amount, cash_currency_code=currency)


def add_facts(db, second_receipt=10000):
    db.add_all([TransactionFact(id=fid, fact_key=f"mock-case-{fid}", amount=amount, currency_code="CNY",
        cash_direction=direction, counterparty_name="Mock third-person payer", account_code="",
        occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc))
        for fid, amount, direction in [(5, 60000, 2), (6, 20000, 1), (7, second_receipt, 1), (8, 33000, 1),
                                        (9, 40000, 1), (10, 30000, 2)]])
    db.flush()
    ReviewCommandMapper(db).create_initial_defaults(list(range(5, 11)))
    db.commit()


def test_refund_300_keeps_real_in_fact_and_original_expense_unchanged(db):
    source = copy.deepcopy(ReviewCommandService(db).detail(1))
    result = execute(db, new_reviews=[dict(case_code="REFUND", parameters=dict(transaction_ids=[2]))])
    refund = ReviewCommandService(db).detail(result["created_reviews"][0]["id"])
    assert refund["ledger_entries"][0]["cash_direction"] == "IN"
    assert refund["ledger_entries"][0]["cash_amount"] == 30000
    assert refund["type"] == "OTHER_MANUAL"
    assert ReviewCommandService(db).detail(1) == source


@pytest.mark.parametrize("second_receipt", [10000, 20000])
def test_aa_600_two_same_name_objects_then_200_and_100_third_person_receipts(db, second_receipt):
    add_facts(db, second_receipt)
    preset = dict(case_code="SHARED_PAYMENT", parameters=dict(phase="ADVANCE_OUT",
        new_positions=[position("Mock note one"), position("Mock note two")],
        allocations=[split(5, 20000, "TRANSACTION"), split(5, 40000)],
        legs=[leg(20000, "IN", new=0), leg(20000, "IN", new=1)],
        position_allocations=[link(1, 0, 20000), link(1, 1, 20000)]))
    result = execute(db, new_reviews=[preset])
    pid_one, pid_two = [row["id"] for row in result["created_positions"]]
    assert pid_one != pid_two
    detail = ReviewCommandService(db).detail(result["created_reviews"][0]["id"])
    sources = {row["position_id"]: row["id"] for row in detail["position_legs"]}
    for fid, pid, amount in [(6, pid_one, 20000), (7, pid_two, second_receipt)]:
        execute(db, new_reviews=[dict(case_code="SHARED_PAYMENT", parameters=dict(phase="COLLECT_IN",
            allocations=[split(fid, amount)], new_positions=[], legs=[leg(amount, "OUT", existing=pid, source=sources[pid])],
            position_allocations=[link(0, 0, amount)]))])
    from backend.mapper.position_mapper import PositionMapper
    mapper = PositionMapper(db)
    assert mapper.quantity(mapper.get(pid_one))["quantity"] == 0
    assert mapper.quantity(mapper.get(pid_two))["quantity"] == 20000 - second_receipt
    assert db.get(Position, pid_two).counterparty == "Mock same-name debtor"
    assert db.get(TransactionFact, 7).counterparty_name == "Mock third-person payer"
    # Same name does not permit a source from another object.
    bad = dict(case_code="POS_POSITION_SETTLE", allocations=[], new_positions=[], position_allocations=[],
        legs=[leg(1, "OUT", existing=pid_two, source=sources[pid_one])])
    preview = ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[bad]))
    assert preview["blocking_issues"][0]["code"] == "INVALID_POSITION_SOURCE"


def test_liability_400_repay_300_and_unit_overflow_failures(db):
    add_facts(db)
    intent = dict(case_code="BORROW_REPAY", parameters=dict(new_positions=[position("Mock liability", "LIABILITY")],
        allocations=[split(9, 40000)], legs=[leg(40000, "IN", new=0)], position_allocations=[link(0, 0, 40000)]))
    invalid = copy.deepcopy(intent)
    invalid["parameters"]["new_positions"][0]["unit_code"] = "USD"
    assert ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[invalid]))["blocking_issues"][0]["code"] == "UNIT_MISMATCH"
    invalid = copy.deepcopy(intent)
    invalid["parameters"]["position_allocations"][0]["cash_amount"] = 40001
    assert ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[invalid]))["blocking_issues"][0]["code"] == "LEDGER_POSITION_ALLOCATION_OVERFLOW"
    result = execute(db, new_reviews=[intent])
    pid, rid = result["created_positions"][0]["id"], result["created_reviews"][0]["id"]
    source = ReviewCommandService(db).detail(rid)["position_legs"][0]["id"]
    execute(db, new_reviews=[dict(case_code="BORROW_REPAY", parameters=dict(new_positions=[],
        allocations=[split(10, 30000)], legs=[leg(30000, "OUT", existing=pid, source=source)],
        position_allocations=[link(0, 0, 30000)]))])
    from backend.mapper.position_mapper import PositionMapper
    mapper = PositionMapper(db)
    assert mapper.quantity(mapper.get(pid))["quantity"] == 10000


def test_movement_in_cannot_reference_an_old_source_and_unknown_object_is_explicit(db):
    opened = execute(db, new_reviews=[dict(case_code="POS_OPENING", allocations=[],
        new_positions=[position("Mock opening")], legs=[dict(leg(40000, "IN", new=0), type="OPENING")], position_allocations=[])])
    pid, rid = opened["created_positions"][0]["id"], opened["created_reviews"][0]["id"]
    source = ReviewCommandService(db).detail(rid)["position_legs"][0]["id"]
    invalid = dict(case_code="POS_POSITION_OPEN", allocations=[], new_positions=[], position_allocations=[],
        legs=[leg(1, "IN", existing=pid, source=source)])
    assert ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[invalid]))["blocking_issues"][0]["code"] == "INVALID_POSITION_SOURCE"
    invalid["legs"][0].update(existing_position_id=99999, source=0)
    assert ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[invalid]))["blocking_issues"][0]["code"] == "REFERENCE_NOT_FOUND"
