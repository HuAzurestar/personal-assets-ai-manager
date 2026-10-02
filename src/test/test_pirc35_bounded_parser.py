from time import monotonic
from zoneinfo import ZoneInfo
import pytest
from backend.error import TargetIntakeError
from backend.parser import bounded_statement
from backend.error.statement_parse import StatementParseError, public_parse_code


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


def test_known_parser_failure_survives_worker_without_private_text():
    with pytest.raises(StatementParseError) as error:
        bounded_statement.parse_statement(b"not,a,statement", "Mock.csv", source="ccb", source_timezone=ZoneInfo("UTC"))
    assert error.value.code == "HEADER_NOT_FOUND"
    assert "not,a,statement" not in str(error.value)


@pytest.mark.parametrize("message,code", [
    ("ZIP password is required or invalid", "ZIP_PASSWORD_INVALID"),
    ("PDF 没有可读取文本；请提供银行电子明细，而非扫描图片", "PDF_TEXT_REQUIRED"),
    ("PDF source does not match selected bank", "SOURCE_MISMATCH"),
    ("Invalid statement workbook", "WORKBOOK_INVALID"),
    ("Mock password private-account-1234567890", "PARSE_ERROR"),
])
def test_public_parse_codes_are_finite_and_never_echo_exceptions(message, code):
    assert public_parse_code(ValueError(message)) == code
    error = StatementParseError(code)
    assert "private-account" not in str(error)
