"""Real service path with synthetic imports and an offline provider transport."""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_auto_tag_scan import scan_runtime as scan_runtime
from test_llm_privacy import _page

from backend import target_main
from backend.core import target_database
from backend.core.import_preview_store import import_preview_store as target_intake_preview_store
from import_batch_helpers import confirm_api_batch
from backend.core.job_scheduler import JobScheduler
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_scan_mapper import ProtectedScanSource
from backend.router import system as system_router
from backend.service import llm_adapter
from backend.service.auto_tag_schedule_service import AutoTagScheduleService
from backend.service.llm_privacy_service import LlmPrivacyService


def _body(response):
    assert response.status_code == 200, response.text
    return response.json()["body"]


def _wait(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("CRON did not produce the expected observable state")


def _install_runtime(monkeypatch, runtime, completion):
    sessions, engine, _, _ = runtime
    scheduler = JobScheduler()
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    for module in (target_main, system_router):
        monkeypatch.setattr(module, "job_scheduler", scheduler)
        monkeypatch.setattr(module, "AUTOTAG_REAL_ANALYSIS", True)
        monkeypatch.setattr(module, "AUTOTAG_SYNTHETIC_ACCEPTANCE", False)
    monkeypatch.setattr(target_main, "provider_secret_reader", SimpleNamespace(
        get_for_provider=lambda _: "offline-test-key",
    ))
    monkeypatch.setattr(llm_adapter, "_direct_litellm_completion", completion)
    target_intake_preview_store.clear()
    return scheduler


def _import(client, serial):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["微信支付账单明细"])
    writer.writerow([
        "交易时间", "交易对方", "商品", "金额(元)", "收/支", "交易单号", "当前状态", "支付方式",
    ])
    writer.writerow([
        "2026-08-01 12:00:00", "示例餐厅", "午餐；实付29.00元；订单号 AZ12345678",
        "29.00", "支出", f"ordinary-live-import-{serial}", "支付成功", "零钱",
    ])
    preview = _body(client.post("/paam/import/v1/preview", json={"files": [{
        "filename": f"live-{serial}.csv",
        "content_base64": base64.b64encode(stream.getvalue().encode()).decode(),
    }]}))
    _body(confirm_api_batch(client, preview))


def _rule(client, view_id, *, enabled=True):
    return _body(client.post("/paam/tag/v1/auto_rule", json={
        "name": "Live service test", "view_id": view_id, "enabled": enabled,
        "method_config": {"schema_version": 1, "model_id": 9, "prompt": "Choose matching tags"},
        "cron": "*/1 * * * * *", "amount_mode": 1,
    }))


def _set_enabled(client, rule_id, enabled):
    current = _body(client.get(f"/paam/tag/v1/auto_rule/{rule_id}"))
    return _body(client.put(f"/paam/tag/v1/auto_rule/{rule_id}", json={
        **{key: current[key] for key in ("name", "method", "method_config", "cron", "amount_mode")},
        "enabled": enabled, "expected_updated_time": current["updated_time"],
    }))


def _requests(client):
    return _body(client.get("/paam/tag/v1/assignment_request/list"))["items"]


def _assert_provider_count(client, calls, expected):
    if len(calls) != expected:
        events = _body(client.get("/paam/system/v1/schedule/event/list", params={"page_size": 100}))["items"]
        pytest.fail(f"Expected {expected} provider calls, got {len(calls)}; safe events:\n" + "\n".join(
            f"{item['code']} {item['phase']} ledger={item.get('ledger_id')} detail={item.get('detail_code')}" for item in events))


def _response(value):
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps(value, ensure_ascii=False),
    }}]}


