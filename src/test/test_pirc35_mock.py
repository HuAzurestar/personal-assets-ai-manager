import importlib.util
import base64
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend.parser.statement_parser import parse_statement
from backend.core import target_database
from backend.entity import LedgerEntry, ReviewAllocation, ReviewCase, TransactionFact
from backend.schema.intake import IntakeConfirmRequest, IntakePreviewRequest
from backend.service.target_intake_service import TargetIntakeService

FIXTURE_DIR = Path(__file__).parent / "fixtures/pirc35"


def test_mock_copies_schema_but_no_source_values():
    spec = importlib.util.spec_from_file_location("pirc35_mock", Path(__file__).parents[1] / "script/pirc35_mock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = {"source_type": "ccb", "filename": "SECRET_NAME.xls", "rows": [{"raw": {
        "记账日期": "SECRET_DATE", "交易时间": "SECRET_TIME", "交易金额": "999999.99",
        "币别": "CNY", "对手信息": "SECRET_NAME", "摘要": "SECRET_SUMMARY",
        "私人列名": "SECRET_VALUE",
    }}]}
    content = module.mock_document(document)
    assert b"SECRET" not in content
    assert b"999999.99" not in content
    assert "私人列名" not in content.decode("utf-8-sig")
    parsed = parse_statement(content, "ccb.csv", source_timezone=ZoneInfo("Asia/Hong_Kong"))
    assert len(parsed["rows"]) == 24
    assert all(row["error"] is None for row in parsed["rows"])
    assert parsed["account"]["number"] == "990000000000001234"


@pytest.mark.parametrize("sample", json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))["fixtures"])
def test_derived_format_import_has_exact_coverage(sample):
    target_database.init_target_db()
    content = (FIXTURE_DIR / sample["file"]).read_bytes()
    with target_database.SessionLocal() as db:
        service = TargetIntakeService(db)
        payload = IntakePreviewRequest(files=[{
            "filename": sample["file"], "content_base64": base64.b64encode(content).decode(),
        }])
        preview = service.preview(payload, source_timezone=ZoneInfo("Asia/Hong_Kong"))
        assert preview["can_confirm"]
        service.confirm(preview["token"], IntakeConfirmRequest(version=preview["version"]))
        count = sample["mock_rows"]
        assert db.scalar(select(func.count(TransactionFact.id))) == count
        assert db.scalar(select(func.count(ReviewCase.id))) == count
        assert db.scalar(select(func.count(LedgerEntry.id))) == count
        facts = dict(db.execute(select(TransactionFact.id, TransactionFact.amount)).all())
        coverage = dict(db.execute(select(ReviewAllocation.transaction_fact_id, func.sum(ReviewAllocation.amount))
                                   .group_by(ReviewAllocation.transaction_fact_id)).all())
        assert coverage == facts
