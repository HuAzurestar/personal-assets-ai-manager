from pydantic import ValidationError
import pytest

from backend.schema.review_command import LegDraft, MoneySplitInput, ReviewChangeInput


def test_business_intent_cannot_supply_published_ids_or_cash_facts():
    valid = dict(transaction_id=1, economic_type="ASSET_LIABILITY", cash_amount=40000, account_ref_id=0)
    for key, value in {"ledger_id": 1, "cash_direction": "OUT", "cash_currency_code": "CNY",
                       "occurred_time": "2024-01-01T00:00:00Z", "review_id": 1}.items():
        with pytest.raises(ValidationError):
            MoneySplitInput(**valid, **{key: value})
    for amount in (True, 1.2, -1, 0, 9_000_000_000_001):
        with pytest.raises(ValidationError):
            MoneySplitInput(**{**valid, "cash_amount": amount})


def test_position_intent_requires_four_core_arrays_and_single_target():
    with pytest.raises(ValidationError):
        ReviewChangeInput(new_reviews=[dict(case_code="POS_OPENING", title="Opening")])
    valid = dict(type="OPENING", leg_amount=1, leg_direction="IN", occurred_time="2024-01-01T00:00:00Z")
    assert LegDraft(**valid, new_position_index=0).new_position_index == 0
    for target in ({}, {"existing_position_id": 1, "new_position_index": 0}):
        with pytest.raises(ValidationError):
            LegDraft(**valid, **target)


def test_manual_intent_cannot_create_system_default_or_conflicting_status():
    with pytest.raises(ValidationError):
        ReviewChangeInput(new_reviews=[dict(case_code="NORMAL", parameters={}, behavior_type=0)])
    with pytest.raises(ValidationError):
        ReviewChangeInput(deactivate_review_ids=[1], activate_review_ids=[1])
