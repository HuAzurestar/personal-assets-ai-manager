"""Certain canonical source groups are not exact-core suspected duplicates."""
from copy import deepcopy
import base64
from pathlib import Path

import pytest

from backend.core.import_risk import canonical_duplicate_groups, default_import_decision
from backend.schema.import_command import PreviewRowListRequest, ImportReviseInput, ImportConfirmInput
from test_pirc35_import_service import service
from test_pirc35_import_batch import prepare, row
from test_pirc35_import_confirm_preview import install, input_for
from test_pirc35_import_duplicate import manifest
from test_pirc35_import_api import client, BASE, page


def projection(service, rows):
    candidates = service.mapper.match(rows, {})
    service.db.rollback()
    return candidates, canonical_duplicate_groups(rows, candidates)


def test_same_reliable_key_first_accept_later_skip_across_pages_and_filter(service):
    first = prepare(service.mapper, [row(n) for n in range(1, 25)])
    second = prepare(service.mapper, [row()], sha="b" * 64)
    rows = first | second
    current = install(service, rows, {})
    before = manifest(service)
    response = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest(page_size=20))
    assert response["items"][0]["default_decision"] == "ACCEPT"
    assert all(item["default_decision"] == "SKIP" for item in response["items"][1:])
    later = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest(
        page_size=20, filter=dict(key="file_id", op="=", val=next(iter(second))[0])))
    item = later["items"][0]
    assert item["default_decision"] == "SKIP"
    assert item["canonical_duplicate"] == dict(kind="SOURCE_REFERENCE", keeper_row=dict(
        file_id=next(iter(first))[0], source_row_number=1), member_count=25, is_keeper=False)
    assert item["duplicate_hint"]["state"] == "NONE_IN_SCOPE" # not 24 suspected Facts
    assert manifest(service) == before
    assert "Mock reference" not in str(item["canonical_duplicate"])


@pytest.mark.parametrize("changes", [dict(reference=""), dict(account=dict(number="****3456")),
    dict(amount_minor=40000), dict(amount_minor=-39999), dict(currency="CNY_4"),
    dict(occurred_at="2024-01-01T00:00:00.000001Z")])
def test_weak_keyless_or_conflicting_group_is_never_certain(service, changes):
    rows = prepare(service.mapper, [row(), row(2, **changes)])
    _candidates, groups = projection(service, rows)
    assert groups == {}


def test_direction_and_complete_source_identity_do_not_group_transfers(service):
    rows = prepare(service.mapper, [row(), row(2, account=dict(number="9990000000123456"),
        source_account=dict(source_namespace="ccb:statement-v1", source_identity="9990000000123456",
            identity_strength="RELIABLE"), amount_minor=40000)])
    _candidates, groups = projection(service, rows)
    assert groups == {}


def test_certain_group_does_not_authorize_other_suspected_cash_or_mutate_choices(service):
    rows = prepare(service.mapper, [row(), row(2), row(3, reference="other")])
    current = install(service, rows, {})
    items = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())["items"]
    assert items[0]["canonical_duplicate"]["is_keeper"]
    assert all(item["default_decision"] == "SKIP" for item in items)
    assert all(item["choice"] is None for item in items)
    assert items[2]["canonical_duplicate"] is None
    assert service.store.get(current["token"]).choices == {}


def test_explicit_recommended_batch_keeps_one_chain_and_skipped_evidence(service):
    rows = prepare(service.mapper, [row(), row(2)])
    current = install(service, rows, {})
    items = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())["items"]
    current = service.revise(current["token"], ImportReviseInput(expected_updated_time=current["updated_time"],
        choices=[dict(file_id=item["file_id"], source_row_number=item["source_row_number"],
            decision=item["default_decision"]) for item in items]))
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, list(rows))))
    assert result["new_fact_count"] == 1 and result["skipped_count"] == 1
    saved = manifest(service)
    assert len(saved["transaction_fact"]) == len(saved["review_case"]) == len(saved["ledger_entry"]) == 1
    assert [item["row_status"] for item in saved["transaction_import_row"]] == [1, 2]
    assert all(item["raw_payload"] for item in saved["transaction_import_row"])


def test_manual_acceptance_is_not_overwritten_or_another_cash_default(service):
    rows = prepare(service.mapper, [row(), row(2)])
    current = install(service, rows, {})
    key = list(rows)[1]
    current = service.revise(current["token"], ImportReviseInput(expected_updated_time=current["updated_time"],
        choices=[dict(file_id=key[0], source_row_number=key[1], decision="ACCEPT")]))
    before = deepcopy(service.store.get(current["token"]).choices)
    item = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())["items"][1]
    assert item["default_decision"] == "SKIP" and item["choice"]["decision"] == "ACCEPT"
    assert service.store.get(current["token"]).choices == before


def test_default_projection_never_turns_inconsistent_risk_into_accept():
    candidate = dict(classification="NEW", issue=None)
    for hint in (dict(state="NONE_IN_SCOPE",candidate_count=None,scope=dict(source_known=True)),
            dict(state="UNCHECKED",candidate_count=None,scope=dict(source_known=False))):
        assert default_import_decision(candidate, hint, None) == "SKIP"


def test_actual_http_and_openapi_share_canonical_recommendation(client):
    content = (Path(__file__).parent / "fixtures/pirc35/alipay-6.csv").read_bytes()
    response = client.post(BASE + "/preview", json=dict(files=[dict(filename=f"Mock export {n}.csv",
        content_base64=base64.b64encode(content + b"\n" * n).decode()) for n in (0, 1)]))
    assert response.status_code == 200, response.text
    current = response.json()["body"]
    items = page(client, current, page_size=100)["items"]
    assert len(items) == 48
    assert [item["default_decision"] for item in items] == ["ACCEPT"] * 24 + ["SKIP"] * 24
    assert all(item["canonical_duplicate"]["member_count"] == 2 for item in items)
    assert all(item["choice"] is None for item in items)
    schema = client.get("/openapi.json").json()["components"]["schemas"]["PreviewRowPO"]["properties"]
    assert schema["default_decision"]["enum"] == ["ACCEPT", "SKIP"]
    assert "CanonicalImportDuplicate" in str(schema["canonical_duplicate"])


def test_twenty_thousand_same_identity_is_one_complete_linear_group():
    from backend.core.import_identity import fact_values
    value = row()
    rows = {(1, n): value | dict(row_number=n) for n in range(1, 20001)}
    original = fact_values(value, "a" * 64)
    candidates = {key:dict(values=original, classification="NEW", issue=None) for key in rows}
    groups = canonical_duplicate_groups(rows, candidates)
    assert len(groups) == 20000
    assert sum(group["is_keeper"] for group in groups.values()) == 1
    assert all(group["member_count"] == 20000 for group in groups.values())
