"""Fictional supported text PDFs through the real bounded parser and v1 API."""
import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from fictional_statement_pdf import statement_pdf
from test_pirc35_import_duplicate import manifest
from test_target_import import target_import_api  # noqa: F401


BASE = "/paam/import/v1"
OWN_ACCOUNT = "990000000000001234"


def body(response):
    assert response.status_code == 200, response.text
    return response.json()["body"]


def snapshot(sessions):
    with sessions() as db:
        return manifest(SimpleNamespace(db=db))


def upload(filename, content, **options):
    return dict(filename=filename, content_base64=base64.b64encode(content).decode(), **options)


def rows(client, current):
    result = body(client.get(BASE + "/preview/" + current["token"] + "/row/list",
        params=dict(preview_digest=current["preview_digest"], page_size=100)))
    assert result["total"] == len(result["items"]) == 2
    assert [row["source_row_number"] for row in result["items"]] == [1, 2]
    assert all(row["classification"] == "NEW" and row["choice"] is None
        and row["default_decision"] == "ACCEPT"
        and row["duplicate_hint"]["state"] == "NONE_IN_SCOPE" for row in result["items"])
    assert all(row["parsed"]["amount"] == 1025 and row["parsed"]["currency_code"] == "CNY"
        and row["parsed"]["cash_direction"] == "OUT" for row in result["items"])
    return result["items"]


