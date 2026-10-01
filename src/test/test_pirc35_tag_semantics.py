import pytest
from sqlalchemy import func, select

from backend.entity import LedgerAccountRef, LedgerEntryTag, TargetTag, TargetTagView
from backend.error import TargetEconomicError
from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
from backend.service.review_command_service import ReviewCommandService
from backend.service.target_tag_projection_service import TargetTagProjectionService
from test_pirc35_review_command import db, execute, normal, borrowed


@pytest.fixture
def tagged(db):
    view = TargetTagView(name="Mock category", system_name="mock_category", status="ACTIVE")
    db.add(view)
    db.flush()
    default = TargetTag(view_id=view.id, name="Unclassified", system_name="unclassified", status="ACTIVE")
    food = TargetTag(view_id=view.id, name="Mock food", system_name="mock_food", status="ACTIVE")
    db.add_all([default, food])
    db.flush()
    TargetTagProjectionService(db).sync_ledgers([1, 2, 3, 4])
    db.commit()
    return db, view.id, default.id, food.id


def ledger(db, result):
    rid = result["created_reviews"][0]["id"]
    return ReviewCommandService(db).detail(rid)["ledger_entries"][0]["id"]


def assign(db, ledger_id, tag_id):
    TargetTagProjectionService(db).mapper.replace({ledger_id: (tag_id,)})
    db.commit()


def tags(db, ledger_id):
    return list(db.scalars(select(LedgerEntryTag.tag_id).where(LedgerEntryTag.ledger_id == ledger_id)))


def test_unique_equivalent_keeps_value_but_not_old_tag_row_identity(tagged):
    db, view_id, default, food = tagged
    first = execute(db, new_reviews=[normal(1)])
    old = ledger(db, first)
    assign(db, old, food)
    old_row = db.scalar(select(LedgerEntryTag).where(LedgerEntryTag.ledger_id == old))
    original = (old_row.id, old_row.created_time, old_row.updated_time)
    intent = dict(new_reviews=[normal(1) | dict(title="Different title is not financial semantics")])
    preview = ReviewCommandService(db).preview(ReviewChangeInput(**intent))
    assert preview["tag_effect"]["mappings"] == [dict(old_ledger_id=old,
        new_output=dict(review_index=0, allocation_index=0), view_id=view_id, tag_id=food, disposition="KEEP")]
    second = execute(db, **intent)
    new = ledger(db, second)
    assert new != old and tags(db, new) == [food] and tags(db, old) == [food]
    db.refresh(old_row)
    assert original == (old_row.id, old_row.created_time, old_row.updated_time)
    assert second["tag_effect"]["mappings"][0]["ledger_id"] == new
    assert "new_output" not in second["tag_effect"]["mappings"][0]


@pytest.mark.parametrize("change", ["split", "ref", "type"])
def test_changed_cash_meaning_does_not_copy(tagged, change):
    db, view_id, default, food = tagged
    old = ledger(db, execute(db, new_reviews=[normal(1)]))
    assign(db, old, food)
    draft = normal(1)
    if change == "split":
        draft["parameters"] = dict(allocations=[dict(transaction_id=1, economic_type="TRANSACTION",
            cash_amount=amount, account_ref_id=0) for amount in (10000, 30000)])
    elif change == "ref":
        ref = LedgerAccountRef(account_id=0, name="Mock ref", status="ACTIVE", identity_strength=0,
                               source_namespace="", source_identity="")
        db.add(ref)
        db.commit()
        draft["account_bindings"] = [dict(transaction_id=1, account_ref_id=ref.id)]
    else:
        draft["case_code"] = "INTERNAL_TRANSFER"
    result = execute(db, new_reviews=[draft])
    outputs = ReviewCommandService(db).detail(result["created_reviews"][0]["id"])["ledger_entries"]
    assert all(tags(db, row["id"]) == [default] for row in outputs)
    effects = result["tag_effect"]["mappings"]
    assert all(row["disposition"] == "REVIEW_REQUIRED" for row in effects if row["ledger_id"])
    assert any(row["old_ledger_id"] == old and row["disposition"] == "RETAIN_INACTIVE" for row in effects)
    assert tags(db, old) == [food]


