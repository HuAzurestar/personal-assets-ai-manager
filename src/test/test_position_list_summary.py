"""R14 public list/search summaries, not a new financial projection or store."""
import json

from sqlalchemy import event, insert

from backend.core import target_database
from backend.entity import Position
from backend.error import TargetEconomicError
from backend.mapper.position_mapper import PositionMapper
from backend.target_main import app
from test_pirc35_position import BASE, client, create, change, command, opening, settle, read


def page(client, path="/list", **params):
    response = client.get(BASE + path, params=params)
    assert response.status_code == 200, response.text
    return response.json()["body"]


def open_source(client, position, amount):
    result = command(client, new_reviews=[opening(position["id"], amount)])
    review_id = result["created_reviews"][0]["id"]
    detail = client.get(f"/paam/ledger/v1/review/{review_id}").json()["body"]
    return review_id, detail["position_legs"][0]["id"]


def test_list_and_search_show_current_quantity_identity_and_same_detail_token(client):
    known = create(client, title="Mock remaining")
    unknown = create(client, title="Mock unknown", unit_code="KG_3")
    zero = create(client, title="Mock zero", unit_code="PCS")
    root_review, source = open_source(client, known, 40000)
    command(client, new_reviews=[settle(known["id"], source, 30000)])
    _, zero_source = open_source(client, zero, 4)
    command(client, new_reviews=[settle(zero["id"], zero_source, 4)])
    for path in ("/list", "/search"):
        result = page(client, path, page_size=100)
        rows = {row["id"]: row for row in result["items"]}
        assert len(rows) == 3  # Same counterparty does not merge distinct objects.
        assert rows[known["id"]]["quantity"] == 10000
        assert rows[unknown["id"]]["quantity_state"] == "UNKNOWN"
        assert rows[unknown["id"]]["quantity"] is None
        assert rows[zero["id"]]["quantity_state"] == "KNOWN" and rows[zero["id"]]["quantity"] == 0
        for row in rows.values():
            assert row["party_name"] == "Mock person" and row["counterparty"] == "Mock borrower"
            detail = read(client, row["id"])
            for key in ("quantity_state", "quantity", "cost_state", "source_token"):
                assert row[key] == detail[key]
    command(client, deactivate_review_ids=[root_review])
    for path in ("/list", "/search"):
        row = next(row for row in page(client, path)["items"] if row["id"] == known["id"])
        assert row["quantity_state"] == "NEEDS_REVIEW" and row["quantity"] is None
        assert row["source_token"] == read(client, known["id"])["source_token"]