def confirm(client, sessions, current, selected):
    route = BASE + "/preview/" + current["token"]
    identities = [{key: row[key] for key in ("file_id", "source_row_number")} for row in selected]
    frozen = snapshot(sessions)
    current = body(client.put(route, json=dict(expected_updated_time=current["updated_time"],
        choices=[identity | dict(decision="ACCEPT", resolution="AUTO") for identity in identities])))
    assert snapshot(sessions) == frozen, "saving choices must not publish PDF cash or cards"
    payload = dict(expected_updated_time=current["updated_time"], preview_digest=current["preview_digest"],
        selected_rows=identities)
    disclosure = body(client.post(route + "/confirm-preview", json=payload))
    assert disclosure["can_confirm"] and disclosure["issues"] == []
    assert disclosure["counts"] == dict(new_real_fact=2, new_duplicate_fact=0, evidence_only=0,
        skipped=0, invalid=0, unresolved=0)
    assert snapshot(sessions) == frozen, "complete PDF preflight must remain read-only"
    result = body(client.post(route + "/confirm", json=payload |
        dict(batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["new_fact_count"] == 2 and result["remaining_count"] == 0
    assert result["duplicate_fact_count"] == result["linked_existing_count"] == result["manual_linked_count"] == 0
    assert result["skipped_count"] == result["invalid_count"] == 0
    assert len(result["processed_rows"]) == 2
    assert all(row["resolution_effect"] == "NEW_REAL" and row["row_status"] == 1
        and row["created_review_id"] > 0 and row["created_ledger_id"] > 0 for row in result["processed_rows"])
    return result


def assert_cash_chain(state, result, provider):
    refs = [ref for ref in state["ledger_account_ref"] if ref["source_namespace"] == provider + ":statement-v1"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["source_identity"] == OWN_ACCOUNT and ref["identity_strength"] == 1
    assert ref["account_id"] == 0 and ref["status"] == "ACTIVE"
    assert state["ledger_account_party"] == state["ledger_account"] == []
    facts = {row["id"]: row for row in state["transaction_fact"]}
    reviews = {row["id"]: row for row in state["review_case"]}
    ledgers = {row["id"]: row for row in state["ledger_entry"]}
    allocations = state["review_transaction_ledger_allocation"]
    evidence = {(row["transaction_import_file_id"], row["source_row_number"]): row
        for row in state["transaction_import_row"]}
    for outcome in result["processed_rows"]:
        fact = facts[outcome["transaction_id"]]
        review = reviews[outcome["created_review_id"]]
        ledger = ledgers[outcome["created_ledger_id"]]
        links = [row for row in allocations if row["transaction_id"] == fact["id"]]
        assert len(links) == 1 and links[0]["review_id"] == review["id"] and links[0]["ledger_id"] == ledger["id"]
        assert review["behavior_type"] == review["status"] == ledger["entry_type"] == 0
        assert fact["cash_direction"] == ledger["entry_direction"] == 2
        assert fact["amount"] == ledger["cash_amount"] == links[0]["cash_amount"] == 1025
        assert fact["currency_code"] == ledger["cash_currency_code"] == links[0]["cash_currency_code"] == "CNY"
        expected = datetime(2024, 1, outcome["source_row_number"], 12 if provider == "abc" else 0,
            tzinfo=ZoneInfo("Asia/Hong_Kong")).astimezone(timezone.utc)
        assert fact["occurred_time"] == ledger["occurred_time"] == expected
        assert fact["account_code"] == ledger["account_code"] == OWN_ACCOUNT
        assert ledger["account_ref_id"] == ref["id"]
        raw = evidence[(outcome["file_id"], outcome["source_row_number"])]
        assert raw["transaction_fact_id"] == fact["id"] and raw["row_status"] == 1
        payload = json.loads(raw["raw_payload"])
        assert payload["raw"]["_page"] == str(outcome["source_row_number"])
        assert payload["normalized"]["source_account"]["source_identity"] == OWN_ACCOUNT
    assert state["position"] == state["position_leg"] == state["review_ledger_position_leg_allocation"] == []


@pytest.mark.parametrize("provider", ["abc", "cmb"])
def test_supported_text_pdf_auto_defaults_publish_exact_chain_and_reupload_does_not_replay(target_import_api, provider):
    client, sessions, _engine = target_import_api
    content = statement_pdf(provider)
    original = snapshot(sessions)
    current = body(client.post(BASE + "/preview", json=dict(timezone="Asia/Hong_Kong",
        files=[upload("Mock-" + provider + ".pdf", content)])))
    assert current["issue_count"] == 0 and current["counts"]["new"] == 2
    assert current["files"][0]["parse_status"] == "READY" and current["files"][0]["parsed_row_count"] == 2
    parsed = snapshot(sessions)
    assert all(parsed[name] == original[name] for name in original if name != "transaction_import_file")
    assert parsed["transaction_import_file"][0]["file_format"] == 4
    result = confirm(client, sessions, current, rows(client, current))
    accepted = snapshot(sessions)
    assert len(accepted["transaction_fact"]) == len(accepted["review_case"]) == len(accepted["ledger_entry"]) == 2
    assert_cash_chain(accepted, result, provider)
    again = body(client.post(BASE + "/preview", json=dict(timezone="Asia/Hong_Kong",
        files=[upload("Mock-renamed.pdf", content)])))
    repeated = body(client.get(BASE + "/preview/" + again["token"] + "/row/list",
        params=dict(preview_digest=again["preview_digest"], page_size=100)))
    assert repeated["total"] == 2 and all(row["classification"] == "PROCESSED" for row in repeated["items"])
    assert again["counts"]["new"] == 0 and again["counts"]["processed"] == 2
    assert snapshot(sessions) == accepted, "same bytes must preserve all twenty tables, raw payloads and original defaults"


def test_wrong_pdf_source_has_safe_failure_valid_peer_can_commit_and_corrected_source_can_recover(target_import_api):
    client, sessions, _engine = target_import_api
    original = snapshot(sessions)
    abc = statement_pdf("abc")
    current = body(client.post(BASE + "/preview", json=dict(timezone="Asia/Hong_Kong", files=[
        upload("Mock-wrong-source.pdf", abc, source_type="cmb"),
        upload("Mock-valid-peer.pdf", statement_pdf("cmb"))])))
    failed, ready = current["files"]
    assert failed["parse_status"] == "FAILED" and failed["parse_issue_code"] == "SOURCE_MISMATCH"
    assert failed["parse_issue_message"] == "文件内容与所选来源不一致。"
    assert failed["parse_recovery"] == "请核对来源选择，按实际银行或平台重新上传。"
    assert OWN_ACCOUNT not in json.dumps(failed, ensure_ascii=False)
    assert failed["parsed_row_count"] == 0 and ready["parse_status"] == "READY"
    assert current["issue_count"] == 1 and current["counts"]["new"] == 2
    parsed = snapshot(sessions)
    assert all(parsed[name] == original[name] for name in original if name != "transaction_import_file")
    selected = rows(client, current)
    assert {row["file_id"] for row in selected} == {ready["file_id"]}
    result = confirm(client, sessions, current, selected)
    accepted = snapshot(sessions)
    assert_cash_chain(accepted, result, "cmb")
    assert [row["status"] for row in accepted["transaction_import_file"] if row["id"] == failed["file_id"]] == [3]
    corrected = body(client.post(BASE + "/preview", json=dict(timezone="Asia/Hong_Kong",
        files=[upload("Mock-corrected-source.pdf", abc)])))
    assert corrected["issue_count"] == 0 and corrected["files"][0]["parse_status"] == "READY"
    assert corrected["files"][0]["file_id"] == failed["file_id"]
    corrected_state = snapshot(sessions)
    assert all(corrected_state[name] == accepted[name] for name in accepted if name != "transaction_import_file")
    recovered = confirm(client, sessions, corrected, rows(client, corrected))
    final = snapshot(sessions)
    assert_cash_chain(final, recovered, "abc")
    assert len(final["transaction_fact"]) == len(final["review_case"]) == len(final["ledger_entry"]) == 4
    assert len(final["ledger_account_ref"]) == 2