def test_symmetric_ambiguity_never_guesses_one_old_or_new_output(tagged):
    db, view_id, default, food = tagged
    split = normal(1)
    split["parameters"] = dict(allocations=[dict(transaction_id=1, economic_type="TRANSACTION",
        cash_amount=20000, account_ref_id=0) for _ in range(2)])
    original = execute(db, new_reviews=[split])
    old_outputs = ReviewCommandService(db).detail(original["created_reviews"][0]["id"])["ledger_entries"]
    for row in old_outputs:
        assign(db, row["id"], food)
    result = execute(db, new_reviews=[split])
    outputs = ReviewCommandService(db).detail(result["created_reviews"][0]["id"])["ledger_entries"]
    assert all(tags(db, row["id"]) == [default] for row in outputs)
    assert not any(row["disposition"] == "KEEP" for row in result["tag_effect"]["mappings"])


@pytest.mark.parametrize("change", ["none", "basis", "quantity", "object", "cash_link"])
def test_position_description_is_part_of_equivalence(tagged, change):
    db, view_id, default, food = tagged
    first = execute(db, new_reviews=[borrowed()])
    old = ledger(db, first)
    assign(db, old, food)
    pid = first["created_positions"][0]["id"]
    draft = borrowed()
    draft["parameters"]["new_positions"] = []
    leg = draft["parameters"]["legs"][0]
    leg.pop("new_position_index")
    leg["existing_position_id"] = pid
    if change == "basis":
        leg["basis"] = "Different explicit basis"
    elif change == "quantity":
        draft["case_code"] = "POS_POSITION_OPEN"
        draft = dict(case_code=draft["case_code"], title=draft["title"], **draft["parameters"])
        draft.pop("transaction_ids", None)
        draft["legs"][0]["leg_amount"] = 50000
    elif change == "object":
        draft = borrowed()  # Same metadata but a new object is not the old Position.
    elif change == "cash_link":
        draft["case_code"] = "POS_POSITION_OPEN"
        draft = dict(case_code=draft["case_code"], title=draft["title"], **draft["parameters"])
        draft["position_allocations"][0]["cash_amount"] = 30000
    # Changing the preset also changes Review behavior: still cannot inherit.
    result = execute(db, new_reviews=[draft])
    new = ledger(db, result)
    assert tags(db, new) == ([food] if change == "none" else [default])


@pytest.mark.parametrize("broken", ["default", "orphan_tag", "duplicate_view"])
def test_tag_relation_damage_blocks_atomic_publication(tagged, broken):
    db, view_id, default, food = tagged
    if broken == "default":
        db.get(TargetTag, default).status = "ARCHIVED"
    else:
        db.add(LedgerEntryTag(ledger_id=1, tag_id=999999 if broken == "orphan_tag" else food))
    db.commit()
    service = ReviewCommandService(db)
    preview = service.preview(ReviewChangeInput(new_reviews=[normal(1)]))
    assert preview["blocking_issues"][0]["code"] == "TAG_RELATION_BROKEN"


def test_restoration_preserves_legal_values_but_archived_tag_is_not_active(tagged):
    db, view_id, default, food = tagged
    original = execute(db, new_reviews=[normal(1)])
    old = ledger(db, original)
    assign(db, old, food)
    execute(db, deactivate_review_ids=[original["created_reviews"][0]["id"]])
    db.get(TargetTag, food).status = "ARCHIVED"
    db.commit()
    execute(db, activate_review_ids=[original["created_reviews"][0]["id"]])
    assert tags(db, old) == [default]
    before = db.scalar(select(func.count()).select_from(LedgerEntryTag))
    for _ in range(3):
        execute(db, deactivate_review_ids=[original["created_reviews"][0]["id"]])
        execute(db, activate_review_ids=[original["created_reviews"][0]["id"]])
    assert db.scalar(select(func.count()).select_from(LedgerEntryTag)) == before


def test_tag_edit_after_preview_invalidates_semantic_mapping(tagged):
    db, view_id, default, food = tagged
    old = ledger(db, execute(db, new_reviews=[normal(1)]))
    intent = dict(new_reviews=[normal(1)])
    preview = ReviewCommandService(db).preview(ReviewChangeInput(**intent))
    assign(db, old, food)
    with pytest.raises(TargetEconomicError, match="preview premises changed"):
        ReviewCommandService(db).command(ReviewCommandInput(**intent,
            expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
