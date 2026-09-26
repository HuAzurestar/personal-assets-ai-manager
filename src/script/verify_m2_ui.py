"""Exercise the M2 UI against disposable persisted fixtures in a real browser."""

from __future__ import annotations

import os
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
                expect(page.locator("[data-auto-runtime]")).to_contain_text("共享 FIFO")
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator("[data-disclosure-form]")
                form.locator('[name="boundaries"]').fill('{"CNY":[0,0]}')
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(form.locator(".form-error-slot")).to_contain_text("严格递增")
                form.locator('[name="boundaries"]').fill('{"CNY":[0,3000,10000,300000],"CNY_4":[0,290000,500000]}')
                # A poll interval elapses while the draft is open: the dialog is not replaced.
                page.wait_for_timeout(5200)
                expect(form.locator('[name="boundaries"]')).to_have_value('{"CNY":[0,3000,10000,300000],"CNY_4":[0,290000,500000]}')
                form.locator('button[type="submit"]').click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                expect(page.locator(".disclosure-card")).to_contain_text("CNY_4")
                page.reload()
                expect(page.locator(".disclosure-card")).to_contain_text("290000")
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
                page.locator('[data-action="interaction-demo"]').click()
                expect(page.locator("[data-interaction-result]")).to_contain_text("成功 1 · 已处理 1 · 失败/未知 2")
                page.locator("[data-interaction-scenario]").select_option("LARGE")
                expect(page.locator("[data-interaction-result]")).to_contain_text("9223372036854775807")
                page.locator('dialog [data-close]').first.click()
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
                expect(page.locator("[data-batch-feedback]")).to_contain_text("同一账目和维度只能选择一项")
                assert commands == []
                page.locator(f'[data-tag-request-select][value="{conflict[1]["id"]}"]').uncheck()
                page.wait_for_timeout(5400)
                expect(page.locator(f'[data-tag-request-select][value="{conflict[0]["id"]}"]')).to_be_checked()
                expect(page.locator('[data-form="tag-request-filter"] [name="status"]')).to_have_value("1")
                page.locator('[data-action="tag-request-batch"][data-operation="approve"]').click()
                page.locator("[data-confirm-batch]").click()
                expect(page.locator("[data-batch-feedback]")).to_contain_text("成功 1")
                expect(selectors).to_have_count(1)
                assert len(commands) == 1
                page.locator('[data-action="tag-request-transition"][data-operation="reject"]').click()
                page.locator("[data-confirm-batch]").click()
                expect(page.locator("[data-batch-feedback]")).to_contain_text("已拒绝，未改变标签")
                expect(selectors).to_have_count(0)
                assert len(commands) == 2

                page.goto(f"{base}/#details/auto-rule?rule_id=1")
                expect(page.locator("[data-auto-rule-detail]")).to_contain_text("不是模型准确率")
                page.route("**/paam/system/v1/schedule/status", lambda route: route.abort())
                expect(page.locator("[data-auto-freshness]")).to_contain_text("当前状态未知", timeout=16000)
                page.unroute("**/paam/system/v1/schedule/status")
                expect(page.locator("[data-auto-freshness]")).to_contain_text("业务状态已更新", timeout=16000)
                page.goto(f"{base}/#settings/automation")
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator("[data-disclosure-form]")
                current = page.request.get(f"{base}/paam/system/v1/setting/automation").json()["body"]
                response = page.request.put(f"{base}/paam/system/v1/setting/automation", data={
                    "expected_updated_time": current["updated_time"], "disclosure": {"date_granularity": "NONE", "amount_bands": current["disclosure"]["amount_bands"]},
                })
                assert response.status == 200
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(form.locator(".form-error-slot")).to_contain_text("配置已被修改")
                expect(form).to_be_visible()
                page.locator('dialog [data-close]').first.click()
                page.reload()
                expect(page.locator(".disclosure-card")).to_contain_text("NONE")
                evidence_dir = os.environ.get("PAAM_UI_EVIDENCE_DIR")
                if evidence_dir:
                    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=Path(evidence_dir) / "m2-settings.png", full_page=True)
                assert not errors, errors
                assert not external, external
                assert len(commands) == 2
                browser.close()
            print("PASS M2 persisted policy, preview, draft protection, conflict, approval/rejection, polling/offline, CAS; provider calls=0")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
