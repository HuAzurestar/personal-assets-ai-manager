"""Strict informed import intents and frozen complete risk scopes, fictional only."""
import pytest
from pydantic import ValidationError
from sqlalchemy import event

from backend.core.import_preview_store import import_preview_store
from backend.error import TargetIntakeError
from backend.schema.import_command import RowChoice, ImportConfirmInput, PreviewRowListRequest
from backend.schema.import_batch_read import ImportBatchPreviewPO, PreviewRowListPO
from test_pirc35_import_service import service, choose
from test_pirc35_import_api import client, BASE
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install, input_for, batch
from test_pirc35_import_duplicate import manifest, existing_pair, local_pair
from test_pirc35_import_match import paired
from import_batch_helpers import confirm_api_batch, prepare_api_batch


def test_fixture_helper_default_never_acknowledges_unknown_cash(service, client):
    rows = prepare(service.mapper, [row(reference="", account={}, source_account={})])
    current = install(service, rows, {})
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    error, payload = prepare_api_batch(client, current)
    assert error is None and "batch_preview_digest" not in payload
    choices = import_preview_store.get(current["token"]).choices
    assert all(choice["resolution"] == "AUTO" and not choice["acknowledge_new_risk"] for choice in choices.values())
    response = client.post(BASE + f"/preview/{current['token']}/confirm", json=payload)
    assert response.status_code == 422 and response.json()["body"]["code"] == "IMPORT_REVIEW_REQUIRED"
    assert manifest(service) == before


def test_fixture_helper_explicit_new_discloses_then_confirms_once(service, client, monkeypatch):
    rows = prepare(service.mapper, [row(reference="", account={}, source_account={})])
    current = install(service, rows, {})
    import_preview_store.put(service.store.get(current["token"]))
    observed = []
    original = client.post

    def recording(url, **kwargs):
        response = original(url, **kwargs)
        observed.append((url, kwargs.get("json"), response))
        return response

    monkeypatch.setattr(client, "post", recording)
    service.db.rollback()
    response = confirm_api_batch(client, current, explicit_new=True)
    assert response.status_code == 200, response.text
    assert response.json()["body"]["new_fact_count"] == 1
    assert [call[0] for call in observed] == [BASE + f"/preview/{current['token']}/confirm-preview", BASE + f"/preview/{current['token']}/confirm"]
    disclosure = observed[0][2].json()["body"]
    assert disclosure["can_confirm"] and disclosure["pairs"][0]["duplicate_hint"]["state"] == "UNCHECKED"
    assert observed[1][1]["batch_preview_digest"] == disclosure["batch_preview_digest"]
    after = manifest(service)
    assert len(after["transaction_fact"]) == len(after["review_case"]) == len(after["ledger_entry"]) == 1
    service.db.rollback()
    # Deliberate repeat in this contract, not an automatic transport retry.
    repeated = confirm_api_batch(client, current, explicit_new=True)
    assert repeated.status_code == 409
    assert manifest(service) == after


@pytest.mark.parametrize("explicit_new", [False, True])
def test_fixture_helper_never_overrides_existing_evidence(service, client, explicit_new):
    originals = prepare(service.mapper, [row()])
    accept(service.mapper, originals)
    incoming = prepare(service.mapper, [row()], sha="b" * 64)
    current = install(service, incoming, {})
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    service.db.rollback()
    response = confirm_api_batch(client, current, explicit_new=explicit_new)
    assert response.status_code == 200, response.text
    assert response.json()["body"]["new_fact_count"] == 0
    after = manifest(service)
    for table in ("transaction_fact", "review_case", "ledger_entry", "review_transaction_ledger_allocation"):
        assert after[table] == before[table]


