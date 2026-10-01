"""Real isolated SQLite canonical Flow reads, not mapper-return mocks."""
import json

import pytest
from sqlalchemy import select, update, text

from backend.core import target_database
from backend.entity import (LedgerEntry, ReviewCase, TransactionFact, LedgerAccountParty, LedgerAccount,
    LedgerAccountRef, LedgerEntryTag, TargetTag, TargetTagView, AutoTagRule, TagAssignmentRequest)
from backend.schema.flow_read import FlowSearchRequest
from backend.service.flow_read_service import FlowReadService
from test_ledger_api import economic_api, _facts, _review, _split, _mock_party, _mock_position, _leg
from test_pirc35_aggregate import seed_contributions


BASE = "/paam/ledger/v1/flow"
PO = {"id", "economic_type", "cash_direction", "cash_amount", "cash_currency_code", "account_ref_id",
    "occurred_time", "created_time", "updated_time"}


def body(response):
    assert response.status_code == 200, response.text
    return response.json()["body"]


def test_flow_detail_originals_and_inactive_duplicate_evidence(economic_api):
    client, sessions = economic_api
    kept, fid = _facts(sessions, [("OUT", 1234, "CNY")] * 2)
    with sessions() as db:
        db.add_all([LedgerAccountRef(id=i, account_id=0, name="Mock source", institution="", reference="",
            source_namespace=f"mock-{i}", source_identity=f"mock-own-{i}", identity_strength=1, status="ACTIVE") for i in (1, 2)])
        db.get(TransactionFact, fid).occurred_time = db.get(TransactionFact, kept).occurred_time
        db.get(LedgerEntry, fid).occurred_time = db.get(TransactionFact, kept).occurred_time
        db.get(LedgerEntry, kept).account_ref_id = 1
        db.get(LedgerEntry, fid).account_ref_id = 2
        db.execute(update(TransactionFact).where(TransactionFact.id == fid).values(
            account_code="1234567890123456", summary="Mock 1234567890123456 bill@example.invalid"))
        db.commit()
    result = _review(client, new_reviews=[dict(case_code="DUPLICATE", parameters=dict(transaction_ids=[fid]),
        account_bindings=[dict(transaction_id=fid, account_ref_id=2)], duplicate_transactions=[dict(transaction_id=fid, kept_transaction_id=kept)])])
    review = body(client.get(f"/paam/ledger/v1/review/{result['created_reviews'][0]['id']}"))
    lid = review["ledger_entries"][0]["id"]
    detail = body(client.get(f"{BASE}/{lid}"))
    assert set(detail["ledger_entry"]) == PO
    assert detail["ledger_entry"] == review["ledger_entries"][0]
    assert detail["allocations"] == review["allocations"]
    assert detail["active"] and detail["ledger_entry"]["economic_type"] == "DUPLICATE"
    assert "1234567890123456" not in json.dumps(detail) and "bill@example.invalid" not in json.dumps(detail)
    assert detail["account"]["state"] == "UNASSIGNED"
    assert detail["position_identity_state"] == "NOT_APPLICABLE"
    assert body(client.get(f"{BASE}/summary"))["entry_count"] == 1
    _review(client, deactivate_review_ids=[review["id"]])
    inactive = body(client.get(f"{BASE}/{lid}"))
    assert not inactive["active"] and inactive["reviews"][0]["status"] == "REVOKED"
    assert inactive["allocations"] == detail["allocations"]
    assert inactive["ledger_entry"] == detail["ledger_entry"]
    page = body(client.get(f"{BASE}/{lid}/allocation/list"))
    assert set(page) == {"items", "total", "page_index", "page_size"} and page["total"] == 1
    assert page["items"][0]["fact"] == inactive["facts"][0]
    assert page["items"][0]["allocation"] == inactive["allocations"][0]
    assert page["items"][0]["active"] is False


