"""Canonical Review list/search/detail and original relation paging on SQLite."""
import json
import pytest
from sqlalchemy import update

from backend.core import target_database
from backend.entity import ReviewCase, ReviewAllocation
from backend.error import TargetEconomicError
from backend.schema.review_query import ReviewRelationRequest
from backend.service.review_read_service import ReviewReadService
from test_ledger_api import economic_api, _facts, _review, _split, _mock_party, _mock_position, _leg
from test_pirc35_aggregate import seed_contributions
from test_pirc35_flow_read import body

BASE = "/paam/ledger/v1/review"
PO = {"id", "type", "status", "title", "created_time", "updated_time"}
TYPES = ["NORMAL_TRANSACTION", "BORROW_AND_REPAY", "CREDIT_CARD", "SHARED_SETTLEMENT", "OTHER_MANUAL"]


def test_review_all_five_types_and_semantic_filters(economic_api):
    client, sessions = economic_api
    _facts(sessions, [("IN", 100, "CNY")] * 5)
    with sessions() as db:
        for identifier, type_id in enumerate(range(5), 1):
            db.get(ReviewCase, identifier).behavior_type = type_id
        db.get(ReviewCase, 5).status = 1
        db.commit()
    page = body(client.get(f"{BASE}/list", params=dict(sorter=json.dumps([dict(key="id", direction="asc")]))))
    assert set(page) == {"items", "total", "page_index", "page_size"} and page["total"] == 5
    assert [row["type"] for row in page["items"]] == TYPES
    assert all(set(row) == PO for row in page["items"])
    for type_name in TYPES:
        result = body(client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key="type", op="=", val=type_name)))))
        assert result["total"] == 1 and result["items"][0]["type"] == type_name
    expression = dict(op="AND", expression=[dict(op="OR", expression=[dict(key="type",op="=",val="CREDIT_CARD"),
        dict(key="status",op="=",val="REVOKED")]), dict(op="NOT", expression=[dict(key="id",op="=",val=5)])])
    result = body(client.get(f"{BASE}/list", params=dict(filter=json.dumps(expression))))
    assert result["total"] == 1 and result["items"][0]["type"] == "CREDIT_CARD"


@pytest.mark.parametrize("expression", [dict(key="type",op="=",val=2), dict(key="status",op="=",val=0),
    dict(key="behavior_type",op="=",val=0), dict(key="id",op="=",val=True),
    dict(key="created_time",op="=",val="2026-09-13T00:00:00Z"),
    dict(key="created_time",op=">=",val="2026-09-13T00:00:00")])
def test_review_rejects_legacy_and_invalid_filters(economic_api, expression):
    client, _ = economic_api
    assert client.get(f"{BASE}/list", params=dict(filter=json.dumps(expression))).status_code == 422


def test_review_search_empty_batch_literal_text_masking_and_scope_binding(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("OUT", 100, "CNY")] * 4)
    with sessions() as db:
        db.get(ReviewCase, ids[0]).title = "Mock Cafe\u0301 literal %_*? 1234567890123456"
        db.commit()
    conditions = dict(page_size=2, sorter=json.dumps([dict(key="id",direction="desc")]),
        query=json.dumps([dict(key="title",word="CAFÉ literal %_*?")]))
    first = body(client.get(f"{BASE}/search", params=conditions))
    assert first["items"] == [] and first["has_more"] and first["total"] is None
    second = body(client.get(f"{BASE}/search", params=conditions | dict(cursor=first["next_cursor"])))
    assert [row["id"] for row in second["items"]] == [ids[0]]
    assert "1234567890123456" not in second["items"][0]["title"] and set(second["items"][0]) == PO
    private = body(client.get(f"{BASE}/search", params=dict(query=json.dumps([dict(key="title",word="1234567890123456")]))))
    assert private["items"] == []
    for change in [dict(page_size=3), dict(filter=json.dumps(dict(key="status",op="=",val="CONFIRMED"))),
        dict(query=json.dumps([dict(key="title",word="Other")]))]:
        response = client.get(f"{BASE}/search", params=conditions | dict(cursor=first["next_cursor"]) | change)
        assert response.status_code == 422 and response.json()["body"]["code"] == "LIST_CURSOR_INVALID"
    response = client.get("/paam/ledger/v1/flow/search", params=dict(page_size=2, cursor=first["next_cursor"]))
    assert response.status_code == 422 and response.json()["body"]["code"] == "LIST_CURSOR_INVALID"


def test_review_each_original_relation_page_matches_detail_before_and_after_revoke(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("OUT", 5000, "CNY")])[0]
    _mock_party(sessions)
    result = _review(client, new_reviews=[dict(case_code="SHARED_PAYMENT", parameters=dict(phase="ADVANCE_OUT",
        new_positions=[_mock_position()], allocations=[_split(fid, 1000), _split(fid, 4000, "ASSET_LIABILITY")],
        legs=[_leg(4000, "IN", new_position_index=0)],
        position_allocations=[dict(allocation_index=1,leg_index=0,cash_amount=4000,cash_currency_code="CNY")]))])
    rid = result["created_reviews"][0]["id"]
    original = body(client.get(f"{BASE}/{rid}"))
    _review(client, deactivate_review_ids=[rid])
    inactive = body(client.get(f"{BASE}/{rid}"))
    assert inactive["status"] == "REVOKED"
    for kind, field in [("allocation","allocations"),("flow","ledger_entries"),("position_leg","position_legs"),
        ("position_allocation","position_allocations"),("position","positions")]:
        assert inactive[field] == original[field]
        page = body(client.get(f"{BASE}/{rid}/{kind}/list"))
        assert page["items"] == inactive[field] and page["total"] == len(inactive[field])
        assert set(page) == {"items", "total", "page_index", "page_size"}


def test_review_large_original_detail_refuses_but_relation_pages_are_complete():
    seed_contributions(2000)
    with target_database.SessionLocal() as db:
        db.execute(update(ReviewAllocation).values(review_id=1))
        db.commit()
    with target_database.SessionLocal() as db, pytest.raises(TargetEconomicError) as caught:
        ReviewReadService(db).detail(1)
    assert caught.value.code == "DETAIL_LIMIT" and caught.value.status_code == 413
    with target_database.SessionLocal() as db:
        page = ReviewReadService(db).relation_page(1, "allocation", ReviewRelationRequest(page_index=20,page_size=100))
        assert page["total"] == 2000 and len(page["items"]) == 100 and page["items"][-1]["id"] == 2000
        page = ReviewReadService(db).relation_page(1, "flow", ReviewRelationRequest(page_index=20,page_size=100))
        assert page["total"] == 2000 and page["items"][-1]["id"] == 2000


def test_review_orphan_is_not_hidden_by_filter_and_huge_title_has_no_partial_result(economic_api):
    client, sessions = economic_api
    lid = _facts(sessions, [("IN", 100, "CNY")])[0]
    with sessions() as db:
        db.get(ReviewCase, lid).title = "Mock " * 450000
        db.commit()
    response = client.get(f"{BASE}/{lid}")
    assert response.status_code == 413 and response.json()["body"]["code"] == "DETAIL_LIMIT"
    with sessions() as db:
        db.execute(update(ReviewAllocation).values(ledger_id=987654))
        db.commit()
    response = client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key="id",op="=",val=999))))
    assert response.status_code == 409 and response.json()["body"]["code"] == "RELATION_BROKEN"
