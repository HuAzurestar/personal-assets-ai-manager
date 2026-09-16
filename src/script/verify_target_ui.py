"""Smoke-test the PIRC-9 workbench in a real browser and disposable SQLite."""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time

import httpx


def run() -> None:
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError as error:
        raise SystemExit(
            "Install the optional browser runner with: "
            ".\\.venv\\Scripts\\python.exe -m pip --isolated install playwright"
        ) from error

    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="paam-target-ui-") as temp:
        database_path = Path(temp) / "target.db"
        os.environ["PAAM_DATA_DIR"] = temp
        os.environ["PAAM_DATABASE_URL"] = f"sqlite:///{database_path}"
        sys.path.insert(0, str(root / "src"))

        import uvicorn

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(
            "backend.target_main:app",
            host="127.0.0.1",
            port=port,
            log_level="error",
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(f"{base_url}/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("target test service did not start")

            statement_rows = [
                "微信支付账单明细列表",
                "交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注",
                *[
                    f"2026-09-12 10:{index:02d}:00,商户消费,浏览器测试商户 {index},午餐,支出,"
                    f"{12 + index / 100:.2f},零钱,支付成功,target-ui-{index},mch-{index},验收"
                    for index in range(1, 27)
                ],
            ]
            statement = ("\n".join(statement_rows) + "\n").encode()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="msedge" if os.name == "nt" else None,
                    headless=True,
                )
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1000},
                    locale="zh-CN",
                )
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base_url)
                expect(page.get_by_role("heading", name="明细")).to_be_visible()
                expect(page.locator(".module-nav [data-module]")).to_have_count(3)
                assert page.evaluate("location.hash").startswith("#details")

                page.locator('.topbar-actions [data-page="import"]').click()
                expect(page.get_by_role("heading", name="导入账单")).to_be_visible()
                expect(page.locator('[data-action="import-source"]')).to_have_count(6)
                expect(page.get_by_role("heading", name="选择数据来源")).to_be_visible()
                expect(page.get_by_role("heading", name="添加账单文件")).to_be_hidden()
                page.locator('[data-action="import-step"][data-step="2"]').last.click()
                expect(page.get_by_role("heading", name="添加账单文件")).to_be_visible()
                if not page.locator('input[name="files"]').count():
                    raise AssertionError(
                        "import page did not render: "
                        f"{page.locator('#page-content').inner_text()}; "
                        f"browser errors: {errors}"
                    )
                page.locator('input[name="files"]').set_input_files({
                    "name": "target-ui.csv",
                    "mimeType": "text/csv",
                    "buffer": statement,
                })
                expect(page.locator("#selected-files")).to_contain_text("target-ui.csv")
                page.locator('[data-action="import-step"][data-step="1"]').first.click()
                expect(page.get_by_role("heading", name="选择数据来源")).to_be_visible()
                page.locator('[data-action="import-step"][data-step="2"]').first.click()
                expect(page.locator("#selected-files")).to_contain_text("target-ui.csv")
                page.locator('[data-action="preview-import"]').click()
                expect(page.get_by_role("heading", name="预览结果")).to_be_visible()
                expect(page.locator(".preview-file-card")).to_contain_text("浏览器测试商户")
                expect(page.locator(".preview-file-card tbody tr")).to_have_count(5)
                page.locator(".back-to-files").click()
                expect(page.get_by_role("heading", name="添加账单文件")).to_be_visible()
                expect(page.locator("#selected-files")).to_contain_text("target-ui.csv")
                page.locator('.import-step[data-step="3"]').click()
                expect(page.get_by_role("heading", name="预览结果")).to_be_visible()
                page.locator('[data-action="detail-preview"]').click()
                drawer = page.locator("dialog.preview-drawer[open]")
                expect(drawer).to_be_visible()
                drawer_box = drawer.bounding_box()
                assert drawer_box is not None
                assert drawer_box["x"] > 500, drawer_box
                assert drawer_box["y"] <= 1, drawer_box
                assert drawer_box["height"] >= 998, drawer_box
                expect(drawer.locator("[data-preview-page-size]")).to_be_visible()
                expect(drawer.locator("[data-preview-page-size] option")).to_have_count(4)
                expect(drawer.locator("tbody tr")).to_have_count(20)
                expect(drawer.locator('[data-action="preview-page-next"]')).to_be_enabled()
                drawer.locator('[data-action="preview-page-next"]').click()
                expect(drawer.locator("tbody tr")).to_have_count(6)
                expect(drawer.locator("[data-preview-range]")).to_have_text(
                    "显示 21–26，共 26 行"
                )
                drawer.locator("[data-preview-page-size]").select_option("30")
                expect(drawer.locator("tbody tr")).to_have_count(26)
                expect(drawer.locator("[data-preview-page-size] option")).to_have_count(4)
                drawer.locator("[data-close]").click()
                expect(page.locator('[data-action="confirm-import"]')).to_be_enabled()
                page.locator('[data-action="confirm-import"]').click()
                expect(page.get_by_role("heading", name="导入记录")).to_be_visible()
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                expect(page.locator('[data-form="history-filter"]')).to_be_visible()
                page.locator('[data-action="import-file-detail"]').click()
                history_drawer = page.locator("dialog.detail-view-drawer[open]")
                expect(history_drawer).to_be_visible()
                history_drawer_box = history_drawer.bounding_box()
                assert history_drawer_box is not None
                assert history_drawer_box["x"] > 500, history_drawer_box
                expect(history_drawer.locator("tbody tr")).to_have_count(20)
                expect(history_drawer).to_contain_text("Transaction Fact")
                expect(
                    history_drawer.locator("th", has_text="摘要")
                ).to_be_visible()
                expect(history_drawer).to_contain_text("显示 20 / 26 条")
                history_drawer.locator("[data-close]").first.click()
                history_hash = page.evaluate("location.hash")
                search = page.locator(
                    '[data-form="history-filter"] input[name="q"]'
                )
                search.click()
                assert page.evaluate("location.hash") == history_hash
                expect(page.get_by_role("heading", name="导入记录")).to_be_visible()
                search.fill("not-present")
                expect(page.get_by_text("没有匹配的导入记录")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash
                assert search.evaluate("node => node === document.activeElement")
                search.fill("target-ui")
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash
                source_filter = page.locator(
                    '[data-form="history-filter"] select[name="source_type"]'
                )
                source_filter.select_option("wechat")
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash

                page.locator('nav [data-page="summary"]').click()
                expect(page.get_by_role("heading", name="本月概览")).to_be_visible()
                expect(page.locator(".month-metrics")).to_be_visible()
                expect(page.locator(".calendar-grid")).to_be_visible()
                account_overview = page.locator(
                    '[data-form="account-filter"] select[name="account_code"]'
                )
                expect(account_overview.locator("option")).to_have_count(1)
                page.locator('[data-action="account-drilldown"]').click()
                expect(page.get_by_role("heading", name="明细")).to_be_visible()
                expect(page.locator('[data-action="economic-detail"]').first).to_be_visible()
                expect(page.locator('[data-module="details"]')).to_have_class(
                    re.compile(r"active")
                )
                page.locator('[data-action="economic-detail"]').first.click()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_contain_text(
                    "来源事实"
                )
                page.locator("dialog[open] [data-close]").first.click()

                page.locator('.detail-tabs [data-page="ledger-imports"]').click()
                expect(page.locator('[data-action="import-file-detail"]')).to_be_visible()
                page.locator('[data-action="import-file-detail"]').first.click()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_be_visible()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_contain_text(
                    "Transaction Fact"
                )
                page.locator("dialog[open] [data-close]").first.click()

                tag_view_response = page.request.post(
                    f"{base_url}/paam/tag/v1/view",
                    data={"name": "验收分类", "system_name": "acceptance"},
                )
                assert tag_view_response.ok, tag_view_response.text()
                page.locator('.detail-tabs [data-page="ledger-tags"]').click()
                expect(page.get_by_role("heading", name="明细")).to_be_visible()
                expect(page.locator(".tag-manager-head")).to_be_visible()

                page.locator('.detail-tabs [data-page="ledger"]').click()
                expect(page.get_by_role("heading", name="明细")).to_be_visible()
                expect(page.get_by_text("浏览器测试商户").first).to_be_visible()
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                page.locator('[data-action="fact-detail"]').first.click()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_contain_text(
                    "规范事实"
                )
                page.locator("dialog[open] [data-close]").first.click()

                page.locator('.detail-tabs [data-page="ledger-reviews"]').click()
                page.locator('[data-action="new-economic-review"]').first.click()
                wizard = page.locator('dialog[open] [data-form="economic-review-create"]')
                expect(wizard).to_be_visible()
                expect(wizard.get_by_role("heading", name="1. 选择事实流水")).to_be_visible()
                expect(wizard.get_by_role("heading", name="2. 定义账本流水")).to_be_visible()
                expect(wizard.get_by_role("heading", name="3. 分配金额")).to_be_visible()
                wizard.locator('[data-close]').click()

                page.goto(f"{base_url}/#summary")
                expect(page.get_by_role("heading", name="本月概览")).to_be_visible()
                assert page.evaluate("location.hash").startswith("#accounts")

                for width, height in (
                    (390, 844),
                    (768, 900),
                    (1440, 900),
                    (1920, 600),
                    (3440, 1440),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    for route_name, heading in (
                        ("details", "明细"),
                        ("accounts", "本月概览"),
                        ("workbench?task=reviews", "工作台"),
                    ):
                        page.goto(f"{base_url}/#{route_name}")
                        expect(page.get_by_role("heading", name=heading)).to_be_visible()
                        overflow = page.evaluate(
                            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                        )
                        assert overflow <= 1, (width, height, route_name, overflow)
                    expect(page.locator(".module-nav")).to_be_visible()
                if errors:
                    raise AssertionError(f"browser errors: {errors}")
                browser.close()

            from sqlalchemy import create_engine, inspect
            from backend.core.target_database import TARGET_TABLE_NAMES

            engine = create_engine(f"sqlite:///{database_path}")
            try:
                actual = set(inspect(engine).get_table_names())
                assert actual == set(TARGET_TABLE_NAMES), actual
            finally:
                engine.dispose()
            print("PASS target UI fact, review, economic flow, and 10-table isolation")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            # SQLite keeps pooled file handles open after the ASGI server exits.
            # Dispose before TemporaryDirectory removes the isolated ledger.
            from backend.core import target_database

            target_database.engine.dispose()


if __name__ == "__main__":
    run()