def test_unknown_asset_identity_is_not_zero_and_position_relations_share_originals(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("OUT", 5000, "CNY")])[0]
    _mock_party(sessions)
    result = _review(client, new_reviews=[dict(case_code="SHARED_PAYMENT", parameters=dict(phase="ADVANCE_OUT",
        new_positions=[_mock_position()], allocations=[_split(fid, 1000), _split(fid, 4000, "ASSET_LIABILITY")],
        legs=[_leg(4000, "IN", new_position_index=0)],
        position_allocations=[dict(allocation_index=1, leg_index=0, cash_amount=4000, cash_currency_code="CNY")]))])
    review = body(client.get(f"/paam/ledger/v1/review/{result['created_reviews'][0]['id']}"))
    flow = next(row for row in review["ledger_entries"] if row["economic_type"] == "ASSET_LIABILITY")
    detail = body(client.get(f"{BASE}/{flow['id']}"))
    assert detail["position_identity_state"] == "KNOWN"
    assert detail["positions"] == review["positions"]
    assert detail["position_legs"] == review["position_legs"]
    assert detail["position_allocations"] == review["position_allocations"]
    page = body(client.get(f"{BASE}/{flow['id']}/position_allocation/list"))
    assert page["total"] == 1 and set(page) == {"items", "total", "page_index", "page_size"}
    row = page["items"][0]
    assert row["allocation"] == detail["position_allocations"][0]
    assert row["position_leg"] == detail["position_legs"][0]
    assert row["position"] == detail["positions"][0]
    assert row["review"] == detail["reviews"][0]
    other = _facts(sessions, [("OUT", 123, "CNY")])[0]
    # Legacy asset cash can survive migration without invented quantity legs.
    # This is fixture construction, not a second production publication path.
    with sessions() as db:
        flow_id = db.scalar(select(LedgerEntry.id).where(LedgerEntry.amount == 123))
        db.execute(update(LedgerEntry).where(LedgerEntry.id == flow_id).values(entry_type=2))
        db.commit()
    detail = body(client.get(f"{BASE}/{flow_id}"))
    assert detail["position_identity_state"] == "NEEDS_IDENTITY" and not detail["position_legs"]
    assert "quantity" not in detail


def test_flow_current_account_scopes_are_distinct_from_source_accounts(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", i * 100, "CNY") for i in range(1, 4)])
    with sessions() as db:
        db.add(LedgerAccountParty(id=1, name="Mock owner", status="ACTIVE"))
        db.add(LedgerAccount(id=1, party_id=1, name="Mock group", status="ACTIVE"))
        db.add_all([LedgerAccountRef(id=i, account_id=1 if i == 1 else 0, name="Mock source", institution="", reference="",
            source_namespace="", source_identity="", identity_strength=0, status="ACTIVE") for i in (1, 2)])
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[0]).values(account_ref_id=1))
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[1]).values(account_ref_id=2))
        db.commit()
    for key, value, expected in [("account_ref_id", 0, ids[2]), ("account_id", 0, ids[1]),
        ("account_id", 1, ids[0]), ("party_id", 1, ids[0])]:
        page = body(client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key=key, op="=", val=value)))))
        assert page["total"] == 1 and page["items"][0]["id"] == expected
    owners = [body(client.get(f"{BASE}/{lid}"))["account"] for lid in ids]
    assert [owner["state"] for owner in owners] == ["ASSIGNED", "UNASSIGNED", "UNIDENTIFIED"]
    assert owners[0]["party"]["name"] == "Mock owner" and owners[0]["account"]["name"] == "Mock group"
    assert owners[1]["ref"]["account_id"] == 0 and owners[1]["account"] is None


@pytest.mark.parametrize("signed", [False, True])
def test_flow_money_sort_groups_currency_and_uses_stable_id(economic_api, signed):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "USD"), ("IN", 100, "CNY"), ("OUT", 100, "CNY"), ("IN", 200, "CNY")])
    key = "signed_cash_amount" if signed else "cash_amount"
    page = body(client.get(f"{BASE}/list", params=dict(sorter=json.dumps([dict(key=key, direction="asc")]))))
    expected = [ids[2], ids[1], ids[3], ids[0]] if signed else [ids[1], ids[2], ids[3], ids[0]]
    assert [row["id"] for row in page["items"]] == expected
    duplicate = client.get(f"{BASE}/list", params=dict(sorter=json.dumps([
        dict(key="cash_currency_code", direction="asc"), dict(key="cash_currency_code", direction="desc"), dict(key=key, direction="asc")])))
    assert duplicate.status_code == 422 and duplicate.json()["body"]["code"] == "LIST_SORTER_INVALID"


