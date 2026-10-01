from datetime import datetime, timezone
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, func
from backend.core import target_database
from backend.entity import Position, LedgerAccountParty, PositionLeg, ReviewCase, LedgerEntry
from backend.target_main import app

BASE = "/paam/financial/v1/position"


@pytest.fixture
def client():
    with TestClient(app) as client:
        with target_database.SessionLocal() as db:
            db.add(LedgerAccountParty(id=1, name="Mock person"))
            db.commit()
        yield client


def create(client, **values):
    payload = dict(title="Mock note", description="fictional", type="ASSET", usage_scenario="PERSONAL-LENDING",
                   party_id=1, counterparty="Mock borrower", unit_code="CNY") | values
    response = client.post(BASE, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["body"]


def change(client, position, **values):
    payload = {key: position[key] for key in ("title", "description", "usage_scenario", "status")}
    payload.update(expected_updated_time=position["updated_time"], **values)
    return client.put(BASE + f"/{position['id']}/metadata", json=payload)


def command(client, **intent):
    base = "/paam/ledger/v1/review"
    preview = client.post(base + "/preview", json=intent).json()["body"]
    assert preview["blocking_issues"] == [], preview
    result = client.post(base + "/command", json=intent | dict(expected_reviews=preview["expected_reviews"],
        preview_digest=preview["preview_digest"]))
    assert result.status_code == 200, result.text
    return result.json()["body"]


def opening(pid, amount=40000):
    return dict(case_code="POS_OPENING", new_positions=[], allocations=[], position_allocations=[],
        legs=[dict(existing_position_id=pid, type="OPENING", leg_amount=amount, leg_direction="IN",
                   occurred_time="2024-01-01T00:00:00Z", source=0, basis="Fictional opening evidence")])


def settle(pid, source, amount=30000):
    return dict(case_code="POS_POSITION_SETTLE", new_positions=[], allocations=[], position_allocations=[],
        legs=[dict(existing_position_id=pid, type="MOVEMENT", leg_amount=amount, leg_direction="OUT",
                   occurred_time="2024-01-02T00:00:00Z", source=source, basis="Fictional documented release")])


def read(client, pid):
    response = client.get(BASE + f"/{pid}")
    assert response.status_code == 200, response.text
    return response.json()["body"]


def test_metadata_alone_unknown_no_legs_cash_and_strict_immutable_fields(client):
    row = create(client, unit_code="KG_3")
    assert row["quantity_state"] == "UNKNOWN" and row["quantity"] is None
    assert row["cost_state"] == "UNKNOWN"
    assert change(client, row, status="SETTLED").status_code == 409
    for key, value in [("type", "LIABILITY"), ("party_id", 2), ("unit_code", "PCS"), ("counterparty", "changed")]:
        assert change(client, row, **{key: value}).status_code == 422
    renamed = change(client, row, title="Distinct identity", description="Editable", usage_scenario="INVESTMENT")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["body"]["source_token"] != row["source_token"]
    assert change(client, row, title="stale").json()["body"]["code"] == "ENTITY_CHANGED"
    with target_database.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PositionLeg)) == 0
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 0
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_quantity_source_token_archive_reopen_and_known_zero_settlement(client):
    row = create(client)
    opened = command(client, new_reviews=[opening(row["id"])])
    source = client.get(f"/paam/ledger/v1/review/{opened['created_reviews'][0]['id']}").json()["body"]["position_legs"][0]["id"]
    paid = command(client, new_reviews=[settle(row["id"], source)])
    current = read(client, row["id"])
    assert current["quantity_state"] == "KNOWN" and current["quantity"] == 10000
    archived = change(client, current, status="ARCHIVED").json()["body"]
    assert archived["quantity"] == 10000
    preview = client.post("/paam/ledger/v1/review/preview", json=dict(new_reviews=[settle(row["id"], source, 10000)])).json()["body"]
    assert preview["blocking_issues"][0]["code"] == "POSITION_NOT_ACTIVE"
    resumed = change(client, archived, status="ACTIVE").json()["body"]
    assert resumed["quantity"] == 10000
    command(client, new_reviews=[settle(row["id"], source, 10000)])
    current = read(client, row["id"])
    assert current["quantity_state"] == "KNOWN" and current["quantity"] == 0
    assert change(client, current, status="SETTLED").status_code == 200
    pages = client.get(BASE + f"/{row['id']}/leg/list", params={"page_size": 2}).json()["body"]
    assert set(pages) == {"items", "total", "page_index", "page_size"} and pages["total"] == 3
    assert pages["items"][0]["unit_code"] == "CNY" and pages["items"][0]["review"]["status"] == "CONFIRMED"
    assert pages["items"][0]["position_allocations"] == []
    before = read(client, row["id"])
    command(client, deactivate_review_ids=[opened["created_reviews"][0]["id"]])
    after = read(client, row["id"])
    assert after["quantity_state"] == "NEEDS_REVIEW" and after["quantity"] is None
    assert after["source_token"] != before["source_token"]
    assert change(client, after, status="SETTLED").status_code == 409
    assert client.get(f"/paam/ledger/v1/review/{paid['created_reviews'][0]['id']}").json()["body"]["status"] == "CONFIRMED"


@pytest.mark.parametrize("usage", ["GENERAL", "PERSONAL-LENDING", "SHARED-SETTLEMENT", "STORED-VALUE", "DEPOSIT-PLEDGE",
                                   "REIMBURSEMENT", "CREDIT-CARD", "FORMAL-LOAN", "INVESTMENT"])
