"""Smoke-test the PIRC-9 workbench in a real browser and disposable SQLite."""

from __future__ import annotations

import os
from pathlib import Path
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

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="paam-target-ui-") as temp:
        database_path = Path(temp) / "target.db"
        os.environ["PAAM_DATA_DIR"] = temp
        os.environ["PAAM_DATABASE_URL"] = f"sqlite:///{database_path}"
        sys.path.insert(0, str(root))

        import uvicorn

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(
            "app.target_main:app",
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
                expect(page.get_by_role("heading", name="收支概览")).to_be_visible()

                page.locator('nav [data-page="import"]').click()
                expect(page.get_by_role("heading", name="数据导入")).to_be_visible()
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
                expect(page.get_by_role("heading", name="导入历史")).to_be_visible()
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                expect(page.locator('[data-form="history-filter"]')).to_be_visible()
                page.locator('[data-action="batch-rows"]').click()
                history_drawer = page.locator("dialog.batch-detail-drawer[open]")
                expect(history_drawer).to_be_visible()
                history_drawer_box = history_drawer.bounding_box()
                assert history_drawer_box is not None
                assert history_drawer_box["x"] > 500, history_drawer_box
                expect(history_drawer.locator("[data-batch-page-size]")).to_be_visible()
                expect(
                    history_drawer.locator("[data-batch-page-size] option")
                ).to_have_count(4)
                expect(history_drawer.locator("tbody tr")).to_have_count(20)
                expect(history_drawer).to_contain_text("浏览器测试商户")
                expect(
                    history_drawer.locator("th", has_text="摘要 / 备注")
                ).to_be_visible()
                history_drawer.locator('[data-action="batch-page-next"]').click()
                expect(history_drawer.locator("tbody tr")).to_have_count(6)
                history_drawer.locator("[data-batch-page-size]").select_option("30")
                expect(history_drawer.locator("tbody tr")).to_have_count(26)
                history_drawer.locator("[data-close]").click()
                history_hash = page.evaluate("location.hash")
                search = page.locator(
                    '[data-form="history-filter"] input[name="q"]'
                )
                search.click()
                assert page.evaluate("location.hash") == history_hash
                expect(page.get_by_role("heading", name="导入历史")).to_be_visible()
                search.fill("not-present")
                expect(page.get_by_text("没有匹配的导入记录")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash
                assert search.evaluate("node => node === document.activeElement")
                search.fill("target-ui")
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash
                account_filter = page.locator(
                    '[data-form="history-filter"] select[name="account"]'
                )
                account_label = page.locator(".account-chip").first.inner_text()
                account_filter.select_option(label=account_label)
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash

                page.locator('nav [data-page="tags"]').click()
                expect(page.get_by_role("heading", name="标签管理")).to_be_visible()
                assert page.locator(".tag-manager-head").evaluate(
                    "node => getComputedStyle(node).display"
                ) == "flex"
                assert page.locator(".tag-view-list").evaluate(
                    "node => getComputedStyle(node).display"
                ) == "grid"

                page.locator('nav [data-page="ledger"]').click()
                expect(page.get_by_role("heading", name="实际流水")).to_be_visible()
                expect(page.get_by_text("浏览器测试商户").first).to_be_visible()
                assert page.locator(".ledger-filter-main").evaluate(
                    "node => getComputedStyle(node).display"
                ) == "grid"
                assert page.locator(".ledger-card-summary").first.evaluate(
                    "node => getComputedStyle(node).display"
                ) == "grid"
                page.locator('[data-action="ledger-date-toggle"]').click()
                page.locator('[data-action="ledger-date-day"][data-value="2026-09-12"]').click()
                page.locator('[data-action="ledger-date-day"][data-value="2026-09-11"]').click()
                expect(page.locator(".ledger-date-hint")).to_contain_text(
                    "结束时间不能早于开始时间"
                )
                page.locator('[data-action="ledger-date-clear"]').click()
                page.locator('[data-action="ledger-toggle"]').first.click()
                expect(page.locator("[data-ledger-detail]:not([hidden])")).to_contain_text(
                    "构成事实"
                )
                expect(page.locator("[data-ledger-detail]:not([hidden])")).to_contain_text(
                    "浏览器测试商户"
                )
                page.locator('[data-ledger-detail] [data-action="detail"]').click()
                expect(page.locator("dialog[open]")).to_contain_text("原始证据")
                expect(page.locator("dialog[open]")).to_contain_text("浏览器测试商户")
                page.locator("dialog[open] [data-close]").click()

                page.locator('[data-action="ledger-select"]').first.check()
                expect(page.get_by_text("已选择 1 条流水")).to_be_visible()
                page.locator('[data-action="review-selected"]').click()
                wizard = page.locator('dialog[open] [data-form="review-wizard"]')
                expect(wizard).to_be_visible()
                expect(wizard.locator('[name="review_type"]')).to_have_value(
                    "CLASSIFICATION"
                )
                expect(wizard.locator('[name^="role-"]')).to_have_count(1)
                wizard.locator('button[type="submit"]').click()
                review_dialog = page.locator("dialog[open]")
                expect(review_dialog).to_contain_text("待确认")
                expect(
                    review_dialog.locator(
                        '[data-action="review-transition"][data-kind="confirm"]'
                    )
                ).to_be_visible()
                review_dialog.locator(
                    '[data-action="review-transition"][data-kind="confirm"]'
                ).click()
                expect(page.locator("dialog[open]")).to_have_count(0)

                page.locator('nav [data-page="reviews"]').click()
                expect(page.get_by_role("heading", name="统一审查")).to_be_visible()
                expect(page.get_by_text("确认普通收支").first).to_be_visible()
                expect(page.get_by_role("heading", name="待处理流水")).to_be_visible()
                if errors:
                    raise AssertionError(f"browser errors: {errors}")
                browser.close()

            from sqlalchemy import create_engine, inspect
            from app.target_database import TARGET_TABLE_NAMES

            engine = create_engine(f"sqlite:///{database_path}")
            try:
                actual = set(inspect(engine).get_table_names())
                assert actual == set(TARGET_TABLE_NAMES), actual
            finally:
                engine.dispose()
            print("PASS target UI import, ledger, detail, and 11-table isolation")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            # SQLite keeps pooled file handles open after the ASGI server exits.
            # Dispose before TemporaryDirectory removes the isolated ledger.
            from app import target_database

            target_database.engine.dispose()


if __name__ == "__main__":
    run()