def test_import_cron_json_request_reject_restart_and_pause_are_separate(
    scan_runtime, monkeypatch,
):
    calls = []

    def completion(**request):
        payload = json.loads(request["messages"][1]["content"])
        assert re.fullmatch(r"item_[0-9a-f]{32}", payload["item"])
        # Unknown names are reduced to a confirmed-safe business category.
        assert payload["merchant"] == "餐厅"
        assert "午餐" in payload["summary"]
        for forbidden in ("AZ12345678", "29.00", "ordinary-live-import", "account_code", "ledger_id"):
            assert forbidden not in request["messages"][1]["content"]
        calls.append(payload)
        return _response({"item": payload["item"], "decision": "suggestion", "suggestions": [
            {"tag": payload["candidates"][0]["id"], "reason": "Meal purpose"},
        ]})

    scheduler = _install_runtime(monkeypatch, scan_runtime, completion)
    with TestClient(target_main.app) as client:
        rule = _rule(client, scan_runtime[2], enabled=False)
        _import(client, "first")
        assert calls == []  # Import did not execute the classifier.
        _set_enabled(client, rule["id"], True)
        first = _wait(lambda: _requests(client))
        assert len(first) == 1 and first[0]["status"] == 1
        _assert_provider_count(client, calls, 1)
        status = _body(client.get("/paam/system/v1/schedule/status"))
        assert status["tag_scan_guard"] == "REAL_READY"
        assert any(task["task_key"] == f"tag-scan:{rule['id']}" for task in status["tasks"])
        ledger_id = first[0]["ledger_id"]
        ledger = _body(client.get(f"/paam/ledger/v1/flow/{ledger_id}"))["ledger_entry"]
        assert ledger["tags"][0]["tag_system_name"] == "unclassified"

        _body(client.post("/paam/tag/v1/assignment_request/batch_reject", json={
            "request_ids": [first[0]["id"]],
        }))
        _import(client, "second")
        _wait(lambda: len(_requests(client)) == 2)
        time.sleep(1.1)  # Another natural CRON tick must not recreate the rejected request.
        _assert_provider_count(client, calls, 2)
        requests = _requests(client)
        assert sorted(row["status"] for row in requests) == [1, 3]
        assert len({row["ledger_id"] for row in requests}) == 2

    assert scheduler.snapshot().scheduler_state == "STOPPED"
    with TestClient(target_main.app) as client:
        _import(client, "third-after-restart")
        _wait(lambda: len(_requests(client)) == 3)
        _assert_provider_count(client, calls, 3)
        assert len({payload["item"] for payload in calls}) == 3
        _set_enabled(client, rule["id"], False)
        _import(client, "fourth-while-disabled")
        time.sleep(1.2)
        assert len(calls) == 3 and len(_requests(client)) == 3
        assert not any(task.task_key == f"tag-scan:{rule['id']}" for task in scheduler.snapshot().tasks)
    target_intake_preview_store.clear()


@pytest.mark.parametrize("mode", ["bad_json", "wrong_item", "unknown_tag", "insufficient"])
def test_cron_never_persists_invalid_model_output(scan_runtime, monkeypatch, mode):
    calls = []

    def completion(**request):
        payload = json.loads(request["messages"][1]["content"])
        calls.append(payload)
        response = _response({
            "item": "item_from_another_call" if mode == "wrong_item" else payload["item"],
            "decision": "insufficient" if mode == "insufficient" else "suggestion",
            "suggestions": [] if mode == "insufficient" else [{
                "tag": "t999" if mode == "unknown_tag" else "t1", "reason": "Meal purpose",
            }],
        })
        if mode == "bad_json":
            response["choices"][0]["message"]["content"] = "```json\n{}\n```"
        return response

    _install_runtime(monkeypatch, scan_runtime, completion)
    with TestClient(target_main.app) as client:
        rule = _rule(client, scan_runtime[2])
        _import(client, mode)

        def analyzed():
            value = _body(client.get(f"/paam/tag/v1/auto_rule/{rule['id']}"))
            return value if value["analyzed_count"] == "1" else None

        finished = _wait(analyzed)
        assert len(calls) == 1
        assert finished["failed_count"] == ("0" if mode == "insufficient" else "1")
        assert finished["scan_after_ledger_id"] > 0
        assert _requests(client) == []
    target_intake_preview_store.clear()


def test_random_item_is_call_local_and_cross_call_output_is_rejected():
    privacy = LlmPrivacyService()
    source = ProtectedScanSource("OUT", 2900, "CNY", "示例餐厅", "午餐")
    with ThreadPoolExecutor(max_workers=8) as pool:
        payloads = list(pool.map(lambda _: privacy.build_payload(_page(), source), range(512)))
    assert len({payload.item for payload in payloads}) == 512
    assert all(re.fullmatch(r"item_[0-9a-f]{32}", payload.item) for payload in payloads)
    response = _response({"item": payloads[0].item, "decision": "suggestion", "suggestions": [
        {"tag": "t1", "reason": "Meal purpose"},
    ]})
    with pytest.raises(LlmAdapterError) as error:
        llm_adapter.parse_provider_response(response, payloads[1])
    assert error.value.code == "OUTPUT_SEMANTIC_INVALID"
    assert llm_adapter.parse_provider_response(response, payloads[0]).kind == "SUGGESTED"


def test_real_and_synthetic_modes_cannot_be_combined(scan_runtime):
    with pytest.raises(ValueError):
        AutoTagScheduleService(scan_runtime[0], JobScheduler(), None,
                               synthetic_acceptance_enabled=True, real_analysis_enabled=True)