def test_all_nine_usage_values_and_both_natures(client, usage):
    assert create(client, usage_scenario=usage, type="LIABILITY")["usage_scenario"] == usage


def test_position_lists_trees_sorting_and_strict_query_values(client):
    create(client, title="First", type="ASSET")
    create(client, title="Second", type="LIABILITY", usage_scenario="CREDIT-CARD")
    expression = dict(op="OR", expression=[dict(key="type", op="=", val="ASSET"),
        dict(op="NOT", expression=[dict(key="usage_scenario", op="!=", val="CREDIT-CARD")])])
    result = client.get(BASE + "/list", params=dict(filter=json.dumps(expression),
        sorter=json.dumps([dict(key="created_time", direction="desc")]), page_size=1)).json()["body"]
    assert result["total"] == 2 and len(result["items"]) == 1
    assert set(result) == {"items", "total", "page_index", "page_size"}
    for expr in [dict(key="party_id", op="=", val=True), dict(key="title", op="=", val="First"),
                 dict(key="type", op="between", val=dict(start="ASSET", end="LIABILITY"))]:
        assert client.get(BASE + "/list", params=dict(filter=json.dumps(expr))).status_code == 422
    for sort in [[dict(key="party_id", direction="asc")], [dict(key="id", direction="asc"), dict(key="id", direction="desc")]]:
        assert client.get(BASE + "/list", params=dict(sorter=json.dumps(sort))).status_code == 422
    assert client.get(BASE + "/list", params=dict(query="[]")).status_code == 422
    assert client.get(BASE + "/9999").status_code == 404


def test_search_literal_nfc_casefold_empty_hit_and_full_batch_final_empty(client):
    create(client, title="No match")
    create(client, title="Café STRASSE 100%_")
    query = json.dumps([dict(key="title", word="CAFE\u0301"), dict(key="title", word="Straße"), dict(key="title", word="%_")])
    first = client.get(BASE + "/search", params=dict(query=query, page_size=1)).json()["body"]
    assert first["items"] == [] and first["scanned_count"] == 1 and first["has_more"] and first["total"] is None
    second = client.get(BASE + "/search", params=dict(query=query, page_size=1, cursor=first["next_cursor"])).json()["body"]
    assert second["items"][0]["title"] == "Café STRASSE 100%_" and second["has_more"]
    last = client.get(BASE + "/search", params=dict(query=query, page_size=1, cursor=second["next_cursor"])).json()["body"]
    assert last["items"] == [] and not last["has_more"] and last["next_cursor"] is None
    retry = client.get(BASE + "/search", params=dict(query=query, page_size=1, cursor=first["next_cursor"])).json()["body"]
    assert retry["items"] == second["items"] and retry["next_cursor"] == second["next_cursor"]
    assert client.get(BASE + "/search", params=dict(query=query, page_size=2, cursor=first["next_cursor"])).json()["body"]["code"] == "LIST_CURSOR_INVALID"
    assert client.get(BASE + "/search", params=dict(query=json.dumps([dict(key="title", word="x")]*9))).status_code == 422
    assert client.get(BASE + "/search", params=dict(query=json.dumps([dict(key="title", word="ß"*65)]))).status_code == 422
    assert client.get(BASE + "/search", params=dict(query=json.dumps([dict(key="title", word="e\u0301"*128)]))).status_code == 200


@pytest.mark.parametrize("cursor", ["{}", "[]", "null", "NaN", "not JSON", "x"*4100,
    '{"last_id":true,"sort_values":[1],"condition_hash":"x"}'])
def test_untrusted_cursors_rejected(client, cursor):
    response = client.get(BASE + "/search", params=dict(cursor=cursor))
    assert response.status_code == 422 and response.json()["body"]["code"] == "LIST_CURSOR_INVALID"


def test_seek_uses_date_types_and_descending_sort(client):
    for title in ("A", "B", "C"):
        create(client, title=title)
    params = dict(page_size=1, sorter=json.dumps([dict(key="created_time", direction="desc")]))
    first = client.get(BASE + "/search", params=params).json()["body"]
    second = client.get(BASE + "/search", params=params | dict(cursor=first["next_cursor"])).json()["body"]
    assert first["items"][0]["title"] == "C" and second["items"][0]["title"] == "B"
    invalid = json.loads(first["next_cursor"])
    invalid["sort_values"][0] = "2024-01-01T00:00:00"
    assert client.get(BASE + "/search", params=params | dict(cursor=json.dumps(invalid))).status_code == 422


def test_search_can_advance_beyond_fifty_thousand_without_total_or_truncation(client):
    with target_database.SessionLocal() as db:
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for offset in range(0, 50001, 400):
            db.execute(insert(Position.__table__), [dict(id=i, title="final match" if i == 50001 else "Mock",
                description="", type="ASSET", usage_scenario="GENERAL", party_id=1, unit_code="PCS",
                created_time=now, updated_time=now) for i in range(offset + 1, min(offset + 400, 50001) + 1)])
        db.commit()
        from backend.schema.bounded_search import parse_search_request
        from backend.mapper.position_mapper import PositionMapper
        mapper = PositionMapper(db)
        cursor, found, scanned = None, [], 0
        while True:
            request = parse_search_request(page_size=100, query='[{"key":"title","word":"final match"}]', cursor=cursor)
            batch = mapper.search(request)
            scanned += batch["scanned_count"]
            found += [row["id"] for row in batch["items"]]
            assert batch["total"] is None and batch["scanned_count"] <= 100
            if not batch["has_more"]:
                break
            cursor = batch["next_cursor"]
        assert scanned == 50001 and found == [50001]