def test_list_summary_count_is_constant_for_one_or_hundred_objects(client):
    with target_database.SessionLocal() as db:
        db.execute(insert(Position.__table__), [dict(title=f"Mock object {index}",
            description="", type="ASSET", usage_scenario="GENERAL", party_id=1, unit_code="PCS")
            for index in range(100)])
        db.commit()
    statements = []
    def count(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(target_database.engine, "before_cursor_execute", count)
    try:
        for path in ("/list", "/search"):
            counts = []
            for size in (1, 100):
                statements.clear()
                rows = page(client, path, page_size=size)["items"]
                assert len(rows) == size
                assert all(row["quantity_state"] == "UNKNOWN" and row["quantity"] is None for row in rows)
                assert all(row["party_name"] == "Mock person" for row in rows)
                counts.append(len(statements))
            assert counts[0] == counts[1], (path, counts)
    finally:
        event.remove(target_database.engine, "before_cursor_execute", count)


def test_summary_pages_search_scope_and_history_do_not_fabricate_zero(client):
    positions = [create(client, title=f"Mock page {index}") for index in range(21)]
    stopped = positions[-1]
    review, _ = open_source(client, stopped, 7)
    command(client, deactivate_review_ids=[review])
    first = page(client, page_size=20)
    last = page(client, page_size=20, page_index=2)
    assert first["total"] == last["total"] == 21 and len(first["items"]) == 20
    assert [row["id"] for row in last["items"]] == [stopped["id"]]
    # History is real evidence; inactive-only history is known current zero,
    # unlike a newly created object with no evidence at all.
    assert last["items"][0]["quantity_state"] == "KNOWN" and last["items"][0]["quantity"] == 0
    params = dict(query=json.dumps([dict(key="title", word="Mock page 20")]), page_size=20)
    start = page(client, "/search", **params)
    assert start["items"] == [] and start["has_more"] and start["total"] is None
    end = page(client, "/search", **params, cursor=start["next_cursor"])
    assert end["items"][0]["quantity"] == 0 and not end["has_more"]


def test_summary_contribution_budget_refuses_whole_page_without_partial_numbers(client, monkeypatch):
    first = create(client, title="Mock first")
    second = create(client, title="Mock second")
    command(client, new_reviews=[opening(first["id"], 1), opening(first["id"], 2), opening(second["id"], 3)])
    monkeypatch.setattr(PositionMapper, "quantity_contribution_limit", 2)
    for path in ("/list", "/search"):
        response = client.get(BASE + path)
        assert response.status_code == 413, response.text
        assert response.json()["body"]["code"] == "AGGREGATION_LIMIT"
        assert "items" not in response.json()["body"]
    row = page(client, page_size=1)["items"][0]
    assert row["quantity"] == 3


def test_summary_scope_cannot_hide_broken_position_identity(client):
    create(client)
    with target_database.SessionLocal() as db:
        db.execute(insert(Position.__table__), [dict(title="Mock broken outside range", description="", type="ASSET",
            usage_scenario="GENERAL", party_id=999, unit_code="PCS")])
        db.commit()
    for path in ("/list", "/search"):
        response = client.get(BASE + path, params={"filter": json.dumps(dict(key="party_id", op="=", val=1))})
        assert response.status_code == 409 and response.json()["body"]["code"] == "RELATION_BROKEN"


def test_summary_openapi_extends_read_items_without_changing_shared_position_po():
    schemas = app.openapi()["components"]["schemas"]
    required = set(schemas["PositionListItem"]["required"])
    assert {"party_name", "quantity_state", "quantity", "cost_state", "source_token"} <= required
    assert "quantity_state" not in schemas["PositionPO"]["properties"]
    for name in ("PositionListPO", "PositionSearchPO"):
        assert schemas[name]["properties"]["items"]["items"]["$ref"].endswith("/PositionListItem")


def test_shared_quantity_budget_failure_keeps_metadata_write_known_uncommitted(client, monkeypatch):
    original = create(client)
    def expire(mapper, rows):
        raise TargetEconomicError(503, "synthetic aggregate budget expiry", code="AGGREGATION_LIMIT")
    with monkeypatch.context() as scope:
        scope.setattr(PositionMapper, "quantities", expire)
        failed = change(client, original, title="Mock must roll back")
        assert failed.status_code == 503 and failed.json()["body"]["code"] == "WRITE_BUSY"
        assert "not committed" in failed.json()["message"]
        payload = {key: original[key] for key in ("title", "description", "type", "usage_scenario", "party_id", "counterparty", "unit_code")}
        failed_create = client.post(BASE, json=payload)
        assert failed_create.status_code == 503 and failed_create.json()["body"]["code"] == "WRITE_BUSY"
    assert read(client, original["id"]) == original
    assert page(client)["total"] == 1


def test_error_after_metadata_commit_stays_unknown_even_for_a_known_read_code(client, monkeypatch):
    from sqlalchemy.orm import Session
    original = create(client)
    commit = Session.commit
    def commit_then_lose(session):
        commit(session)
        raise TargetEconomicError(503, "synthetic response loss after commit", code="AGGREGATION_LIMIT")
    with monkeypatch.context() as scope:
        scope.setattr(Session, "commit", commit_then_lose)
        response = change(client, original, title="Mock committed outcome unknown")
        assert response.status_code == 503 and response.json()["body"]["code"] == "RESULT_UNKNOWN"
    persisted = read(client, original["id"])
    assert persisted["title"] == "Mock committed outcome unknown" and page(client)["total"] == 1
