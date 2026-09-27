"""Disposable UI fixture. Run in a throwaway container, never a live deployment."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import keyring
import uvicorn
from sqlalchemy import update

source = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(source), str(source / "test")]
temporary = TemporaryDirectory(prefix="pirc24-usability-")
os.environ["PAAM_DATA_DIR"] = temporary.name
os.environ["PAAM_DATABASE_URL"] = f"sqlite:///{Path(temporary.name) / 'tag-requests.db'}"
os.environ["PAAM_AUTOTAG_REAL_ANALYSIS"] = "0"
os.environ["PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE"] = "0"

from backend.core import target_database
from backend.entity import AutoTagRule, TagAssignmentRequest, TransactionFact
from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
from test_tag_assignment_request_api import _ledger, _rule, _scan, request_api


async def forbid_provider(*_args, **_kwargs):
    raise AssertionError("A usability fixture must never call a model provider")


ConfiguredLlmAnalyzer.analyze = forbid_provider
keyring.get_password = lambda _service, _username: "fixture-placeholder-not-a-real-key"
fixture = request_api.__wrapped__(Path(temporary.name))
client, sessions, category_id, mood_id, tag_ids = next(fixture)
target_database.SessionLocal = sessions
with sessions() as db:
    target_database.engine = db.get_bind()

food_rule = _rule(sessions, category_id, "按消费内容分类（虚构测试）")
travel_rule = _rule(sessions, category_id, "按商户判断分类（虚构测试）")
disabled_rule = _rule(sessions, category_id, "备用规则（已停用）")
for index in range(4):
    ledger_id = _ledger(sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["unclassified"])
    if index < 3:
        _scan(sessions, food_rule, ledger_id, tag_ids["category"]["food"], "Food")
    if index == 0:
        _scan(sessions, travel_rule, ledger_id, tag_ids["category"]["travel"], "Travel")

with sessions() as db:
    db.execute(update(AutoTagRule).where(AutoTagRule.id == disabled_rule).values(enabled=0))
    db.execute(update(TransactionFact).values(counterparty_name="虚构测试餐厅", summary="午餐套餐，仅供隔离测试"))
    db.execute(update(TagAssignmentRequest).values(reason_summary="商品摘要包含午餐套餐，建议归入餐饮。本条为测试夹具，不是真实模型结果。"))
    db.commit()

from backend.target_main import app


@app.get("/__usability_fixture__")
def fixture_marker():
    return {"fixture": "pirc24-usability-disposable", "real_analysis": False}


if __name__ == "__main__":
    try:
        uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
    finally:
        fixture.close()
        temporary.cleanup()