def test_flow_search_empty_batch_literal_unicode_masking_and_cursor_binding(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY")] * 4)
    with sessions() as db:
        db.get(TransactionFact, ids[0]).summary = "Cafe\u0301 literal %_ *?"
        db.get(TransactionFact, ids[1]).summary = "Mock 1234567890123456"
        db.commit()
    conditions = dict(page_size=2, query=json.dumps([dict(key="summary", word="CAFÉ literal %_ *?")]))
    first = body(client.get(f"{BASE}/search", params=conditions))
    assert not first["items"] and first["total"] is None and first["has_more"] and first["scanned_count"] == 2
    second = body(client.get(f"{BASE}/search", params=conditions | dict(cursor=first["next_cursor"])))
    assert [row["id"] for row in second["items"]] == [ids[0]] and set(second["items"][0]) == PO
    last = body(client.get(f"{BASE}/search", params=conditions | dict(cursor=second["next_cursor"])))
    assert not last["has_more"] and last["next_cursor"] is None and last["scanned_count"] == 0
    private = body(client.get(f"{BASE}/search", params=dict(query=json.dumps([dict(key="summary", word="1234567890123456")]))))
    assert private["items"] == []
    for changed in [dict(page_size=3), dict(query=json.dumps([dict(key="summary", word="changed")])),
        dict(filter=json.dumps(dict(key="account_ref_id", op="=", val=0))),
        dict(sorter=json.dumps([dict(key="id", direction="asc")]))]:
        response = client.get(f"{BASE}/search", params=conditions | dict(cursor=first["next_cursor"]) | changed)
        assert response.status_code == 422 and response.json()["body"]["code"] == "LIST_CURSOR_INVALID"


def test_flow_positive_orphan_is_not_hidden_by_empty_filter(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("IN", 100, "CNY")])[0]
    with sessions() as db:
        db.execute(update(LedgerEntry).where(LedgerEntry.id == fid).values(account_ref_id=987654))
        db.commit()
    response = client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key="party_id", op="=", val=1))))
    assert response.status_code == 409 and response.json()["body"]["code"] == "ACCOUNT_RELATION_BROKEN"


def test_flow_search_can_resume_beyond_fifty_thousand_candidates():
    seed_contributions(50002)
    with target_database.SessionLocal() as db:
        request = FlowSearchRequest(page_size=1, query=[dict(key="summary", word="Mock")], sorter=[dict(key="id", direction="asc")])
        first = FlowReadService(db).search(request)
        cursor = json.loads(first["next_cursor"])
        cursor.update(last_id=50000, sort_values=[50000])
        result = FlowReadService(db).search(request.model_copy(update=dict(cursor=json.dumps(cursor))))
    assert result["items"][0]["id"] == 50001 and result["total"] is None and result["has_more"]


def _tag_fixture(client, sessions):
    fid = _facts(sessions, [("OUT", 100, "CNY")])[0]
    view = body(client.post("/paam/tag/v1/view", json=dict(name="Mock category", system_name="mock_category")))
    tag = body(client.post(f"/paam/tag/v1/view/{view['id']}/tag", json=dict(name="Mock tag", system_name="mock_tag")))
    current = body(client.get(f"/paam/tag/v1/assignment/{fid}"))
    body(client.put(f"/paam/tag/v1/assignment/{fid}", json=dict(expected_updated_time=current["updated_time"], tag_state=dict(mock_category="mock_tag"))))
    return fid, view["id"], next(row["id"] for row in tag["tags"] if row["system_name"] == "mock_tag")


def test_flow_tag_filter_is_exists_and_source_is_not_fabricated(economic_api):
    client, sessions = economic_api
    lid, view_id, tag_id = _tag_fixture(client, sessions)
    detail = body(client.get(f"{BASE}/{lid}"))
    assert detail["tags"][0]["source_type"] == "UNKNOWN"
    with sessions() as db:
        db.add(AutoTagRule(id=1, name="Mock rule", view_id=view_id, method_config_json="{}", enabled=0))
        db.add(TagAssignmentRequest(id=1, rule_id=1, rule_revision=1, ledger_id=lid, view_id=view_id, proposed_tag_id=tag_id, status=2))
        db.commit()
    row = body(client.get(f"{BASE}/{lid}/tag/list"))["items"][0]
    assert row["source_type"] == "AUTO_RULE" and (row["request_id"], row["rule_id"], row["rule_revision"]) == (1, 1, 1)
    with sessions() as db:
        db.add(TagAssignmentRequest(id=2, rule_id=1, rule_revision=1, ledger_id=lid, view_id=view_id, proposed_tag_id=tag_id, status=2))
        db.commit()
    row = body(client.get(f"{BASE}/{lid}/tag/list"))["items"][0]
    assert row["source_type"] == "UNKNOWN" and row["request_id"] is None
    page = body(client.get(f"{BASE}/list", params=dict(filter=json.dumps(dict(key="tag_id", op="=", val=tag_id)))))
    assert page["total"] == 1 and len(page["items"]) == 1


