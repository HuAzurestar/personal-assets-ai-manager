"""Named, bounded, public-only metadata selection over fictional identities."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.core import target_database
from backend.entity import LedgerAccountParty, LedgerAccount, LedgerAccountRef
from backend.target_main import app

BASE = "/paam/ledger/v1"


def seed():
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        db.add_all([LedgerAccountParty(id=1, name="Mock Alice"), LedgerAccountParty(id=2, name="Mock Bob")])
        db.add_all([LedgerAccount(id=1, name="Mock daily", party_id=1), LedgerAccount(id=2, name="Mock reserve", party_id=2)])
        db.add_all([LedgerAccountRef(id=identifier, account_id=1 if identifier <= 21 else 2,
            name="", institution="", reference="9900123400007788", source_namespace=namespace,
            source_identity=f"99119999{identifier:08d}1234", identity_strength=1, status="ACTIVE")
            for identifier, namespace in ((i, "ccb:statement-v1" if i != 22 else "abc:statement-v1") for i in range(1, 23))])
        db.add(LedgerAccountRef(id=23, account_id=0, name="Mock %? literal", source_namespace="cmb:statement-v1",
            source_identity="9900999900001234", identity_strength=1, status="CLOSED"))
        db.commit()


def search(client, object_name, key="display_label", word="Mock", **params):
    return client.get(f"{BASE}/{object_name}/search", params={"query": json.dumps([dict(key=key, word=word)]), **params})


def test_source_labels_have_names_masked_identity_and_current_ownership():
    with TestClient(app) as client:
        seed()
        rows = client.get(BASE + "/account-ref/list", params={"page_size": 100}).json()["body"]["items"]
        assert len(rows) == 23
        assert rows[0]["party_name"] == "Mock Alice" and rows[0]["account_name"] == "Mock daily"
        assert rows[0]["party_id"] == 1
        assert "建设银行" in rows[0]["display_label"] and "****1234" in rows[0]["display_label"]
        assert "农业银行" in rows[21]["display_label"] and "Mock Bob" in rows[21]["display_label"]
        assert "招商银行" in rows[22]["display_label"] and "未分组" in rows[22]["display_label"]
        assert rows[22]["party_id"] == 0 and rows[22]["status"] == "CLOSED"
        assert "99119999" not in json.dumps(rows) and "990012340000" not in json.dumps(rows)
        group = client.get(BASE + "/account/1").json()["body"]
        assert group["party_name"] == "Mock Alice" and "Mock Alice" in group["display_label"]


def test_empty_batches_continue_and_cursor_conditions_are_bound():
    with TestClient(app) as client:
        seed()
        response = search(client, "account-ref", word="农业银行", page_size=20)
        assert response.status_code == 200, response.text
        first = response.json()["body"]
        assert first["items"] == [] and first["has_more"] and first["scanned_count"] == 20 and first["total"] is None
        response = search(client, "account-ref", word="农业银行", page_size=20, cursor=first["next_cursor"])
        assert response.status_code == 200, response.text
        last = response.json()["body"]
        assert [row["id"] for row in last["items"]] == [22] and not last["has_more"]
        stale = search(client, "account-ref", word="建设银行", page_size=20, cursor=first["next_cursor"])
        assert stale.status_code == 422 and stale.json()["body"]["code"] == "LIST_CURSOR_INVALID"
        scoped = search(client, "account-ref", word="Mock Bob", page_size=100,
            filter=json.dumps(dict(key="account_id", op="=", val=1)))
        assert scoped.status_code == 200 and scoped.json()["body"]["items"] == []


def test_search_only_matches_public_masks_and_literal_normalized_text():
    with TestClient(app) as client:
        seed()
        for key in ("source_identity", "reference", "display_label"):
            response = search(client, "account-ref", key=key, word="99119999", page_size=100)
            assert response.status_code == 200, response.text
            assert response.json()["body"]["items"] == []
        assert len(search(client, "account-ref", key="source_identity", word="1234", page_size=100).json()["body"]["items"]) == 23
        assert [row["id"] for row in search(client, "account-ref", word="%?", page_size=100).json()["body"]["items"]] == [23]
        assert [row["id"] for row in search(client, "account-party", key="name", word="ALICE").json()["body"]["items"]] == [1]
        assert [row["id"] for row in search(client, "account", key="party_name", word="bob").json()["body"]["items"]] == [2]
        bad = search(client, "account-ref", key="raw_identity", word="9911")
        assert bad.status_code == 422 and bad.json()["body"]["code"] == "LIST_QUERY_FIELD_NOT_SUPPORTED"


def test_named_page_and_search_query_count_does_not_grow_per_row():
    with TestClient(app) as client:
        seed()
        statements = []
        def count(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(target_database.engine, "before_cursor_execute", count)
        try:
            counts = {}
            for path in ("list", "search"):
                for size in (1, 100):
                    statements.clear()
                    response = client.get(BASE + f"/account-ref/{path}", params={"page_size": size})
                    assert response.status_code == 200, response.text
                    counts[path, size] = len(statements)
            assert counts["list", 1] == counts["list", 100]
            assert counts["search", 1] == counts["search", 100]
        finally:
            event.remove(target_database.engine, "before_cursor_execute", count)


def test_new_search_does_not_hide_broken_ownership():
    with TestClient(app) as client:
        seed()
        with target_database.SessionLocal() as db:
            db.add(LedgerAccountRef(account_id=987, name="Mock broken"))
            db.commit()
        response = search(client, "account-ref", word="not found", page_size=1)
        assert response.status_code == 409 and response.json()["body"]["code"] == "ACCOUNT_RELATION_BROKEN"
