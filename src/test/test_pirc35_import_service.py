import base64
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import event, func, select, update

from backend.core import target_database
from backend.core.import_preview_store import ImportPreviewStore
from backend.entity import TransactionFact, TransactionImportRow, TransactionImportFile, ReviewCase
from backend.error import TargetIntakeError, ListQueryError
from backend.schema.import_command import ImportConfirmInput, ImportReviseInput, PreviewRowListRequest
from backend.schema.intake import IntakePreviewRequest
from backend.schema.import_batch_read import ImportPreviewPO, ImportConfirmPO, PreviewRowListPO
from backend.service.import_batch_service import ImportBatchService
from test_pirc35_import_batch import prepare, row


@pytest.fixture
def service():
    target_database.ensure_target_schema()
    store = ImportPreviewStore()
    with target_database.SessionLocal() as db:
        yield ImportBatchService(db, store=store)


def preview(service, filename="ccb-2.csv", *, content=None):
    if content is None:
        content = (Path(__file__).parent / "fixtures" / "pirc35" / filename).read_bytes()
    payload = IntakePreviewRequest(files=[dict(filename=filename, content_base64=base64.b64encode(content).decode())])
    return service.preview(payload, source_timezone=ZoneInfo("Asia/Hong_Kong"))


def choose(service, current, keys, decision="ACCEPT", **extra):
    return service.revise(current["token"], ImportReviseInput(expected_updated_time=current["updated_time"],
        choices=[dict(file_id=key[0], source_row_number=key[1], decision=decision, **extra) for key in keys]))


def confirm(service, current, keys, **extra):
    return service.confirm(current["token"], ImportConfirmInput(expected_updated_time=current["updated_time"],
        preview_digest=current["preview_digest"], selected_rows=[dict(file_id=key[0], source_row_number=key[1]) for key in keys]), **extra)


def test_operational_counters_distinguish_no_write_rollback_and_unknown_committed_result(service):
    from backend.core.feature_observability import observability
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)

    def totals():
        return {row["name"]: row["total"] for row in observability.snapshot()["metrics"]}

    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        confirm(service, current | dict(preview_digest="0" * 64), keys)
    assert totals().get("import_batch_rollback_count", 0) == 0

    def fault(stage):
        if stage == "before_commit":
            raise RuntimeError("fictional private bill should never enter logs")

    with pytest.raises(RuntimeError):
        confirm(service, current, keys, fault=fault)
    assert totals()["import_batch_rollback_count"] == 1
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 0

    def lost(stage):
        if stage == "after_commit":
            raise RuntimeError("fictional response lost")

    with pytest.raises(TargetIntakeError, match="RESULT_UNKNOWN"):
        confirm(service, current, keys, fault=lost)
    assert totals()["import_batch_rollback_count"] == 1
    assert totals()["import_result_unknown_count"] == 1
    assert totals()["import_rows"] == 24
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 1
    assert "private bill" not in str(observability.snapshot())


def test_sweep_failure_is_safe_observable_and_does_not_advance_cursor(service, monkeypatch):
    from backend.core.feature_observability import observability
    original = type(service).sweep_cursor

    def fail_write(_):
        raise TargetIntakeError(503, "fictional private SQL", code="WRITE_BUSY")

    monkeypatch.setattr(service, "write_metadata", fail_write)
    with pytest.raises(TargetIntakeError):
        service.sweep_pending()
    assert type(service).sweep_cursor == original
    metrics = {row["name"]: row["total"] for row in observability.snapshot()["metrics"]}
    assert metrics["preview_sweep_failed_count"] == metrics["write_busy_count"] == 1
    assert "private SQL" not in str(observability.snapshot())


@pytest.mark.parametrize("name", ["abc-1.csv", "ccb-2.csv", "wechat-3.csv", "cmb-4.csv", "cmb-5.csv", "alipay-6.csv"])
def test_approved_fictional_fixture_has_explicit_batches_and_current_persisted_progress(service, name):
    current = preview(service, name)
    ImportPreviewPO(**current)
    assert current["counts"]["new"] == 24 and current["issue_count"] == 0
    assert "rows" not in current and "version" not in current
    keys = sorted(service.store.get(current["token"]).rows)
    first = keys[:20]
    current = choose(service, current, first)
    result = confirm(service, current, first)
    ImportConfirmPO(**result)
    assert result["new_fact_count"] == 20 and result["remaining_count"] == 4
    assert service.current(current["token"])["counts"]["processed"] == 20
    assert all(item["created_review_id"] > 0 for item in result["processed_rows"])
    current = service.current(current["token"])
    current = choose(service, current, keys[20:])
    result = confirm(service, current, keys[20:])
    assert result["remaining_count"] == 0
    assert service.db.scalar(select(func.count()).select_from(ReviewCase)) == 24
    # Re-upload is a new preview of current persisted state, never first replay.
    again = preview(service, name)
    assert again["counts"]["processed"] == 24
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        choose(service, again, keys[:1])


