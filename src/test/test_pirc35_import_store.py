from datetime import timedelta
import threading
import pytest
from backend.core.import_preview_store import ImportPreviewState, ImportPreviewStore
from backend.error import TargetIntakeError


def state(token):
    return ImportPreviewState(token=token, files=[dict(file_id=1)], rows={(1, 5): dict(raw="Mock")})


def test_cache_capacity_evicts_oldest_nonexecuting_and_no_result_field():
    store = ImportPreviewStore(maximum=2)
    first, second, third = state("one"), state("two"), state("three")
    store.put(first)
    store.put(second)
    assert not hasattr(first, "result")
    with store.claim(first.token, first.updated_time):
        removed = store.put(third)
        assert [row.token for row in removed] == ["two"]
        assert store.get("one").status == "CONFIRMING"
    assert store.get("one").status == "READY"
    with pytest.raises(TargetIntakeError) as error:
        store.get("two")
    assert error.value.code == "PREVIEW_UNAVAILABLE" and error.value.status_code == 410


def test_all_pinned_admission_fails_without_eviction_and_get_is_nonblocking():
    store = ImportPreviewStore(maximum=1)
    current = state("one")
    store.put(current)
    with store.claim("one", current.updated_time):
        result = []
        worker = threading.Thread(target=lambda: result.append(store.get("one")))
        worker.start()
        worker.join(timeout=.5)
        assert not worker.is_alive() and result[0].status == "CONFIRMING"
        with pytest.raises(TargetIntakeError) as error:
            store.put(state("two"))
        assert error.value.code == "PREVIEW_BUSY" and error.value.status_code == 503
        with pytest.raises(TargetIntakeError):
            with store.claim("one", current.updated_time):
                pass
        assert store.get("one").token == "one"


def test_cas_soft_timeout_and_claim_commit_refresh_never_save_a_receipt():
    store = ImportPreviewStore()
    current = state("one")
    current.updated_time -= timedelta(minutes=31)
    store.put(current)
    assert store.get("one").timed_out
    with store.claim("one", current.updated_time) as lease:
        lease.state.candidates[(1, 5)] = dict(classification="PROCESSED")
        lease.publish()
    after = store.get("one")
    assert after.updated_time > current.updated_time and not after.timed_out
    assert after.candidates[(1, 5)]["classification"] == "PROCESSED"
    with pytest.raises(TargetIntakeError) as error:
        store.replace(current, current.updated_time)
    assert error.value.code == "PREVIEW_CHANGED"


def test_byte_limits_are_admission_limits_and_failed_admission_preserves_existing():
    current = state("one")
    size = current.byte_size()
    store = ImportPreviewStore(single_bytes=size, total_bytes=size)
    store.put(current)
    huge = state("three")
    huge.rows[(1, 5)]["raw"] = "Mock" * 1000
    with pytest.raises(TargetIntakeError) as error:
        store.put(huge)
    assert error.value.code == "INPUT_LIMIT"
    assert store.get("one").token == "one"
