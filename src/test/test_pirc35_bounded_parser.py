from time import monotonic
from zoneinfo import ZoneInfo
import pytest
from backend.error import TargetIntakeError
from backend.parser import bounded_statement


def test_expired_parse_deadline_no_process_and_busy_no_unbounded_queue():
    with pytest.raises(TargetIntakeError) as error:
        bounded_statement.parse_statement(b"Mock bytes", "mock.csv", source_timezone=ZoneInfo("UTC"), deadline=monotonic() - 1)
    assert error.value.code == "PARSE_LIMIT"
    assert bounded_statement._parser_slot.acquire(blocking=False)
    try:
        with pytest.raises(TargetIntakeError) as error:
            bounded_statement.parse_statement(b"Mock bytes", "mock.csv", source_timezone=ZoneInfo("UTC"))
        assert error.value.code == "PARSE_BUSY" and error.value.status_code == 503
    finally:
        bounded_statement._parser_slot.release()


def test_slow_local_parser_is_killed_and_slot_released(monkeypatch):
    real_popen = bounded_statement.subprocess.Popen
    observed = []
    def slow(command, **kwargs):
        process = real_popen([command[0], "-c", "import time; time.sleep(30)"], **kwargs)
        observed.append(process)
        return process
    monkeypatch.setattr(bounded_statement.subprocess, "Popen", slow)
    started = monotonic()
    with pytest.raises(TargetIntakeError) as error:
        bounded_statement.parse_statement(b"Mock bytes", "mock.csv", password="Fictional password",
            source_timezone=ZoneInfo("UTC"), deadline=started + .2)
    assert error.value.code == "PARSE_LIMIT" and monotonic() - started < 3
    assert observed[0].poll() is not None
    assert bounded_statement._parser_slot.acquire(blocking=False)
    bounded_statement._parser_slot.release()
