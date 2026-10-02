"""R19 derived list summaries; originals and financial state remain unchanged."""
import json
from time import monotonic

import pytest
from sqlalchemy import event, update, text

from backend.entity import LedgerEntry, ReviewCase, TransactionFact, LedgerAccountRef, LedgerAccount, LedgerAccountParty
from backend.schema.flow_read import FlowListRequest, FlowSearchRequest
from backend.service.flow_read_service import FlowReadService
from backend.core.import_public_text import masked_summary
from test_ledger_api import economic_api, _facts, _review
from test_pirc35_flow_read import PO, body


BASE = "/paam/ledger/v1/flow"
LIST_PO = PO | {"active", "summary", "transaction_id", "review", "account"}


def test_list_original_review_current_state_and_immutable_detail(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("OUT", 1234, "CNY")])[0]
    with sessions() as db:
        db.get(TransactionFact, fid).summary = "Mock groceries 1234567890123456 bill@example.invalid"
        db.commit()
    original = body(client.get(f"{BASE}/{fid}"))
    result = _review(client, new_reviews=[dict(case_code="NORMAL", title="Mock corrected", parameters=dict(transaction_ids=[fid]))])
    rid = result["created_reviews"][0]["id"]
    page = body(client.get(f"{BASE}/list", params=dict(sorter=json.dumps([dict(key="id", direction="asc")]))))
    assert set(page) == {"items", "total", "page_index", "page_size"} and page["total"] == 2
    old, new = page["items"]
    assert set(old) == set(new) == LIST_PO
    assert old["active"] is False and new["active"] is True
    assert old["review"]["id"] == original["reviews"][0]["id"] and old["review"]["status"] == "REVOKED"
    assert new["review"] == dict(id=rid, title="Mock corrected", status="CONFIRMED")
    assert old["transaction_id"] == new["transaction_id"] == fid
    assert "Mock groceries" in new["summary"] and "1234567890123456" not in json.dumps(page)
    assert "bill@example.invalid" not in json.dumps(page)
    assert new["account"] == dict(state="UNIDENTIFIED", display_label="来源未识别")
    for active, expected in [(True, new["id"]), (False, old["id"])]:
        selected = body(client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key="active", op="=", val=active)))))
        assert selected["total"] == 1 and selected["items"][0]["id"] == expected
    detail = body(client.get(f"{BASE}/{fid}"))
    assert set(detail["ledger_entry"]) == PO and detail["ledger_entry"] == original["ledger_entry"]
    assert body(client.get(f"{BASE}/summary"))["entry_count"] == 1


