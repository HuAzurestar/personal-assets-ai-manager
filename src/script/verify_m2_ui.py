"""Exercise the M2 UI against disposable persisted fixtures in a real browser."""

from __future__ import annotations

import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
from playwright.sync_api import expect, sync_playwright
import uvicorn

from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix="paam-m2-ui-") as directory:
        app = prepare_app(Path(directory))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(f"{base}/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("UI fixture server did not start")
            from backend.core.job_scheduler import JobOutcome, job_scheduler
            from backend.core.schedule_diagnostics import new_run_id
            # Exercise the real scheduler and readonly APIs with synthetic events;
            # this callback has no model, SQL, import, or approval side effects.
            loop = job_scheduler._scheduler._eventloop

            async def prepare_diagnostics():
                release = asyncio.Event()
                entered = asyncio.Event()
                calls = 0

                async def work(context):
                    nonlocal calls
                    calls += 1
                    if calls > 1:
                        return JobOutcome("COMPLETED", outcome_code="NO_DATA")
                    context.progress(phase="CALL", page_total=3, inspected_count=2, submitted_count=2)
                    entered.set()
                    await release.wait()
                    context.emit("OUTPUT_SEMANTIC_INVALID", phase="CALL", ledger_id=1, detail_code="ITEM_MISMATCH")
                    context.progress(phase="FINISH", failed_count=1, request_count=1)
                    return JobOutcome("PARTIAL_FAILURE", "ITEM_FAILURE")

                job_scheduler.register_interval("tag-scan:999", seconds=3600, callback=work)
                for _ in range(12):
                    job_scheduler.diagnostics.record(
                        run_id=new_run_id(), task_key="tag-scan:999", phase="CALL",
                        code="OUTPUT_JSON_INVALID", ledger_id=1,
                    )
                await job_scheduler.notify("tag-scan:999")
                await entered.wait()
                return release

            release = asyncio.run_coroutine_threadsafe(prepare_diagnostics(), loop).result(timeout=5)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                errors = []
                external = []
                commands = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda req: external.append(req.url) if not req.url.startswith(base) else None)
                page.on("request", lambda req: commands.append(req.url) if req.method == "POST" and "batch_" in req.url else None)
                page.goto(f"{base}/#settings/automation")
                page.locator('[data-preserve="runtime"] > summary').click()
                expect(page.locator("[data-auto-runtime]")).to_contain_text("等待队列")
                expect(page.locator("[data-auto-runtime]")).to_contain_text("请求模型 · 已检查 2 / 3")
                expect(page.locator("[data-auto-runtime]")).to_contain_text("导入预览超时清理（系统维护）")
                diagnostics = page.locator("[data-auto-diagnostics]")
                expect(diagnostics.locator('[data-action="copy-diagnostic"]')).to_have_count(10)
                diagnostics.locator('[data-action="diagnostic-page"]').last.click()
                expect(diagnostics).to_contain_text("第 2/2 页")
                loop.call_soon_threadsafe(release.set)
                expect(page.locator("[data-auto-runtime]")).to_contain_text("临时代号与本次请求不一致", timeout=12000)

                async def empty_tick():
                    while job_scheduler.snapshot().tasks[-1].queue_state == "RUNNING":
                        await asyncio.sleep(.01)
                    await job_scheduler.notify("tag-scan:999")

                asyncio.run_coroutine_threadsafe(empty_tick(), loop).result(timeout=5)
                expect(page.locator("[data-auto-runtime]")).to_contain_text("无待分析数据", timeout=12000)
                expect(page.locator("[data-auto-runtime]")).to_contain_text("最近失败（不会被空扫描清除）")
                diagnostics.locator('[name="task_key"]').fill("tag-scan:999")
                diagnostics.locator('[name="severity"]').select_option("INFO")
                diagnostics.locator('button[type="submit"]').click()
                expect(diagnostics).to_contain_text("正常完成，无待分析数据")
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator("[data-disclosure-form]")
                form.locator('[name="advanced"]').check()
                form.locator('[name="boundaries"]').fill('{"CNY":[0,0]}')
                form.locator('button[type="submit"]').click()
                expect(form.locator(".form-error-slot")).to_contain_text("严格递增")
                form.locator('[name="boundaries"]').fill('{"CNY":[0,3000,10000,300000],"CNY_4":[0,290000,500000]}')
                form.locator('[name="acknowledged"]').check()
                # A poll interval elapses while the draft is open: the dialog is not replaced.
                page.wait_for_timeout(5200)
                expect(form.locator('[name="boundaries"]')).to_have_value('{"CNY":[0,3000,10000,300000],"CNY_4":[0,290000,500000]}')
                form.locator('button[type="submit"]').click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                expect(page.locator('[aria-labelledby="automation-disclosure-title"]')).to_contain_text("CNY_4")
                page.reload()
                page.locator('[data-action="disclosure-edit"]').click()
                expect(page.locator('[data-currency-row]').filter(has_text="CNY_4 区间").locator('[data-boundary]').nth(1)).to_have_value("29.0000")
                page.locator('dialog [data-close]').first.click()
                page.locator('[data-action="disclosure-preview"]').click()
                preview_form = page.locator("[data-preview-form]")
                preview_form.locator('button[type="submit"]').click()
                expect(page.locator("[data-disclosure-preview]")).to_contain_text("[0,3000)")
                expect(page.locator(".automation-code").last).not_to_contain_text("amount_units")
                preview_form.locator('[name="amount_mode"]').select_option("2")
                preview_form.locator('button[type="submit"]').click()
                expect(page.locator(".automation-code").last).to_contain_text('"amount_units":2900')
                preview_form.locator('[name="sample"]').select_option("NO_CONTEXT")
                preview_form.locator('button[type="submit"]').click()
                expect(page.locator("[data-disclosure-preview]")).to_contain_text("清洗后不调用模型")
                page.locator('dialog [data-close]').first.click()
                # Development-only demo is absent; actual batch and large-counter
                # contracts remain exercised below and in automation_m2_ui.cjs.
                expect(page.locator('[data-action="interaction-demo"]')).to_have_count(0)
                assert commands == []

                # Changing bands deliberately cancelled old pending fixtures. Restore
                # new synthetic pending rows through the test scanner, never a provider.
                from backend.core import target_database
                from backend.entity import AutoTagRule
                from test_tag_assignment_request_api import _scan
                with target_database.SessionLocal() as db:
                    first = db.get(AutoTagRule, 1)
                    second = db.get(AutoTagRule, 2)
                    assert first.rule_revision == second.rule_revision == 2
                # The seeded IDs are returned by the real list API; no source data is guessed.
                pending_before = page.request.get(f"{base}/paam/tag/v1/assignment_request/list?page_size=100").json()["body"]["items"]
                first_old = next(item for item in pending_before if item["rule_id"] == 1 and item["ledger_id"] == 1)
                second_old = next(item for item in pending_before if item["rule_id"] == 2)
                # Playwright's synchronous driver owns an event loop on this thread.
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(_scan, target_database.SessionLocal, 1, 1, first_old["proposed_tag_id"], first_old["proposed_tag_name"]).result()
                    executor.submit(_scan, target_database.SessionLocal, 1, 2, first_old["proposed_tag_id"], first_old["proposed_tag_name"]).result()
                    executor.submit(_scan, target_database.SessionLocal, 2, 1, second_old["proposed_tag_id"], second_old["proposed_tag_name"]).result()
                page.goto(f"{base}/#workbench/tag-review?status=1")
                selectors = page.locator('[data-tag-request-select]:not(:disabled)')
                expect(selectors).to_have_count(3)
                ids = page.request.get(f"{base}/paam/tag/v1/assignment_request/list?page_size=100").json()["body"]["items"]
                conflict = [item for item in ids if item["ledger_id"] == 1 and item["status"] == 1]
                for item in conflict:
                    page.locator(f'[data-tag-request-select][value="{item["id"]}"]').check()
                page.locator('[data-action="tag-request-batch"][data-operation="approve"]').click()
                expect(page.locator("dialog[open]")).to_contain_text("同范围冲突项")
                assert commands == []
                page.locator('dialog [data-close]').first.click()
                page.locator(f'[data-tag-request-select][value="{conflict[1]["id"]}"]').uncheck()
                page.wait_for_timeout(5400)
                expect(page.locator(f'[data-tag-request-select][value="{conflict[0]["id"]}"]')).to_be_checked()
                expect(page.locator('[data-form="tag-request-filter"] [name="status"]')).to_have_value("1")
                other = next(item for item in ids if item["ledger_id"] == 2 and item["status"] == 1)
                page.locator(f'[data-tag-request-select][value="{other["id"]}"]').check()
                # Whole-command failures provide no per-item result. Even a stale
                # manual source must not be relabelled as a rule revision failure.
                batch_url = "**/paam/tag/v1/assignment_request/batch_approve"
                for code, status, expected in [
                    ("TAG_REQUEST_NOT_FOUND", 404, "至少一条请求不存在"),
                    ("TAG_REQUEST_STALE", 409, "适用条件已变化"),
                    (None, None, "提交结果未知"),
                ]:
                    if code:
                        payload = {"status": status, "message": "private details", "body": {
                            "code": code, "details": {"reason": "MANUAL_TAG_CONFLICT"},
                        }}
                        page.route(batch_url, lambda route, _request, payload=payload, status=status: route.fulfill(
                            status=status, content_type="application/json", body=json.dumps(payload),
                        ))
                    else:
                        page.route(batch_url, lambda route: route.abort())
                    page.locator('[data-action="tag-request-batch"][data-operation="approve"]').click()
                    page.locator("[data-confirm-batch]").click()
                    feedback = page.locator("[data-batch-feedback]")
                    expect(feedback).to_contain_text(expected)
                    expect(feedback).to_contain_text("未获得逐项结果")
                    expect(feedback).not_to_contain_text("规则版本已变化")
                    expect(feedback).not_to_contain_text("Request #")
                    expect(feedback).not_to_contain_text("private details")
                    if code is None:
                        expect(feedback).not_to_contain_text("整批未提交")
                    page.unroute(batch_url)
                    expect(selectors).to_have_count(3)
                assert len(commands) == 3
                # Real backend partial success: both competing requests fail,
                # while the independent Ledger is approved in the same command.
                page.locator(f'[data-tag-request-select][value="{conflict[1]["id"]}"]').check()
                page.locator('[data-action="tag-request-batch"][data-operation="approve"]').click()
                page.locator("[data-confirm-batch]").click()
                expect(page.locator("[data-batch-feedback]")).to_contain_text("成功 1 · 已处理 0 · 失败/未知 2")
                expect(selectors).to_have_count(2)
                assert len(commands) == 4
                page.locator(f'[data-action="tag-request-transition"][data-operation="reject"][data-id="{conflict[0]["id"]}"]').click()
                page.locator("[data-confirm-batch]").click()
                expect(page.locator("[data-batch-feedback]")).to_contain_text("已拒绝，未改变标签")
                expect(selectors).to_have_count(1)
                assert len(commands) == 5
                page.locator('[data-action="tag-request-transition"][data-operation="approve"]').click()
                page.locator("[data-confirm-batch]").click()
                expect(page.locator("[data-batch-feedback]")).to_contain_text("成功 1")
                expect(selectors).to_have_count(0)
                assert len(commands) == 6

                page.goto(f"{base}/#details/auto-rule?rule_id=1")
                stats_help = page.get_by_role("button", name="统计口径", exact=True)
                stats_help.focus()
                expect(stats_help.locator("..").get_by_role("tooltip")).to_be_visible()
                expect(stats_help.locator("..").get_by_role("tooltip")).to_contain_text("非模型准确率")
                page.route("**/paam/system/v1/schedule/status", lambda route: route.abort())
                expect(page.locator("[data-auto-freshness]")).to_contain_text("当前状态未知", timeout=16000)
                page.unroute("**/paam/system/v1/schedule/status")
                expect(page.locator("[data-auto-freshness]")).to_contain_text("已更新", timeout=16000)
                page.goto(f"{base}/#settings/automation")
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator("[data-disclosure-form]")
                current = page.request.get(f"{base}/paam/system/v1/setting/automation").json()["body"]
                response = page.request.put(f"{base}/paam/system/v1/setting/automation", data={
                    "expected_updated_time": current["updated_time"], "disclosure": {"date_granularity": "NONE", "amount_bands": current["disclosure"]["amount_bands"]},
                })
                assert response.status == 200
                form.locator('[data-boundary]').nth(1).fill("31")
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(form.locator(".form-error-slot")).to_contain_text("配置已被修改")
                expect(form).to_be_visible()
                page.locator('dialog [data-close]').first.click()
                page.reload()
                assert page.request.get(f"{base}/paam/system/v1/setting/automation").json()["body"]["disclosure"]["date_granularity"] == "NONE"
                evidence_dir = os.environ.get("PAAM_UI_EVIDENCE_DIR")
                if evidence_dir:
                    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=Path(evidence_dir) / "m2-settings.png", full_page=True)
                assert not errors, errors
                assert not external, external
                assert len(commands) == 6
                browser.close()
            print("PASS M2 persisted policy, preview, draft protection, actual partial batch, 3 injected command failures, approval/rejection, polling/offline, CAS, live scheduler progress, retained failure, diagnostic pagination/filter; provider calls=0")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
