import base64
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from backend.core import target_database
from backend.core.import_preview_store import import_preview_store
from backend.entity import TransactionImportFile, TransactionImportRow, TransactionFact
from backend.target_main import app

BASE = "/paam/import/v1"


@pytest.fixture
def client():
    import_preview_store.clear()
    with TestClient(app) as client:
        yield client
    import_preview_store.clear()


def preview(client, content=None, filename="mock.csv"):
    content = content if content is not None else (Path(__file__).parent / "fixtures" / "pirc35" / "ccb-2.csv").read_bytes()
    response = client.post(BASE + "/preview", json=dict(files=[dict(filename=filename, content_base64=base64.b64encode(content).decode())]))
    assert response.status_code == 200, response.text
    return response.json()["body"]


def page(client, current, **params):
    response = client.get(BASE + f"/preview/{current['token']}/row/list", params=dict(preview_digest=current["preview_digest"], **params))
    assert response.status_code == 200, response.text
    return response.json()["body"]


def selected(row):
    return {key: row[key] for key in ("file_id", "source_row_number")}


def accept(client, current, rows):
    response = client.put(BASE + f"/preview/{current['token']}", json=dict(expected_updated_time=current["updated_time"],
        choices=[selected(row) | dict(decision="ACCEPT") for row in rows]))
    assert response.status_code == 200, response.text
    current = response.json()["body"]
    response = client.post(BASE + f"/preview/{current['token']}/confirm", json=dict(expected_updated_time=current["updated_time"],
        preview_digest=current["preview_digest"], selected_rows=[selected(row) for row in rows]))
    assert response.status_code == 200, response.text
    return response.json()["body"]


def test_v1_cutover_selected_batch_typed_read_and_no_old_receipt(client):
    current = preview(client)
    rows = page(client, current)["items"]
    assert len(rows) == 20 and "raw_payload" not in str(rows)
    old = client.post(BASE + f"/preview/{current['token']}/confirm", json=dict(version="retired"))
    assert old.status_code == 422
    result = accept(client, current, rows)
    assert result["new_fact_count"] == 20 and result["remaining_count"] == 4
    assert "bill_fact_ids" not in result and "version" not in result
    file_id = current["files"][0]["file_id"]
    detail = client.get(BASE + f"/import_file/{file_id}").json()["body"]
    assert set(detail) == {"file", "progress", "coverage"}
    assert detail["progress"] == dict(accepted=20, skipped=0, invalid=0, remaining=4)
    assert detail["coverage"] == "ACTIVITY_RANGE_ONLY"
    sha = current["files"][0]["sha256"]
    files = client.get(BASE + "/import_file/list", params=dict(filter=json.dumps(dict(key="sha256", op="=", val=sha)))).json()["body"]
    assert set(files) == {"items", "total", "page_index", "page_size"} and files["total"] == 1
    persisted = client.get(BASE + f"/import_file/{file_id}/row/list").json()["body"]
    assert len(persisted["items"]) == 20
    row = persisted["items"][0]
    assert row["transaction_id"] > 0 and "transaction_fact" not in row and "raw_payload" not in row
    one = client.get(BASE + f"/import_file/{file_id}/row/{row['id']}").json()["body"]
    assert set(one) == {"row", "raw_payload", "fact"} and isinstance(one["raw_payload"], dict)
    assert one["fact"]["id"] == row["transaction_id"] and one["raw_payload"]["normalized"]["currency"] == "CNY"
    assert client.get(BASE + f"/import_file/{file_id + 1}/row/{row['id']}").status_code == 404
    relations = client.get(BASE + f"/import_file/{file_id}/row/relations", params=dict(row_ids=json.dumps([row["id"]]))).json()["body"]
    assert relations["total"] == 1 and relations["items"][0]["review_status"] == "CONFIRMED"
    facts = client.get(BASE + f"/import_file/{file_id}/transaction_fact/list").json()["body"]
    assert facts["total"] == 20
    # No version readback, replay result or accepted-row financial reset.
    current = client.get(BASE + f"/preview/{current['token']}").json()["body"]
    assert current["counts"]["processed"] == 20
    assert client.delete(BASE + f"/preview/{current['token']}").status_code == 200
    assert client.get(BASE + f"/preview/{current['token']}").status_code == 410
    assert client.get(BASE + f"/import_file/{file_id}").json()["body"]["progress"]["accepted"] == 20


def test_empty_valid_header_not_parse_error_and_no_fabricated_currency(client):
    content = "建设银行个人交易明细\n账号：990000000000001234\n币种：人民币\n交易日期,交易金额,币别,摘要\n".encode()
    current = preview(client, content)
    assert current["files"][0]["parsed_row_count"] == 0 and current["issue_count"] == 0
    assert current["files"][0]["parse_status"] == "EMPTY"
    file = client.get(BASE + f"/import_file/{current['files'][0]['file_id']}").json()["body"]
    assert file["file"]["status"] == 1 and file["progress"]["remaining"] == 0


