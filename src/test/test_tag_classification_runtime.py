"""Multi-View preset -> real CRON pipeline, with an OFFLINE provider transport."""

import base64
import csv
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "script"))
from configure_tag_classification import install  # noqa: E402
from test_auto_tag_scan import scan_runtime as scan_runtime  # noqa: E402, F401
from test_live_tag_scan import _body, _install_runtime, _response, _set_enabled, _wait  # noqa: E402
from backend import target_main  # noqa: E402
from import_batch_helpers import confirm_api_batch


@pytest.fixture(autouse=True)
def isolate_secret_presence(monkeypatch):
    # Settings GET also queries key presence; never touch a host OS keyring.
    # The independent provider stub is already installed by _install_runtime.
    monkeypatch.setattr(
        "backend.router.dependency.protected_secret_store",
        SimpleNamespace(is_configured=lambda _: True),
    )


def test_preset_is_idempotent_and_import_cron_review_remain_independent(scan_runtime, monkeypatch):
    calls = []

    def completion(**request):
        data = json.loads(request["messages"][1]["content"])
        calls.append(data)
        names = {candidate["name"]: candidate["id"] for candidate in data["candidates"]}
        expected = "医疗" if "医疗" in names else "网购"
        assert data["merchant"] == "京东"
        assert "药品" in data["summary"]
        assert "7654321" not in json.dumps(data)
        return _response({"item": data["item"], "decision": "suggestion", "suggestions": [{
            "tag": names[expected], "reason": data["reason_options"][0],
        }]})

    _install_runtime(monkeypatch, scan_runtime, completion)
    with TestClient(target_main.app) as client:
        before = _body(client.get("/paam/tag/v1/view/list"))["total"]
        assert install(client, 9)["applied"] is False
        assert _body(client.get("/paam/tag/v1/view/list"))["total"] == before
        first = install(client, 9, apply=True)
        assert all(not row["enabled"] for row in first["configuration"])
        assert first == install(client, 9, apply=True)
        assert _body(client.get("/paam/tag/v1/view/list"))["total"] == before + 2
        assert calls == []

        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["微信支付账单明细"])
        writer.writerow(["交易时间", "交易对方", "商品", "金额(元)", "收/支", "交易单号", "当前状态", "支付方式"])
        writer.writerow(["2030-01-02 12:00:00", "京东", "网购药品；订单号 TEST7654321", "19.00", "支出", "fictional-scenario-1", "支付成功", "零钱"])
        preview = _body(client.post("/paam/import/v1/preview", json={"files": [{
            "filename": "fictional-scenario.csv",
            "content_base64": base64.b64encode(stream.getvalue().encode()).decode(),
        }]}))
        # The fictional no-profile bill intentionally represents new cash.
        _body(confirm_api_batch(client, preview, explicit_new=True))
        assert calls == []  # Import never invokes the model or a scan callback.

        for row in first["configuration"]:
            current = _body(client.get(f"/paam/tag/v1/auto_rule/{row['rule_id']}"))
            _body(client.put(f"/paam/tag/v1/auto_rule/{current['id']}", json={
                **{key: current[key] for key in ("name", "method", "method_config", "amount_mode")},
                "enabled": True, "cron": "*/1 * * * * *", "expected_updated_time": current["updated_time"],
            }))
        def finished():
            items = _body(client.get("/paam/tag/v1/assignment_request/list"))["items"]
            return items if len(items) == 2 else None
        requests = _wait(finished)  # Natural shared CRON only; no callback/manual run.
        safe_events = _body(client.get("/paam/system/v1/schedule/event/list", params={"page_size": 100}))["items"]
        if len(calls) != 2:
            pytest.fail(f"Expected 2 provider calls, got {len(calls)}; safe events:\n" + "\n".join(
                f"{item['task_key']} {item['code']} {item['phase']} ledger={item.get('ledger_id')} detail={item.get('detail_code')}" for item in safe_events))
        assert {row["proposed_tag_name"] for row in requests} == {"医疗", "网购"}
        assert all(row["status"] == 1 for row in requests)
        for row in first["configuration"]:
            _set_enabled(client, row["rule_id"], False)
        ledger_id = requests[0]["ledger_id"]
        def tags():
            entry = _body(client.get(f"/paam/ledger/v1/flow/{ledger_id}"))
            return {tag["view_system_name"]: tag["tag_system_name"] for tag in entry["tags"]}
        assert tags()["expense_purpose"] == tags()["purchase_channel"] == "unclassified"
        medical = next(row for row in requests if row["proposed_tag_name"] == "医疗")
        online = next(row for row in requests if row["proposed_tag_name"] == "网购")
        _body(client.post("/paam/tag/v1/assignment_request/batch_approve", json={"request_ids": [medical["id"]]}))
        _body(client.post("/paam/tag/v1/assignment_request/batch_reject", json={"request_ids": [online["id"]]}))
        assert tags()["expense_purpose"] == "medical"
        assert tags()["purchase_channel"] == "unclassified"
        assert len(calls) == 2


def test_preset_conflict_is_detected_before_any_write(scan_runtime, monkeypatch):
    _install_runtime(monkeypatch, scan_runtime, lambda **_: pytest.fail("No provider call allowed"))
    with TestClient(target_main.app) as client:
        _body(client.post("/paam/tag/v1/view", json={"name": "用户的渠道分类", "system_name": "purchase_channel"}))
        before = _body(client.get("/paam/tag/v1/view/list"))
        with pytest.raises(ValueError, match="conflicts"):
            install(client, 9, apply=True)
        assert _body(client.get("/paam/tag/v1/view/list")) == before
