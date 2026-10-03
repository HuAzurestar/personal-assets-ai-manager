"""Exact, bounded import candidate reads against isolated fictional sources."""
import pytest
from sqlalchemy import event, select, update

from backend.entity import TransactionFact, TransactionImportRow, LedgerEntry, ReviewCase, LedgerAccountRef
from backend.error import TargetIntakeError, TargetEconomicError
from test_pirc35_import_api import client, preview as api_preview, page as api_page, BASE
from test_pirc35_import_service import service
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install, snapshot


def test_match_route_is_typed_read_only_empty_exact_scope(client):
    current = api_preview(client)
    key = api_page(client, current)["items"][0]
    response = client.get(BASE + f"/preview/{current['token']}/match/list", params=dict(
        preview_digest=current["preview_digest"], file_id=key["file_id"],
        source_row_number=key["source_row_number"], kind="SAME_SOURCE"))
    assert response.status_code == 200, response.text
    assert response.json()["body"] == dict(items=[], total=0, page_index=1, page_size=20)


def test_match_openapi_names_the_candidate_and_strict_informed_manual_intent(client):
    schema = client.get("/openapi.json").json()
    route = schema["paths"].get(BASE + "/preview/{token}/match/list")
    assert route is not None
    assert set(route) == {"get"}
    assert "ImportMatchCandidatePO" in schema["components"]["schemas"]
    choice = schema["components"]["schemas"]["RowChoice"]
    assert choice["additionalProperties"] is False
    assert set(choice["properties"]["resolution"]["enum"]) == {"AUTO", "NEW", "LINK_EXISTING", "DUPLICATE"}
    assert choice["properties"]["acknowledge_new_risk"]["type"] == "boolean"
    assert choice["properties"]["acknowledge_new_risk"]["default"] is False
    target = choice["properties"]["target"]["anyOf"][0]
    assert target["discriminator"]["propertyName"] == "kind"


def paired(service, count=1, *, source_changes=None, accepted_changes=None):
    originals = prepare(service.mapper, [row(n, reference="", **(accepted_changes or {})) for n in range(1, count + 1)])
    accepted = accept(service.mapper, originals)
    incoming = prepare(service.mapper, [row(reference="", **(source_changes or {}))], sha="b" * 64)
    current = install(service, incoming, {})
    return current, next(iter(incoming)), accepted


def matches(service, current, key, kind="SAME_SOURCE", **page):
    from backend.schema.import_command import ImportMatchListRequest
    return service.match_page(current["token"], current["preview_digest"], key, kind, ImportMatchListRequest(**page))


def test_same_source_paging_is_complete_masked_and_does_not_write(service):
    current, key, accepted = paired(service, 35, accepted_changes=dict(note="Mock 1234567890123456 demo@example.org"))
    before = snapshot(service)
    first = matches(service, current, key)
    second = matches(service, current, key, page_index=2)
    assert set(first) == {"items", "total", "page_index", "page_size"}
    assert first["total"] == second["total"] == 35
    assert len(first["items"]) == 20 and len(second["items"]) == 15
    assert [item["transaction_id"] for item in first["items"] + second["items"]] == [
        item["transaction_id"] for item in accepted["processed_rows"]]
    assert all(item["eligible_actions"] == ["LINK_EXISTING"] and item["reason_codes"] == []
        for item in first["items"] + second["items"])
    assert all(item["current_review_summaries"][0]["member_count"] == 1 for item in first["items"])
    assert "1234567890123456" not in str(first) and "demo@example.org" not in str(first)
    assert "0000000000123456" not in str(first)
    assert matches(service, current, key, page_index=3)["items"] == []
    assert matches(service, current, key, "CROSS_SOURCE")["total"] == 0
    assert snapshot(service) == before


def test_full_source_identity_not_tail_or_selected_card_decides_kind(service):
    current, key, _accepted = paired(service, source_changes=dict(account=dict(number="9000000000123456"),
        source_account=dict(source_namespace="ccb:statement-v1", source_identity="9000000000123456", identity_strength="RELIABLE")))
    assert matches(service, current, key)["total"] == 0
    result = matches(service, current, key, "CROSS_SOURCE")
    assert result["total"] == 1 and result["items"][0]["eligible_actions"] == ["DUPLICATE"]
    assert result["items"][0]["source_label_masked"] == "ccb ****3456"
    assert result["items"][0]["current_review_summaries"][0]["status"] == "CONFIRMED"