def test_failed_file_is_explicit_and_other_file_can_still_be_confirmed(client):
    valid = (Path(__file__).parent / "fixtures" / "pirc35" / "ccb-2.csv").read_bytes()
    response = client.post(BASE + "/preview", json=dict(files=[
        dict(filename="Mock valid.csv", source_type="ccb", content_base64=base64.b64encode(valid).decode()),
        dict(filename="Mock broken.csv", source_type="ccb", content_base64=base64.b64encode(b"not,a,statement").decode()),
    ]))
    assert response.status_code == 200, response.text
    current = response.json()["body"]
    files = {row["filename"]: row for row in current["files"]}
    failed = files["Mock broken.csv"]
    assert failed["parse_status"] == "FAILED"
    assert failed["parse_issue_code"] == "HEADER_NOT_FOUND"
    assert "表头" in failed["parse_issue_message"] and failed["parse_recovery"]
    assert failed["invalid"] == failed["parsed_row_count"] == 0
    assert files["Mock valid.csv"]["parse_status"] == "READY"
    assert current["issue_count"] == 1
    rows = page(client, current)["items"]
    assert len(rows) == 20
    result = accept(client, current, rows[:1])
    assert result["new_fact_count"] == 1
    response = client.get(BASE + f"/preview/{current['token']}")
    assert response.status_code == 200, response.text
    assert {row["filename"]: row for row in response.json()["body"]["files"]}["Mock broken.csv"]["parse_status"] == "FAILED"
    valid_id = files["Mock valid.csv"]["file_id"]
    file = client.get(BASE + f"/import_file/{valid_id}").json()["body"]
    assert file["progress"]["accepted"] == 1 and file["progress"]["remaining"] == 23


def test_source_read_literal_search_and_public_issue_projection_before_matching(client):
    current = preview(client)
    file_id = current["files"][0]["file_id"]
    accept(client, current, page(client, current)["items"][:1])
    with target_database.SessionLocal() as db:
        db.execute(update(TransactionImportRow).values(source_reference="mock-private-reference-123456789",
            issue_code="MOCK_ISSUE", issue_message="mock private parser text 0000000000123456"))
        db.commit()
    endpoint = BASE + f"/import_file/{file_id}/row/search"
    for word, expected in [("MOCK_ISSUE", 1), ("mock private parser", 0), ("0000000000123456", 0), ("%", 0)]:
        result = client.get(endpoint, params=dict(query=json.dumps([dict(key="issue_message", word=word)])))
        assert result.status_code == 200, result.text
        body = result.json()["body"]
        assert body["total"] is None and len(body["items"]) == expected
    row = client.get(BASE + f"/import_file/{file_id}/row/list").json()["body"]["items"][0]
    assert row["source_reference"].startswith("****") and "0000000000123456" not in str(row)
    assert row["issue_message"] == "MOCK_ISSUE"
    assert client.get(endpoint, params=dict(query=json.dumps([dict(key="raw_payload", word="Mock")]))).status_code == 422


def test_source_scan_empty_hit_batch_continue_condition_hash_and_no_cumulative_cap(client):
    from backend.entity.base import utc_now
    now = utc_now()
    with target_database.SessionLocal() as db:
        db.execute(insert(TransactionImportFile), [dict(id=n, filename="Mock needle" if n == 50001 else "Mock other",
            sha256=f"{n:064x}", status=1, created_time=now, updated_time=now) for n in range(1, 50002)])
        db.commit()
    endpoint = BASE + "/import_file/search"
    params = dict(query=json.dumps([dict(key="filename", word="needle")]), page_size=100,
        sorter=json.dumps([dict(key="id", direction="asc")]),
        filter=json.dumps(dict(key="id", op="=", val=50001)))
    # Scope begins beyond 50000; search is a candidate seek, not a history cap.
    body = client.get(endpoint, params=params).json()["body"]
    assert body["items"][0]["id"] == 50001 and body["total"] is None
    params.pop("filter")
    body = client.get(endpoint, params=params).json()["body"]
    assert body["items"] == [] and body["has_more"] and body["scanned_count"] == 100
    cursor = body["next_cursor"]
    again = client.get(endpoint, params=params | dict(cursor=cursor)).json()["body"]
    assert again["items"] == [] and again["has_more"]
    assert client.get(endpoint, params=params | dict(cursor=cursor, page_size=20)).status_code == 422


def test_positive_orphan_source_not_silently_dropped_by_join(client):
    current = preview(client)
    file_id = current["files"][0]["file_id"]
    with target_database.SessionLocal() as db:
        db.add(TransactionImportRow(transaction_import_file_id=file_id, source_row_number=1, row_status=1,
            transaction_fact_id=99999, raw_hash="a" * 64))
        db.commit()
    response = client.get(BASE + f"/import_file/{file_id}/row/list")
    assert response.status_code == 409 and response.json()["body"]["code"] == "RELATION_BROKEN"


@pytest.mark.parametrize("params", [dict(page_size=101), dict(query=json.dumps([dict(key="filename", word="x")])),
    dict(filter=json.dumps(dict(key="status", op="=", val=True))), dict(filter=json.dumps(dict(key="sha256", op="=", val="bad"))),
    dict(filter=json.dumps(dict(key="id", op="=", val=2**100))), dict(sorter=json.dumps([dict(key="id", direction="asc")] * 2))])
def test_invalid_list_capabilities_values_and_boundaries(client, params):
    assert client.get(BASE + "/import_file/list", params=params).status_code == 422


@pytest.mark.parametrize("ids", [[{}], [[]], [True], ["1"], [2**100], [1, 1], [], [1] * 101])
def test_relation_ids_reject_invalid_input_before_hashing_or_sql(client, ids):
    file_id = preview(client)["files"][0]["file_id"]
    response = client.get(BASE + f"/import_file/{file_id}/row/relations", params={"row_ids": json.dumps(ids)})
    assert response.status_code == 422, response.text
    assert response.json()["body"]["code"] == "INPUT_LIMIT"