@pytest.mark.parametrize("intent", [dict(resolution="bad"), dict(resolution="NEW", acknowledge_new_risk=1),
    dict(resolution="NEW", acknowledge_new_risk="true"), dict(acknowledge_new_risk=True),
    dict(resolution="LINK_EXISTING"), dict(resolution="DUPLICATE"),
    dict(target=dict(kind="FACT", transaction_id=1)),
    dict(resolution="NEW", target=dict(kind="FACT", transaction_id=1)),
    dict(resolution="DUPLICATE", target=dict(kind="FACT", transaction_id=True)),
    dict(resolution="DUPLICATE", target=dict(kind="FACT", transaction_id="1")),
    dict(resolution="DUPLICATE", target=dict(kind="FACT", transaction_id=1, file_id=2)),
    dict(resolution="DUPLICATE", target=dict(kind="ROW", file_id=1, source_row_number=1, transaction_id=2)),
    dict(resolution="LINK_EXISTING", target=dict(kind="FACT", transaction_id=1), account_ref_id=0),
    dict(resolution="DUPLICATE", target=dict(kind="FACT", transaction_id=1), acknowledge_new_risk=True),
    dict(decision="SKIP", resolution="NEW"), dict(decision="SKIP", acknowledge_new_risk=True),
    dict(decision="SKIP", resolution="DUPLICATE", target=dict(kind="FACT", transaction_id=1))])
def test_public_choice_rejects_inconsistent_or_coerced_intent(intent):
    with pytest.raises(ValidationError):
        RowChoice(**(dict(file_id=1, source_row_number=1, decision="ACCEPT") | intent))


@pytest.mark.parametrize("intent", [dict(), dict(resolution="NEW", acknowledge_new_risk=True),
    dict(resolution="LINK_EXISTING", target=dict(kind="FACT", transaction_id=1)),
    dict(resolution="DUPLICATE", target=dict(kind="ROW", file_id=2, source_row_number=1)), dict(decision="SKIP")])
def test_public_choice_preserves_exact_intent_without_implicit_ack(intent):
    result = RowChoice(**(dict(file_id=1, source_row_number=1, decision="ACCEPT") | intent)).model_dump()
    assert result["acknowledge_new_risk"] is intent.get("acknowledge_new_risk", False)
    assert result["resolution"] == intent.get("resolution", "AUTO")
    assert result["target"] == intent.get("target")


def test_row_page_and_batch_disclose_other_pages_and_block_plain_accept(service):
    rows = prepare(service.mapper, [row(n, reference="") for n in range(1, 36)])
    current = install(service, rows, {})
    before = manifest(service)
    first = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())
    second = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest(page_index=2))
    PreviewRowListPO(**first)
    assert first["total"] == second["total"] == 35 and len(first["items"]) == 20 and len(second["items"]) == 15
    assert all(item["duplicate_hint"]["candidate_count"] == 34 for item in first["items"] + second["items"])
    key = next(iter(rows))
    current = choose(service, current, [key])
    disclosure = batch(service, current, [key])
    ImportBatchPreviewPO(**disclosure)
    assert not disclosure["can_confirm"] and disclosure["counts"]["unresolved"] == 1
    assert disclosure["issues"] == [dict(file_id=key[0], source_row_number=key[1], code="IMPORT_REVIEW_REQUIRED")]
    # Full prospective effect stays visible; blocked is not a silent skip.
    assert disclosure["effects"]["by_currency"][0]["cash_out_amount"] == 40000
    assert disclosure["pairs"][0]["duplicate_hint"]["candidate_count"] == 34
    with pytest.raises(TargetIntakeError) as error:
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, [key])))
    assert error.value.code == "IMPORT_REVIEW_REQUIRED"
    assert manifest(service) == before


def test_unknown_risk_is_not_zero_and_needs_explicit_new_preflight(service):
    rows = prepare(service.mapper, [row(reference="", account={}, source_account={})])
    key = next(iter(rows))
    current = install(service, rows, {})
    page = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())
    assert page["items"][0]["duplicate_hint"]["state"] == "UNCHECKED"
    assert page["items"][0]["duplicate_hint"]["candidate_count"] is None
    current = choose(service, current, [key], resolution="NEW", acknowledge_new_risk=True)
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="IMPORT_REVIEW_REQUIRED"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, [key])))
    assert manifest(service) == before
    disclosure = batch(service, current, [key])
    assert disclosure["can_confirm"] and disclosure["effects"]["new_original_defaults"][0]["account_ref_id"] == 0
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, [key],
        batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["new_fact_count"] == 1