@pytest.mark.parametrize("changes", [dict(amount_minor=-39999), dict(amount_minor=40000),
    dict(currency="CNY_4"), dict(occurred_at="2024-01-01T00:00:00.000001Z")])
def test_candidate_scope_is_exact_in_all_four_core_values(service, changes):
    current, key, _accepted = paired(service, source_changes=changes)
    assert matches(service, current, key)["total"] == 0
    assert matches(service, current, key, "CROSS_SOURCE")["total"] == 0


def test_missing_origin_proof_is_visible_blocked_not_a_false_empty_scope(service):
    current, key, accepted = paired(service)
    id = accepted["processed_rows"][0]["transaction_id"]
    service.db.execute(update(TransactionFact).where(TransactionFact.id == id).values(fact_key="legacy-unproven"))
    service.db.commit()
    for kind in ("SAME_SOURCE", "CROSS_SOURCE"):
        result = matches(service, current, key, kind)
        assert result["total"] == 1
        item = result["items"][0]
        assert item["source_label_masked"] == "来源身份待核对"
        assert item["eligible_actions"] == [] and item["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"]


def test_accepted_source_and_same_file_cannot_be_redecided_as_link(service):
    current, key, accepted = paired(service)
    original = service.mapper.source_rows([(accepted["processed_rows"][0]["file_id"], 1)])
    old_key = next(iter(original))
    from test_pirc35_import_batch import row as source_row
    service.store.remove(current["token"])
    accepted_current = install(service, {old_key: source_row(reference="")}, {})
    result = matches(service, accepted_current, old_key)
    assert result["total"] == 1
    assert result["items"][0]["eligible_actions"] == []
    assert result["items"][0]["reason_codes"] == ["ROWS_ALREADY_PROCESSED"]


@pytest.mark.parametrize("damage", ["source", "account"])
def test_broken_references_are_errors_not_filtered_candidates(service, damage):
    current, key, accepted = paired(service)
    if damage == "source":
        service.db.execute(update(TransactionImportRow).where(TransactionImportRow.id == accepted["processed_rows"][0]["row_id"])
            .values(transaction_import_file_id=999))
    else:
        service.db.execute(update(LedgerEntry).where(LedgerEntry.id == accepted["processed_rows"][0]["created_ledger_id"])
            .values(account_ref_id=999))
    service.db.commit()
    with pytest.raises((TargetIntakeError, TargetEconomicError)) as error:
        matches(service, current, key)
    assert error.value.code in {"RELATION_BROKEN", "ACCOUNT_RELATION_BROKEN"}


def test_current_uncovered_keeper_is_visible_but_not_eligible_for_cross_source(service):
    changes = dict(account=dict(number="9000000000123456"), source_account=dict(source_namespace="ccb:statement-v1",
        source_identity="9000000000123456", identity_strength="RELIABLE"))
    current, key, accepted = paired(service, source_changes=changes)
    service.db.execute(update(ReviewCase).where(ReviewCase.id == accepted["processed_rows"][0]["created_review_id"]).values(status=1))
    service.db.commit()
    item = matches(service, current, key, "CROSS_SOURCE")["items"][0]
    assert item["current_review_summaries"] == []
    assert item["eligible_actions"] == [] and item["reason_codes"] == ["INVALID_DUPLICATE"]


@pytest.mark.parametrize("mode", ["digest", "row", "kind", "late"])
def test_bad_or_changed_preview_context_never_returns_a_candidate_page(service, mode, monkeypatch):
    current, key, _accepted = paired(service)
    if mode == "digest":
        current = current | dict(preview_digest="0" * 64)
    elif mode == "row":
        key = (999, 1)
    elif mode == "late":
        from backend.service.import_match_service import ImportMatchService
        original = ImportMatchService.page
        def changed(instance, state, key, kind, request):
            result = original(instance, state, key, kind, request)
            service.store.replace(state, state.updated_time)
            return result
        monkeypatch.setattr(ImportMatchService, "page", changed)
    with pytest.raises(TargetIntakeError) as error:
        matches(service, current, key, "WRONG" if mode == "kind" else "SAME_SOURCE")
    assert error.value.code == {"digest": "PREVIEW_CHANGED", "row": "PREVIEW_ROW_NOT_FOUND",
        "kind": "INVALID_EVIDENCE_TARGET", "late": "PREVIEW_CHANGED"}[mode]