def test_no_choice_is_not_implicit_accept_and_input_cas_digest_are_required(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    with pytest.raises(TargetIntakeError, match="ROW_CHOICE_REQUIRED"):
        confirm(service, current, keys)
    new = choose(service, current, keys)
    with pytest.raises(TargetIntakeError, match="PREVIEW_CHANGED"):
        choose(service, current, keys)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        confirm(service, new | dict(preview_digest=current["preview_digest"]), keys)
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 0


def test_preview_rows_standard_shape_limit_filter_and_stale_digest(service):
    current = preview(service)
    page = service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())
    PreviewRowListPO(**page)
    assert set(page) == {"items", "total", "page_index", "page_size"}
    assert len(page["items"]) == 20 and page["total"] == 24
    assert "raw_payload" not in str(page) and "000000" not in str(page)
    key = (page["items"][0]["file_id"], page["items"][0]["source_row_number"])
    new = choose(service, current, [key])
    with pytest.raises(TargetIntakeError, match="PREVIEW_CHANGED"):
        service.row_page(current["token"], current["preview_digest"], PreviewRowListRequest())
    for filter in [dict(key="file_id", op="=", val=True), dict(key="raw_payload", op="=", val="x"),
                   dict(key="classification", op="=", val="made up")]:
        with pytest.raises(ListQueryError):
            service.row_page(new["token"], new["preview_digest"], PreviewRowListRequest(filter=filter))