def test_explicit_new_risk_count_changes_invalidate_locked_preview(service):
    current, key, _accepted = paired(service)
    current = choose(service, current, [key], resolution="NEW", acknowledge_new_risk=True)
    disclosure = batch(service, current, [key])
    assert disclosure["can_confirm"] and disclosure["pairs"][0]["duplicate_hint"]["candidate_count"] == 1
    extra = prepare(service.mapper, [row(reference="")], sha="c" * 64)
    accept(service.mapper, extra)
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, [key],
            batch_preview_digest=disclosure["batch_preview_digest"])))
    assert manifest(service) == before
    refreshed = batch(service, current, [key])
    assert refreshed["pairs"][0]["duplicate_hint"]["candidate_count"] == 2
    assert refreshed["batch_preview_digest"] != disclosure["batch_preview_digest"]


@pytest.mark.parametrize("resolution", ["LINK_EXISTING", "DUPLICATE"])
def test_real_http_manual_pair_requires_batch_preview_and_preserves_original(service, client, resolution):
    if resolution == "LINK_EXISTING":
        current, key, accepted = paired(service)
        id = accepted["processed_rows"][0]["transaction_id"]
    else:
        id, rows, _choices = existing_pair(service)
        key = next(iter(rows))
        current = install(service, rows, {})
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    response = client.put(BASE + f"/preview/{current['token']}", json=dict(expected_updated_time=current["updated_time"].isoformat(),
        choices=[dict(file_id=key[0], source_row_number=key[1], decision="ACCEPT", resolution=resolution,
            target=dict(kind="FACT", transaction_id=id))]))
    assert response.status_code == 200, response.text
    current = response.json()["body"]
    assert manifest(service) == before  # PUT is only process-local intent.
    body = input_for(current, [key])
    refused = client.post(BASE + f"/preview/{current['token']}/confirm", json=body)
    assert refused.status_code == 422 and refused.json()["body"]["code"] == "IMPORT_REVIEW_REQUIRED"
    assert manifest(service) == before
    disclosure = client.post(BASE + f"/preview/{current['token']}/confirm-preview", json=body)
    assert disclosure.status_code == 200, disclosure.text
    preview = disclosure.json()["body"]
    assert preview["can_confirm"] and preview["pairs"][0]["resolution"] == resolution
    assert manifest(service) == before
    # End the fixture's independent read transaction before observing the POST.
    service.db.rollback()
    result = client.post(BASE + f"/preview/{current['token']}/confirm", json=body | dict(batch_preview_digest=preview["batch_preview_digest"]))
    assert result.status_code == 200, result.text
    result = result.json()["body"]
    assert result["new_fact_count"] == (1 if resolution == "DUPLICATE" else 0)
    assert result["processed_rows"][0]["resolution_effect"] == ("DUPLICATE_ZERO" if resolution == "DUPLICATE" else "EVIDENCE_ONLY")
    after = manifest(service)
    for table in ("transaction_fact", "review_case", "ledger_entry", "review_transaction_ledger_allocation"):
        assert after[table][:len(before[table])] == before[table]


def test_row_target_saved_on_other_page_is_rechecked_and_never_implicitly_selected(service):
    rows, _choices, anchor, source = local_pair(service)
    current = install(service, rows, {})
    current = choose(service, current, [anchor], resolution="NEW", acknowledge_new_risk=True)
    # Anchor is saved in an earlier PUT, not part of this PUT's row changes.
    current = choose(service, current, [source], resolution="DUPLICATE",
        target=dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        batch(service, current, [source])
    assert manifest(service) == before
    disclosure = batch(service, current, [anchor, source])
    assert disclosure["can_confirm"] and disclosure["counts"]["new_real_fact"] == disclosure["counts"]["new_duplicate_fact"] == 1
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, [anchor, source],
        batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["new_fact_count"] == 2 and result["duplicate_fact_count"] == 1


def test_late_row_risk_read_cannot_return_old_context_after_choice_change(service, monkeypatch):
    from backend.service.import_risk_service import ImportRiskService
    rows = prepare(service.mapper, [row(reference="")])
    key = next(iter(rows))
    current = install(service, rows, {})
    original = ImportRiskService.plan
    def changed(self, rows, candidates, keys):
        result = original(self, rows, candidates, keys)
        latest = service.store.get(current["token"])
        latest.choices[key] = dict(decision="SKIP")
        service.store.replace(latest, latest.updated_time)
        return result
    monkeypatch.setattr(ImportRiskService, "plan", changed)
    with pytest.raises(TargetIntakeError, match="PREVIEW_CHANGED"):
        service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())
    assert not service.db.in_transaction()