def test_match_budget_fails_instead_of_returning_first_candidates(service, monkeypatch):
    import backend.mapper.import_match_mapper as module
    current, key, _accepted = paired(service, 3)
    before = snapshot(service)
    monkeypatch.setattr(module, "MAX_EVIDENCE_CANDIDATES", 2)
    with pytest.raises(TargetIntakeError) as error:
        matches(service, current, key)
    assert error.value.code == "IMPORT_MATCH_LIMIT"
    assert error.value.details["action"] == "NARROW_IMPORT_SCOPE"
    assert snapshot(service) == before


def test_page_one_and_one_hundred_use_fixed_batch_reads_no_per_candidate_sql(service):
    current, key, _accepted = paired(service, 100)
    counts = []
    for size in (1, 100):
        service.db.rollback()
        statements = []
        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(service.db.bind, "before_cursor_execute", observe)
        try:
            result = matches(service, current, key, page_size=size)
        finally:
            event.remove(service.db.bind, "before_cursor_execute", observe)
        assert result["total"] == 100 and len(result["items"]) == size
        assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE")) for sql in statements)
        counts.append(len(statements))
    assert counts[0] == counts[1]


def test_same_file_distinct_row_is_listed_but_not_offered_as_evidence_link(service):
    originals = prepare(service.mapper, [row(n, reference="") for n in (1, 2)])
    accept(service.mapper, dict(list(originals.items())[:1]))
    key = next(key for key in originals if key[1] == 2)
    current = install(service, {key: originals[key]}, {})
    item = matches(service, current, key)["items"][0]
    assert item["eligible_actions"] == [] and item["reason_codes"] == ["INVALID_EVIDENCE_TARGET"]


def test_source_without_full_identity_cannot_receive_manual_merge_actions(service):
    current, key, _accepted = paired(service, source_changes=dict(account={}, source_account={}))
    for kind in ("SAME_SOURCE", "CROSS_SOURCE"):
        result = matches(service, current, key, kind)
        assert result["total"] == 1
        assert result["items"][0]["eligible_actions"] == []
        assert result["items"][0]["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"]


def test_effective_duplicate_is_visible_but_cannot_be_a_real_keeper(service):
    from test_pirc35_import_duplicate import existing_pair, confirm_pair, other_row
    from test_pirc35_import_confirm_preview import batch
    a, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    result = confirm_pair(service, current, list(rows), batch(service, current, list(rows)))
    b = result["processed_rows"][0]["transaction_id"]
    service.store.remove(current["token"])
    rows = prepare(service.mapper, [other_row(account="8000000000999999", reference="")], sha="c" * 64)
    current = install(service, rows, {})
    items = {item["transaction_id"]: item for item in matches(service, current, next(iter(rows)), "CROSS_SOURCE")["items"]}
    assert items[a]["eligible_actions"] == ["DUPLICATE"]
    assert items[b]["eligible_actions"] == [] and items[b]["reason_codes"] == ["INVALID_DUPLICATE"]
    assert items[b]["current_review_summaries"][0]["type"] == "OTHER_MANUAL"


def test_count_and_page_origin_proofs_share_a_real_wal_snapshot(service):
    from backend.core import target_database
    from backend.mapper.import_batch_mapper import ImportBatchMapper
    service.db.rollback()
    assert service.db.connection().exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
    service.db.rollback()
    current, key, _accepted = paired(service, 3)
    service.db.rollback()
    triggered = []
    def insert_after_read(_connection, _cursor, statement, _parameters, _context, _many):
        if not triggered and "FROM transaction_fact" in statement and "transaction_fact.occurred_time =" in statement:
            triggered.append(True)
            with target_database.SessionLocal() as writer:
                mapper = ImportBatchMapper(writer)
                extra = prepare(mapper, [row(reference="")], sha="c" * 64)
                accept(mapper, extra)
    event.listen(service.db.bind, "after_cursor_execute", insert_after_read)
    try:
        result = matches(service, current, key)
    finally:
        event.remove(service.db.bind, "after_cursor_execute", insert_after_read)
    assert triggered == [True]
    assert result["total"] == len(result["items"]) == 3
    later = matches(service, current, key)
    assert later["total"] == len(later["items"]) == 4