def test_named_masked_source_current_ownership_and_unassigned(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY")] * 3)
    with sessions() as db:
        db.add(LedgerAccountParty(id=1, name="Mock Alice", status="ACTIVE"))
        db.add(LedgerAccount(id=1, party_id=1, name="Mock daily", status="ACTIVE"))
        db.add_all([LedgerAccountRef(id=i, account_id=1 if i == 1 else 0, name="Mock card", institution="",
            reference="990000008888", source_namespace="CCB:account", source_identity=f"88000000000{i}",
            identity_strength=1, status="ACTIVE") for i in (1, 2)])
        for lid, ref_id in zip(ids, (1, 2, 0)):
            db.get(LedgerEntry, lid).account_ref_id = ref_id
        db.commit()
    page = body(client.get(f"{BASE}/list", params=dict(sorter=json.dumps([dict(key="id", direction="asc")]))))
    assert [row["account"]["state"] for row in page["items"]] == ["ASSIGNED", "UNASSIGNED", "UNIDENTIFIED"]
    label = page["items"][0]["account"]["display_label"]
    assert all(word in label for word in ("Mock Alice", "Mock daily", "Mock card", "****8888"))
    assert "未分组" in page["items"][1]["account"]["display_label"]
    assert "990000008888" not in json.dumps(page) and "880000000001" not in json.dumps(page)
    with sessions() as db:
        db.get(LedgerAccount, 1).name = "Mock renamed"
        db.commit()
    current = body(client.get(f"{BASE}/list"))["items"]
    assert "Mock renamed" in next(row for row in current if row["id"] == ids[0])["account"]["display_label"]


def test_search_list_projection_masks_titles_and_binds_active_cursor(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY")] * 3)
    with sessions() as db:
        db.get(ReviewCase, ids[0]).title = "Mock 1234567890123456 bill@example.invalid"
        db.commit()
    params = dict(page_size=1, query=json.dumps([dict(key="summary", word="fact")]),
        sorter=json.dumps([dict(key="id", direction="asc")]), filter=json.dumps(dict(key="active", op="=", val=True)))
    first = body(client.get(f"{BASE}/search", params=params))
    assert set(first["items"][0]) == LIST_PO
    assert "1234567890123456" not in json.dumps(first) and "bill@example.invalid" not in json.dumps(first)
    changed = client.get(f"{BASE}/search", params=params | dict(cursor=first["next_cursor"],
        filter=json.dumps(dict(key="active", op="=", val=False))))
    assert changed.status_code == 422 and changed.json()["body"]["code"] == "LIST_CURSOR_INVALID"
    page = body(client.get(f"{BASE}/list", params=dict(sorter=params["sorter"])))
    assert first["items"][0] == page["items"][0]


def test_list_and_search_source_queries_are_bounded_not_per_row(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY")] * 100)
    with sessions() as db:
        db.add_all([LedgerAccountRef(id=i, account_id=0, name=f"Mock card {i}", institution="", reference="",
            source_namespace="", source_identity="", identity_strength=0, status="ACTIVE") for i in ids])
        db.execute(update(LedgerEntry).values(account_ref_id=LedgerEntry.id))
        db.commit()
    engine = sessions.kw["bind"]
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        for search in (False, True):
            counts = []
            for size in (1, 100):
                statements.clear()
                with sessions() as db:
                    service = FlowReadService(db)
                    if search:
                        result = service.search(FlowSearchRequest(page_size=size, query=[dict(key="summary", word="fact")]))
                    else:
                        result = service.page(FlowListRequest(page_size=size)).model_dump()
                assert len(result["items"]) == size and all(row["account"]["state"] == "UNASSIGNED" for row in result["items"])
                counts.append(len(statements))
                assert not any("raw_payload" in statement or "content_json" in statement for statement in statements)
            assert counts[0] == counts[1]
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("field", ["summary", "review"])
def test_projection_limit_refuses_entire_page_not_silent_truncation(economic_api, field):
    client, sessions = economic_api
    fid = _facts(sessions, [("IN", 100, "CNY")])[0]
    with sessions() as db:
        if field == "summary":
            db.get(TransactionFact, fid).summary = "M" * (2 * 1024 * 1024)
        else:
            db.get(ReviewCase, fid).title = "M" * (2 * 1024 * 1024)
        db.commit()
    for suffix in ("list", "search"):
        response = client.get(f"{BASE}/{suffix}", params=dict(query=json.dumps([dict(key="summary", word="M" if field == "summary" else "fact")])) if suffix == "search" else {})
        assert response.status_code == 413 and response.json()["body"]["code"] == "DETAIL_LIMIT"


def test_openapi_declares_derived_rows_without_changing_base_po(economic_api):
    client, _ = economic_api
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["FlowPO"]["properties"]) == PO
    assert set(schemas["FlowListItem"]["required"]) == LIST_PO
    assert schemas["FlowSearchBatch"]["properties"]["items"]["items"]["$ref"].endswith("/FlowListItem")


def test_public_masking_long_non_email_runs_do_not_retry_every_character():
    # Real long text, not a mocked budget. Preserve the existing public mask
    # semantics for punctuation, Unicode, repeated emails and numeric IDs.
    start = monotonic()
    for suffix in ("", "@invalid", "@invalid."):
        value = "M" * (2 * 1024 * 1024) + suffix
        assert masked_summary(value) == value
    assert monotonic() - start < 5
    assert masked_summary("a.b+tag@example.invalid (账单@example.invalid) a@b.c 1234567890123456") == (
        "[已脱敏] ([已脱敏]) [已脱敏] ****3456")


def test_list_state_count_and_named_source_share_wal_snapshot(economic_api, monkeypatch):
    from backend.mapper.flow_read_mapper import FlowReadMapper
    client, sessions = economic_api
    fid = _facts(sessions, [("OUT", 100, "CNY")])[0]
    result = _review(client, new_reviews=[dict(case_code="NORMAL", title="Mock current", parameters=dict(transaction_ids=[fid]))])
    rid = result["created_reviews"][0]["id"]
    lid = body(client.get(f"/paam/ledger/v1/review/{rid}"))["ledger_entries"][0]["id"]
    with sessions() as db:
        db.execute(text("PRAGMA journal_mode=WAL"))
        db.add(LedgerAccountParty(id=1, name="Mock old owner", status="ACTIVE"))
        db.add(LedgerAccount(id=1, party_id=1, name="Mock group", status="ACTIVE"))
        db.add(LedgerAccountRef(id=1, account_id=1, name="Mock source", institution="", reference="",
            source_namespace="", source_identity="", identity_strength=0, status="ACTIVE"))
        db.get(LedgerEntry, lid).account_ref_id = 1
        db.commit()
    original, moved = FlowReadMapper.page, []
    def after_page(self, request):
        result = original(self, request)
        if not moved:
            with sessions() as writer:
                writer.get(LedgerAccountParty, 1).name = "Mock new owner"
                writer.commit()
            _review(client, deactivate_review_ids=[rid])
            moved.append(True)
        return result
    monkeypatch.setattr(FlowReadMapper, "page", after_page)
    params = dict(filter=json.dumps(dict(key="active", op="=", val=True)))
    first = body(client.get(f"{BASE}/list", params=params))
    assert first["total"] == 1 and first["items"][0]["id"] == lid and first["items"][0]["active"] is True
    assert "Mock old owner" in first["items"][0]["account"]["display_label"]
    next_page = body(client.get(f"{BASE}/list", params=params))
    assert next_page["total"] == 1 and next_page["items"][0]["id"] == fid
    history = body(client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key="active", op="=", val=False)))))["items"]
    assert history[0]["active"] is False and "Mock new owner" in history[0]["account"]["display_label"]


def test_list_projection_python_assembly_shares_query_budget(economic_api, monkeypatch):
    import backend.mapper.bounded_query_mapper as bounded
    client, sessions = economic_api
    _facts(sessions, [("IN", 100, "CNY")])
    original = FlowReadService._items
    def expired(self, rows):
        result = original(self, rows)
        monkeypatch.setattr(bounded, "monotonic", lambda: clock + 31)
        return result
    clock = bounded.monotonic()
    monkeypatch.setattr(FlowReadService, "_items", expired)
    response = client.get(f"{BASE}/list")
    assert response.status_code == 503 and response.json()["body"]["code"] == "QUERY_BUSY"