def test_hint_page_sql_is_bulk_and_empty_page_is_not_an_input_error(service):
    rows = prepare(service.mapper, [row(n, reference="") for n in range(1, 101)])
    current = install(service, rows, {})
    counts = []
    for size in (1, 100):
        service.db.rollback()
        statements = []
        def capture(_connection, _cursor, sql, _parameters, _context, _many):
            statements.append(sql)
        event.listen(service.db.bind, "before_cursor_execute", capture)
        try:
            page = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest(page_size=size))
        finally:
            event.remove(service.db.bind, "before_cursor_execute", capture)
        assert all(item["duplicate_hint"]["candidate_count"] == 99 for item in page["items"])
        assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
        counts.append(len(statements))
    assert counts[0] == counts[1]
    assert service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest(page_index=6))["items"] == []


def test_http_new_consent_creates_extra_real_cash_only_after_complete_disclosure(service, client):
    current, key, _accepted = paired(service)
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    response = client.put(BASE + f"/preview/{current['token']}", json=dict(expected_updated_time=current["updated_time"].isoformat(),
        choices=[dict(file_id=key[0], source_row_number=key[1], decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True)]))
    assert response.status_code == 200, response.text
    current = response.json()["body"]
    assert manifest(service) == before
    body = input_for(current, [key])
    refused = client.post(BASE + f"/preview/{current['token']}/confirm", json=body)
    assert refused.status_code == 422 and refused.json()["body"]["code"] == "IMPORT_REVIEW_REQUIRED"
    disclosure = client.post(BASE + f"/preview/{current['token']}/confirm-preview", json=body).json()["body"]
    assert disclosure["can_confirm"] and disclosure["counts"]["new_real_fact"] == 1
    assert disclosure["pairs"][0]["duplicate_hint"]["candidate_count"] == 1
    assert disclosure["effects"]["by_currency"] == [dict(currency_code="CNY", cash_in_amount=0,
        cash_out_amount=40000, excluded_in_amount=0, excluded_out_amount=0)]
    service.db.rollback()
    result = client.post(BASE + f"/preview/{current['token']}/confirm", json=body | dict(batch_preview_digest=disclosure["batch_preview_digest"]))
    assert result.status_code == 200, result.text
    assert result.json()["body"]["new_fact_count"] == 1
    after = manifest(service)
    assert len(after["transaction_fact"]) == len(after["review_case"]) == len(after["ledger_entry"]) == 2
    assert all(value["cash_amount"] == 40000 and value["entry_type"] == 0 for value in after["ledger_entry"])
    for table in ("transaction_fact", "review_case", "ledger_entry"):
        assert after[table][:len(before[table])] == before[table]


@pytest.mark.parametrize("intent", [dict(resolution="LINK_EXISTING", target=dict(kind="FACT", transaction_id=True)),
    dict(resolution="DUPLICATE", target=dict(kind="FACT", transaction_id=1, file_id=1)),
    dict(resolution="NEW", acknowledge_new_risk="true"), dict(resolution="AUTO", acknowledge_new_risk=True)])
def test_invalid_http_intent_cannot_mutate_cache_or_business_tables(service, client, intent):
    current, key, _accepted = paired(service)
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    response = client.put(BASE + f"/preview/{current['token']}", json=dict(expected_updated_time=current["updated_time"].isoformat(),
        choices=[dict(file_id=key[0], source_row_number=key[1], decision="ACCEPT") | intent]))
    assert response.status_code == 422
    assert import_preview_store.get(current["token"]).choices == {}
    assert manifest(service) == before


def test_new_consent_cannot_overwrite_a_reliably_identified_existing_fact(service):
    original = prepare(service.mapper, [row()])
    accept(service.mapper, original)
    incoming = prepare(service.mapper, [row()], sha="b" * 64)
    key = next(iter(incoming))
    current = install(service, incoming, {})
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        choose(service, current, [key], resolution="NEW", acknowledge_new_risk=True)
    assert service.store.get(current["token"]).choices == {}
    assert manifest(service) == before