def test_advisory_timeout_and_file_status_do_not_invalidate_selected_intent(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    state = service.store.get(current["token"])
    state.timeout = timedelta(seconds=-1)
    service.store.replace(state, state.updated_time)
    current = service.current(state.token)
    service.db.execute(update(TransactionImportFile).values(status=3))
    service.db.commit()
    assert service.current(state.token)["preview_digest"] == current["preview_digest"]
    assert service.current(state.token)["timed_out"]
    result = confirm(service, current, keys)
    assert result["new_fact_count"] == 1 and result["files"][0]["status"] == 2


def test_selected_scope_no_all_file_revalidation_and_same_preview_next_batch(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)
    current = choose(service, current, keys[:1])
    calls = []
    match = service.mapper.match
    def recorded(rows, choices):
        calls.append(set(rows))
        return match(rows, choices)
    service.mapper.match = recorded
    confirm(service, current, keys[:1])
    assert calls == [set(keys[:1]), set(keys[:1])]
    current = choose(service, service.current(current["token"]), keys[1:2])
    assert confirm(service, current, keys[1:2])["new_fact_count"] == 1


def test_response_loss_after_commit_no_cache_replay_reupload_proves_current_state(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    def fault(stage):
        if stage == "after_commit":
            raise RuntimeError("mock dropped response")
    with pytest.raises(TargetIntakeError, match="RESULT_UNKNOWN"):
        confirm(service, current, keys, fault=fault)
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 2
    assert service.db.scalar(select(func.count()).select_from(TransactionImportRow)) == 2
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        confirm(service, current, keys)
    again = preview(service)
    assert again["counts"]["processed"] == 2 and again["files"][0]["remaining"] == 22
    assert not hasattr(service.store.get(again["token"]), "result")


def test_before_commit_failure_leaves_all_selected_rows_unprocessed(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    def fault(stage):
        if stage == "before_commit":
            raise RuntimeError("mock rollback")
    with pytest.raises(RuntimeError, match="mock rollback"):
        confirm(service, current, keys, fault=fault)
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 0
    assert service.current(current["token"])["files"][0]["remaining"] == 24


def test_cancel_eviction_marks_only_unshared_pending_not_partial(service):
    current = preview(service)
    same_file = preview(service)
    file_id = current["files"][0]["file_id"]
    service.cancel(current["token"])
    assert service.db.scalar(select(TransactionImportFile.status).where(TransactionImportFile.id == file_id)) == 0
    keys = sorted(service.store.get(same_file["token"]).rows)[:1]
    same_file = choose(service, same_file, keys)
    confirm(service, same_file, keys)
    service.cancel(same_file["token"])
    assert service.db.scalar(select(TransactionImportFile.status).where(TransactionImportFile.id == file_id)) == 2
    with pytest.raises(TargetIntakeError) as error:
        service.current(same_file["token"])
    assert error.value.code == "PREVIEW_UNAVAILABLE"


def test_selected_ref_metadata_change_is_stale_but_unrelated_row_not_rechecked(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    # A competitor creates a reliable ref after the preview. Locked selection
    # must notice automatic binding changed, not silently select its new ID.
    identity = ("ccb:statement-v1", "990000000000001234")
    service.write_metadata(lambda: service.mapper.accounts.create_reliable_refs({identity}))
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        confirm(service, current, keys)
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 0


def test_confirm_pins_only_its_token_get_nonblocking_and_second_submit_busy(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    entered, release, errors = Event(), Event(), []
    def fault(stage):
        if stage == "facts":
            entered.set()
            assert release.wait(1)
    def worker():
        try:
            with target_database.SessionLocal() as db:
                confirm(ImportBatchService(db, store=service.store), current, keys, fault=fault)
        except Exception as error:
            errors.append(error)
    # Do not leave this request's old read snapshot open while another writer runs.
    service.db.rollback()
    thread = Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(1)
        assert service.store.get(current["token"]).status == "CONFIRMING"
        with pytest.raises(TargetIntakeError) as error:
            confirm(service, current, keys)
        assert error.value.code == "PREVIEW_BUSY"
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not errors


def test_startup_and_periodic_sweep_bounded_pk_batches_preserve_completed_files(service):
    from backend.entity.base import utc_now
    now = utc_now()
    service.db.add_all([TransactionImportFile(filename="mock.csv", sha256=f"{n:064x}", status=0,
        updated_time=now - timedelta(minutes=31)) for n in range(1, 1202)])
    service.db.add(TransactionImportFile(filename="completed.csv", sha256="f" * 64, status=2, total_count=1000,
        success_count=500, updated_time=now - timedelta(days=1)))
    service.db.commit()
    statements = []
    def observe(_connection, _cursor, statement, parameters, _context, _many):
        if "UPDATE transaction_import_file" in statement:
            statements.append((statement, parameters))
    event.listen(service.db.bind, "before_cursor_execute", observe)
    try:
        assert service.fail_expired_pending_files() == 1201
    finally:
        event.remove(service.db.bind, "before_cursor_execute", observe)
    assert len(statements) == 4 and all(" IN " in sql and len(params) <= 403 for sql, params in statements)
    completed = service.db.execute(select(TransactionImportFile.__table__).where(TransactionImportFile.sha256 == "f" * 64)).mappings().one()
    assert completed["status"] == 2 and completed["success_count"] == 500
    service.db.add(TransactionImportFile(filename="fresh.csv", sha256="e" * 64, status=0))
    service.db.commit()
    assert service.fail_expired_pending_files() == 0
    assert service.fail_orphaned_pending_files() == 1


def test_invalid_file_error_is_public_code_not_private_parser_text(service, monkeypatch):
    import backend.service.import_batch_service as module
    def parser(*_args, **_kwargs):
        raise ValueError("Mock secret 0000000000123456 password mock-private")
    monkeypatch.setattr(module, "parse_statement", parser)
    current = preview(service)
    assert current["issues"][0]["code"] == "PARSE_ERROR"
    assert "0000000000123456" not in str(current) and "mock-private" not in str(current)
    assert service.db.scalar(select(TransactionImportFile.status)) == 3


def test_current_reads_one_sqlite_snapshot_and_locked_rematch_does_not_nest_begin(service, monkeypatch):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    service.db.rollback()
    observed = []
    progress, match = service.mapper.progress, service.mapper._match

    def in_snapshot(label):
        driver = service.db.connection().connection.driver_connection
        observed.append((label, driver.in_transaction))

    def read_progress(*args, **kwargs):
        in_snapshot("progress")
        return progress(*args, **kwargs)

    def read_match(*args, **kwargs):
        in_snapshot("match")
        return match(*args, **kwargs)

    monkeypatch.setattr(service.mapper, "progress", read_progress)
    monkeypatch.setattr(service.mapper, "_match", read_match)
    current = service.current(current["token"])
    assert observed and all(active for _label, active in observed)
    assert ("progress", True) in observed
    service.db.rollback()
    state = service.store.get(current["token"])
    service.mapper.match(state.rows, state.choices)
    assert ("match", True) in observed
    observed.clear()
    # This replaces the previous read transaction with BEGIN IMMEDIATE. A
    # nested BEGIN would fail rather than silently promoting that old snapshot.
    result = confirm(service, current, keys)
    assert result["new_fact_count"] == 1
    assert ("match", True) in observed and all(active for _label, active in observed)
