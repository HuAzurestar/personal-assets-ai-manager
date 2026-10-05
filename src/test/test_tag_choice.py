"""Bounded, read-only tag choices for transaction filters, not nested dictionaries."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.core import target_database
from backend.entity import TargetTag, TargetTagView
from backend.target_main import app

BASE = "/paam/tag/v1/tag"


def seed():
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        db.add_all([TargetTagView(id=1, name="Mock purpose", system_name="mock-purpose"),
                    TargetTagView(id=2, name="Mock history", system_name="mock-history", status="ARCHIVED")])
        db.add_all([TargetTag(id=identifier, view_id=1 if identifier < 45 else 2,
            name="Mock late %? cafe\u0301" if identifier == 45 else f"Mock tag {identifier}",
            system_name=f"mock-{identifier}", status="ARCHIVED" if identifier == 45 else "ACTIVE")
            for identifier in range(1, 46)])
        db.commit()


def search(client, word="late", **params):
    return client.get(BASE + "/search", params={
        "query": json.dumps([dict(key="display_label", word=word)]), **params})


def test_list_is_a_flat_bounded_page_and_get_is_named():
    with TestClient(app) as client:
        seed()
        response = client.get(BASE + "/list")
        assert response.status_code == 200, response.text
        page = response.json()["body"]
        assert set(page) == {"items", "total", "page_index", "page_size"}
        assert page["total"] == 45 and len(page["items"]) == 20
        assert [row["id"] for row in page["items"]] == list(range(1, 21))
        assert all("tags" not in row for row in page["items"])
        detail = client.get(BASE + "/45")
        assert detail.status_code == 200, detail.text
        row = detail.json()["body"]
        assert row["view_name"] == "Mock history" and row["view_status"] == "ARCHIVED"
        assert row["status"] == "ARCHIVED" and "Mock history" in row["display_label"]
        assert row["id"] == 45 and row["view_id"] == 2
        assert client.get(BASE + "/999").status_code == 404


def test_literal_search_crosses_empty_batches_and_binds_cursor():
    with TestClient(app) as client:
        seed()
        first = search(client, word="LATE %? CAFÉ").json()["body"]
        assert first["items"] == [] and first["scanned_count"] == 20 and first["has_more"]
        second = search(client, word="LATE %? CAFÉ", cursor=first["next_cursor"]).json()["body"]
        assert second["items"] == [] and second["has_more"]
        last = search(client, word="LATE %? CAFÉ", cursor=second["next_cursor"]).json()["body"]
        assert [row["id"] for row in last["items"]] == [45] and not last["has_more"]
        assert last["total"] is None and last["scanned_count"] == 5
        for params in ({"word": "other"}, {"page_size": 10},
                       {"filter": json.dumps(dict(key="view_id", op="=", val=1))},
                       {"sorter": json.dumps([dict(key="id", direction="desc")])}):
            response = search(client, cursor=first["next_cursor"], **params)
            assert response.status_code == 422 and response.json()["body"]["code"] == "LIST_CURSOR_INVALID"
        response = client.get(BASE + "/search", params={"page_size": 100, "query": json.dumps([
            dict(key="name", word="%?"), dict(key="view_name", word="history")])})
        assert [row["id"] for row in response.json()["body"]["items"]] == [45]


@pytest.mark.parametrize("path,params", [
    ("/list", {"page_size": 101}),
    ("/list", {"query": "[]"}),
    ("/list", {"filter": json.dumps(dict(key="view_id", op="=", val=True))}),
    ("/list", {"filter": json.dumps(dict(key="id", op="=", val=2**63))}),
    ("/list", {"filter": json.dumps(dict(key="status", op="=", val="CLOSED"))}),
    ("/search", {"query": json.dumps([dict(key="payload", word="mock")])}),
    ("/search", {"query": json.dumps([dict(key="name", word=" ")])}),
    ("/search", {"page_index": 2}),
    ("/search", {"cursor": "{"}),
    ("/45", {"filter": "null"}),
    (f"/{2**63}", {}),
    ("/0", {}),
])
def test_strict_read_boundaries(path, params):
    with TestClient(app) as client:
        seed()
        response = client.get(BASE + path, params=params)
        assert response.status_code == 422, response.text
        assert response.json()["status"] == 422


def test_filters_and_readonly_routes_leave_existing_dictionary_unchanged():
    with TestClient(app) as client:
        seed()
        response = client.get(BASE + "/list", params={"filter": json.dumps(dict(
            op="AND", expression=[dict(key="view_id", op="=", val=2), dict(key="status", op="=", val="ARCHIVED")]))})
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["body"]["items"]] == [45]
        for method in ("POST", "PUT", "DELETE"):
            assert client.request(method, BASE + "/45", json={}).status_code == 405
        assert client.get(BASE + "/list").json()["body"]["total"] == 45


def test_broken_reference_is_not_hidden_by_scope_or_empty_search():
    with TestClient(app) as client:
        seed()
        with target_database.SessionLocal() as db:
            db.add(TargetTag(view_id=999, name="Mock orphan", system_name="mock-orphan"))
            db.commit()
        for path, params in (("/list", {"filter": json.dumps(dict(key="view_id", op="=", val=1))}),
                             ("/search", {"query": json.dumps([dict(key="name", word="absent")])}), ("/1", {})):
            response = client.get(BASE + path, params=params)
            assert response.status_code == 409 and response.json()["body"]["code"] == "RELATION_BROKEN"


def test_query_count_is_constant_with_page_size():
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
                    response = client.get(BASE + f"/{path}", params={"page_size": size})
                    assert response.status_code == 200, response.text
                    counts[path, size] = len(statements)
            assert counts["list", 1] == counts["list", 100]
            assert counts["search", 1] == counts["search", 100]
        finally:
            event.remove(target_database.engine, "before_cursor_execute", count)


def test_response_limit_is_explicit_not_a_partial_page(monkeypatch):
    from backend.service import tag_choice_service
    with TestClient(app) as client:
        seed()
        monkeypatch.setattr(tag_choice_service, "MAX_RESPONSE_BYTES", 32)
        for path in ("/list", "/search", "/1"):
            response = client.get(BASE + path)
            assert response.status_code == 413 and response.json()["body"]["code"] == "DETAIL_LIMIT"


def test_budget_includes_python_projection(monkeypatch):
    from backend.service import tag_choice_service
    project = tag_choice_service.TagChoiceService._po
    def delayed(service, row):
        time.sleep(.03)
        return project(service, row)
    with TestClient(app) as client:
        seed()
        monkeypatch.setattr(tag_choice_service, "QUERY_SECONDS", .01)
        monkeypatch.setattr(tag_choice_service.TagChoiceService, "_po", delayed)
        response = client.get(BASE + "/1")
        assert response.status_code == 503 and response.json()["body"]["code"] == "QUERY_BUSY"


def test_openapi_is_concrete_and_only_new_tag_reads():
    schema = app.openapi()
    for path, response_name in (("/list", "TagChoiceListResponse"),
                                ("/search", "TagChoiceSearchResponse"), ("/{tag_id}", "TagChoiceResponse")):
        route = schema["paths"][BASE + path]
        assert set(route) == {"get"}
        assert route["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(response_name)
    assert "/paam/tag/v1/view/list" in schema["paths"]