@pytest.mark.parametrize("extra", [dict(kind="WRONG"), dict(file_id=0), dict(source_row_number=0),
    dict(page_size=101), dict(page_index=0), dict(query="[]"), dict(keyword="Mock"), dict(sorter="[]")])
def test_public_candidate_parameters_reject_invalid_or_unadvertised_capabilities(client, extra):
    current = api_preview(client)
    key = api_page(client, current)["items"][0]
    parameters = dict(preview_digest=current["preview_digest"], file_id=key["file_id"],
        source_row_number=key["source_row_number"], kind="SAME_SOURCE") | extra
    response = client.get(BASE + f"/preview/{current['token']}/match/list", params=parameters)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("key", [(True, 1), (1, True), (2**63, 1), (1, 2**63)])
def test_internal_row_locators_are_strict_sqlite_ids(service, key):
    current, _key, _accepted = paired(service)
    with pytest.raises(TargetIntakeError) as error:
        matches(service, current, key)
    assert error.value.code == "INVALID_EVIDENCE_TARGET"


def test_expired_matching_budget_is_not_reported_as_unknown_financial_commit(service, monkeypatch):
    import backend.mapper.import_batch_mapper as module
    current, key, _accepted = paired(service)
    before = snapshot(service)
    ticks = iter([0.0])
    monkeypatch.setattr(module, "monotonic", lambda: next(ticks, 3.0))
    with pytest.raises(TargetIntakeError) as error:
        matches(service, current, key)
    assert error.value.code == "IMPORT_MATCH_LIMIT" and error.value.status_code == 422
    assert snapshot(service) == before


def test_same_source_supplement_does_not_require_a_new_cash_binding(service):
    current, key, accepted = paired(service)
    ref = service.db.scalar(select(LedgerEntry.account_ref_id).where(
        LedgerEntry.id == accepted["processed_rows"][0]["created_ledger_id"]))
    service.db.execute(update(LedgerAccountRef).where(LedgerAccountRef.id == ref).values(status="CLOSED"))
    service.db.commit()
    item = matches(service, current, key)["items"][0]
    assert item["eligible_actions"] == ["LINK_EXISTING"] and item["reason_codes"] == []
    # Verify the suggested action against the actual internal publication,
    # not just a UI label. Public RowChoice remains closed until risk guards.
    from test_pirc35_import_confirm_preview import batch, input_for
    from backend.schema.import_command import ImportConfirmInput
    state = service.store.get(current["token"])
    service.store.remove(current["token"])
    current = install(service, state.rows, {key: dict(decision="ACCEPT", resolution="LINK_EXISTING",
        target=dict(kind="FACT", transaction_id=accepted["processed_rows"][0]["transaction_id"]))})
    disclosure = batch(service, current, [key])
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, [key],
        batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["new_fact_count"] == 0 and result["manual_linked_count"] == 1
    assert service.db.get(LedgerAccountRef, ref).status == "CLOSED"


def test_real_public_candidate_payload_is_typed_and_all_twenty_tables_unchanged(service, client):
    from backend.core.import_preview_store import import_preview_store
    from backend.schema.import_batch_read import ImportMatchListResponse
    from test_pirc35_import_duplicate import manifest
    current, key, _accepted = paired(service, 3)
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    response = client.get(BASE + f"/preview/{current['token']}/match/list", params=dict(
        preview_digest=current["preview_digest"], file_id=key[0], source_row_number=key[1],
        kind="SAME_SOURCE", page_index=2, page_size=2))
    assert response.status_code == 200, response.text
    ImportMatchListResponse(**response.json())
    body = response.json()["body"]
    assert body["total"] == 3 and len(body["items"]) == 1
    assert set(body["items"][0]) == {"transaction_id", "occurred_time", "amount", "currency_code", "cash_direction",
        "summary_masked", "source_label_masked", "current_review_summaries", "eligible_actions", "reason_codes"}
    assert body["items"][0]["eligible_actions"] == ["LINK_EXISTING"]
    assert manifest(service) == before