def test_flow_detail_limits_relations_but_paged_reads_do_not_truncate(economic_api):
    client, sessions = economic_api
    lid, view_id, _ = _tag_fixture(client, sessions)
    with sessions() as db:
        db.add_all([TargetTag(id=i, view_id=view_id, name="Mock archived", system_name=f"mock_archived_{i}", status="ARCHIVED") for i in range(100, 4099)])
        db.add_all([LedgerEntryTag(ledger_id=lid, tag_id=i) for i in range(100, 4099)])
        db.commit()
    limited = client.get(f"{BASE}/{lid}")
    assert limited.status_code == 413 and limited.json()["body"]["code"] == "DETAIL_LIMIT"
    page = body(client.get(f"{BASE}/{lid}/tag/list", params=dict(page_size=100, page_index=40)))
    assert page["total"] == 4000 and len(page["items"]) == 100
    assert all(row["tag_status"] == "ARCHIVED" and row["source_type"] == "UNKNOWN" for row in page["items"])
    assert body(client.get(f"{BASE}/{lid}/allocation/list"))["total"] == 1
    assert body(client.get(f"{BASE}/{lid}/position_allocation/list"))["total"] == 0


def test_flow_detail_byte_limit_returns_error_not_partial_evidence(economic_api):
    client, sessions = economic_api
    lid = _facts(sessions, [("IN", 100, "CNY")])[0]
    with sessions() as db:
        db.get(TransactionFact, lid).summary = "Mock " * 450000
        db.commit()
    response = client.get(f"{BASE}/{lid}")
    assert response.status_code == 413 and response.json()["body"]["code"] == "DETAIL_LIMIT"
    # Cash list never selects or leaks huge source text.
    assert body(client.get(f"{BASE}/list"))["total"] == 1


def test_flow_detail_owner_move_during_read_stays_one_wal_snapshot(economic_api, monkeypatch):
    from backend.mapper.flow_read_mapper import FlowReadMapper
    client, sessions = economic_api
    lid = _facts(sessions, [("IN", 100, "CNY")])[0]
    with sessions() as db:
        db.execute(text("PRAGMA journal_mode=WAL"))
        db.add_all([LedgerAccountParty(id=i, name="Mock owner", status="ACTIVE") for i in (1, 2)])
        db.add_all([LedgerAccount(id=i, party_id=i, name="Mock group", status="ACTIVE") for i in (1, 2)])
        db.add(LedgerAccountRef(id=1, account_id=1, name="Mock source", institution="", reference="",
            source_namespace="", source_identity="", identity_strength=0, status="ACTIVE"))
        db.execute(update(LedgerEntry).where(LedgerEntry.id == lid).values(account_ref_id=1))
        db.commit()
    original, moved = FlowReadMapper.originals, []

    def after_guard(self, ledger_id):
        if not moved:
            with sessions() as writer:
                writer.execute(update(LedgerAccountRef).where(LedgerAccountRef.id == 1).values(account_id=2))
                writer.commit()
            moved.append(True)
        return original(self, ledger_id)

    monkeypatch.setattr(FlowReadMapper, "originals", after_guard)
    assert body(client.get(f"{BASE}/{lid}"))["account"]["party"]["id"] == 1
    assert body(client.get(f"{BASE}/{lid}"))["account"]["party"]["id"] == 2


@pytest.mark.parametrize("query", [[dict(key="summary", word="\ud800")],
    [dict(key="summary", word="x" * 129)], [dict(key="summary", word=" ")],
    [dict(key="summary", word="x")] * 9, [dict(key="raw_payload", word="Mock")]])
def test_flow_search_rejects_invalid_terms_without_server_error(economic_api, query):
    client, _ = economic_api
    assert client.get(f"{BASE}/search", params=dict(query=json.dumps(query))).status_code == 422


@pytest.mark.parametrize("component", ["query", "filter", "sorter"])
def test_flow_json_component_has_sixteen_kib_budget(economic_api, component):
    client, _ = economic_api
    response = client.get(f"{BASE}/list", params={component: " " * 16385})
    assert response.status_code == 422 and response.json()["body"]["code"] == f"LIST_{component.upper()}_INVALID"
