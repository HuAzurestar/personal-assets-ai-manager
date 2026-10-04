"""Fictional-data browser regression for AI management; no model calls."""
import os
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
    with tempfile.TemporaryDirectory(prefix="paam-ai-ui-") as temp:
        app = prepare_app(Path(temp))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + "/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#settings/ai")
                expect(page.locator("[data-ai-page]")).to_be_visible()
                expect(page.locator("[data-ai-page]")).to_contain_text("生产 Prompt v1")
                page.get_by_role("button", name="创建 Prompt 版本", exact=True).click()
                page.locator('dialog textarea[name="instruction"]').fill("仅根据用途分类。<script>not executed</script>")
                page.get_by_role("button", name="保存草稿", exact=True).click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                draft = page.locator("[data-ai-page] .automation-card").filter(has_text="auto_tag.classify · v2")
                expect(draft).to_contain_text("草稿")
                draft.get_by_role("button", name="虚构样例预览", exact=True).click()
                expect(page.locator("dialog [data-messages]")).to_contain_text("仅根据用途分类。")
                expect(page.locator("dialog [data-messages]")).to_contain_text("不泄露身份")
                assert page.locator('dialog [data-messages] script').count() == 0
                page.locator("dialog select").select_option("NO_CONTEXT")
                page.locator("dialog [data-preview]").click()
                expect(page.locator("dialog [data-messages]")).to_contain_text("清洗后没有业务语义")
                page.locator("dialog [data-ai-close]").click()
                draft.get_by_role("button", name="发布", exact=True).click()
                expect(page.locator("dialog")).to_contain_text("v1 → v2")
                page.locator("dialog [data-publish]").click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                expect(page.locator("[data-ai-page]")).to_contain_text("生产 Prompt v2")
                page.reload()
                expect(page.locator("[data-ai-page]")).to_contain_text("生产 Prompt v2")
                old = page.locator("[data-ai-page] .automation-card").filter(has_text="auto_tag.classify · v1")
                old.get_by_role("button", name="恢复此版本", exact=True).click()
                page.locator("dialog [data-publish]").click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                expect(page.locator("[data-ai-page]")).to_contain_text("生产 Prompt v1")
                usage = httpx.get(base + "/paam/system/v1/ai/usage").json()["body"]
                assert usage["calls"] == 0
                # Real server pages rather than an arbitrary browser-side slice.
                for index in range(21):
                    result = httpx.post(base + "/paam/system/v1/ai/prompt", json={
                        "prompt_key": "auto_tag.classify", "instruction": f"Fictional instruction {index}",
                    })
                    assert result.status_code == 200, result.text
                page.reload()
                page.get_by_role("link", name="下一页", exact=True).click()
                expect(page).to_have_url(base + "/#settings/ai?prompt_page=2")
                expect(page.locator("[data-ai-page]")).to_contain_text("第 2 / 2 页")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.locator('[data-page="settings"]').last.click()
                expect(page.locator("[data-auto-page=settings]")).to_be_visible()
                page.locator('[data-page="ai"]').last.click()
                expect(page.locator("[data-ai-page]")).to_be_visible()
                assert not errors, errors
                browser.close()
            print("PASS AI navigation, draft, escaped fixture preview, publication, rollback, reload, server pagination, mobile bounds and zero model calls")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            app.dependency_overrides.clear()
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
