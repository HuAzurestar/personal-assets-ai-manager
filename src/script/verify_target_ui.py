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
                timezone_result = page.evaluate("""async () => {
                    const time = await import('/static/js/util/core.js');
                    time.setSelectedTimeZone('Europe/London');
                    const result = {
                        normal: time.zonedISOString('2026-03-30T01:30'),
                        gap: '',
                        overlap: '',
                        newYorkDate: '',
                        tokyoDate: '',
                    };
                    try { time.zonedISOString('2026-03-29T01:30'); }
                    catch (error) { result.gap = error.message; }
                    try { time.zonedISOString('2026-10-25T01:30'); }
                    catch (error) { result.overlap = error.message; }
                    time.setSelectedTimeZone('America/New_York');
                    result.newYorkDate = time.selectedCalendarDate('2026-09-01T01:00:00Z').iso;
                    time.setSelectedTimeZone('Asia/Tokyo');
                    result.tokyoDate = time.selectedCalendarDate('2026-09-01T01:00:00Z').iso;
                    time.setSelectedTimeZone('Asia/Hong_Kong');
                    return result;
                }""")
                assert timezone_result["normal"] == "2026-03-30T00:30:00.000Z"
                assert "不存在" in timezone_result["gap"]
                assert "不唯一" in timezone_result["overlap"]
                assert timezone_result["newYorkDate"] == "2026-08-31"
                assert timezone_result["tokyoDate"] == "2026-09-01"
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.locator(".module-nav [data-module]")).to_have_count(3)
                assert page.evaluate("location.hash").startswith(
                    "#details/transaction-fact"
                )
                assert page.evaluate(
                    "getComputedStyle(document.querySelector('.module-topbar')).backdropFilter"
                ) == "none"

                page.locator('.topbar-actions [data-page="import"]').click()
                expect(page.locator(".module-heading")).to_be_hidden()
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
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.locator('[data-form="history-filter"]')).to_be_visible()
                expect(page.locator("#page-content").get_by_text("target-ui.csv")).to_be_visible()
                page.locator('[data-action="import-file-detail"]').click()
                history_drawer = page.locator("dialog.detail-view-drawer[open]")
                expect(history_drawer).to_be_visible()
                history_drawer_box = history_drawer.bounding_box()
                assert history_drawer_box is not None
                assert history_drawer_box["width"] >= 1438, history_drawer_box
                expect(history_drawer.locator(".inspection-import-row")).to_have_count(20)
                expect(history_drawer).to_contain_text("来源行处理结果")
                expect(history_drawer).to_contain_text("显示 1–20 / 26 行")
                history_drawer.locator("[data-close]").first.click()
                history_hash = page.evaluate("location.hash")
                expect(page.locator('[data-form="history-filter"]')).to_be_visible()
                source_filter = page.locator(
                    '[data-form="history-filter"] select[name="source_type"]'
                )
                source_filter.select_option("102")
                expect(page.get_by_text("target-ui.csv")).to_be_visible()
                assert page.evaluate("location.hash") == history_hash

                page.locator('nav [data-page="summary"]').click()
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.locator(".month-metrics")).to_be_visible()
                expect(page.locator(".calendar-grid")).to_be_visible()
                expect(page.locator(
                    '[data-form="account-filter"] select[name="account_code"]'
                )).to_have_count(0)
                expect(page.locator(".account-scope")).to_contain_text("Ledger 汇总")
                page.locator('[data-action="account-drilldown"]').click()
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.locator('[data-action="economic-detail"]').first).to_be_visible()
                assert page.evaluate("location.hash").startswith("#details/ledger")
                expect(page.locator('[data-module="details"]')).to_have_class(
                    re.compile(r"active")
                )
                page.locator('[data-action="economic-detail"]').first.click()
                economic_drawer = page.locator("dialog.detail-view-drawer[open]")
                expect(economic_drawer).to_contain_text(
                    "来源事实"
                )
                economic_drawer.locator('[data-action="edit-ledger-account"]').click()
                account_form = page.locator('dialog[open] [data-form="ledger-account"]')
                expect(account_form).to_be_visible()
                account_form.locator('input[name="account_code"]').fill(
                    "ui-ledger-account"
                )
                account_form.locator("button.primary").click()
                expect(page.get_by_text("ui-ledger-account").first).to_be_visible()

                page.locator('.secondary-nav [data-page="ledger-imports"]').click()
                expect(page.locator('[data-action="import-file-detail"]')).to_be_visible()
                page.locator('[data-action="import-file-detail"]').first.click()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_be_visible()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_contain_text(
                    "来源行处理结果"
                )
                page.locator("dialog[open] [data-close]").first.click()

                tag_view_response = page.request.post(
                    f"{base_url}/paam/tag/v1/view",
                    data={"name": "验收分类", "system_name": "acceptance"},
                )
                assert tag_view_response.ok, tag_view_response.text()
                page.locator('.secondary-nav [data-page="ledger-tags"]').click()
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.locator(".tag-manager-head")).to_be_visible()
                page.locator('[data-action="new-view"]').click()
                dictionary_form = page.locator('dialog[open] [data-form="dictionary"]')
                expect(dictionary_form).to_be_visible()
                dictionary_form.locator('[name="name"]').fill("消费 类型")
                expect(dictionary_form.locator('[name="system_name"]')).to_have_value(
                    "xiao_fei_lei_xing"
                )
                dictionary_form.locator('[name="system_name"]').fill("custom_name")
                dictionary_form.locator('[name="name"]').fill("新的名称")
                expect(dictionary_form.locator('[name="system_name"]')).to_have_value(
                    "custom_name"
                )
                page.locator("dialog[open] [data-close]").click()
                tag_card = page.locator(".tag-view-card").first
                tag_card.locator('[data-action="new-tag-inline"]').click()
                inline_tag = tag_card.locator('[data-form="inline-tag"]')
                inline_tag.locator('[name="name"]').fill("餐饮消费")
                expect(inline_tag.locator('[name="system_name"]')).to_have_value(
                    "can_yin_xiao_fei"
                )
                inline_tag.locator('[data-action="cancel-tag"]').click()

                page.locator('.secondary-nav [data-page="ledger"]').click()
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.get_by_text("浏览器测试商户").first).to_be_visible()
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                expect(page.locator(".list-context")).to_have_count(0)
                fact_filter = page.locator('[data-form="fact-filter"]')
                expect(fact_filter.locator('select[name="cash_direction"]')).to_be_visible()
                expect(fact_filter.locator('select[name="currency_code"]')).to_be_visible()
                expect(fact_filter.locator('select[name="sort"]')).to_be_visible()
                expect(fact_filter.locator('select[name="currency_code"] option')).to_have_count(7)
                expect(fact_filter.locator('option[value="CNY_4"]')).to_have_count(0)
                expect(page.locator(".detail-data-table thead")).to_contain_text("发生时间")
                expect(page.locator(".detail-data-table thead")).not_to_contain_text("方向")
                expect(page.locator(".detail-data-table thead")).to_contain_text("交易对手")
                expect(page.locator(".detail-data-table thead")).to_contain_text("本方账户")
                expect(page.locator(".fact-amount").first).to_contain_text("CNY")
                expect(page.locator(".fact-amount").first).not_to_contain_text("¥")
                filter_tops = page.locator(".detail-filter > label, .detail-filter > .detail-filter-field").evaluate_all(
                    "nodes => nodes.map(node => Math.round(node.getBoundingClientRect().top))"
                )
                assert max(filter_tops) - min(filter_tops) <= 1, filter_tops
                fact_filter.locator('[data-action="range-open"]').click()
                range_panel = fact_filter.locator('[data-range-popover]')
                expect(range_panel).to_be_visible()
                expect(range_panel.locator('[data-range-time="hour"] option')).to_have_count(24)
                expect(range_panel.locator('[data-range-time="minute"] option')).to_have_count(60)
                range_panel.locator('[data-range-date="2026-09-01"]').click()
                expect(fact_filter.locator('[name="date_from"]')).to_have_value(
                    "2026-09-01T00:00"
                )
                range_panel.locator('[data-range-endpoint="start"]').click()
                range_panel.locator('[data-range-time="hour"]').select_option("08")
                range_panel.locator('[data-range-time="minute"]').select_option("15")
                expect(fact_filter.locator('[name="date_from"]')).to_have_value(
                    "2026-09-01T08:15"
                )
                range_panel.locator('[data-range-endpoint="end"]').click()
                range_panel.locator('[data-range-date="2026-08-31"]').click()
                expect(range_panel.locator('[data-range-message]')).to_have_text(
                    "开始时间不能晚于结束时间"
                )
                range_panel.locator('[data-range-date="2026-09-17"]').click()
                range_panel.locator('[data-range-time="hour"]').select_option("18")
                range_panel.locator('[data-range-time="minute"]').select_option("45")
                expect(fact_filter.locator('[name="date_to"]')).to_have_value(
                    "2026-09-17T18:45"
                )
                expect(range_panel.locator('[data-range-endpoint="start"]')).to_contain_text("08:15")
                expect(range_panel.locator('[data-range-endpoint="end"]')).to_contain_text("18:45")
                range_panel.locator('[data-range-apply]').click()
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                assert page.evaluate(
                    "new URLSearchParams(location.hash.split('?')[1]).get('date_from')"
                ) == "2026-09-01T08:15"
                fact_filter.locator('select[name="cash_direction"]').select_option("1")
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                assert "cash_direction=1" in page.evaluate("location.hash")
                fact_filter.locator('select[name="currency_code"]').select_option("CNY")
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                fact_filter.locator('select[name="sort"]').select_option("amount.asc")
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                assert "cash_direction=1" in page.evaluate("location.hash")
                assert "sort_field=amount" in page.evaluate("location.hash")
                fact_filter.locator('[data-action="range-open"]').click()
                fact_filter.locator('[data-range-clear]').click()
                expect(page.locator('[data-form="fact-filter"]')).to_be_visible()
                assert "date_from=" not in page.evaluate("location.hash")
                assert "date_to=" not in page.evaluate("location.hash")
                page.locator('[data-action="detail-clear"]').click()
                expect(page.get_by_text("浏览器测试商户").first).to_be_visible()
                page.locator('[data-action="fact-detail"]').first.click()
                expect(page.locator("dialog.detail-view-drawer[open]")).to_contain_text(
                    "交易概览"
                )
                page.locator("dialog[open] [data-close]").first.click()

                page.locator('.secondary-nav [data-page="ledger-reviews"]').click()
                page.locator('[data-page="reviews"]').first.click()
                wizard = page.locator('[data-review-workflow] [data-form="economic-review-create"]')
                expect(wizard).to_be_visible()
                expect(wizard.locator('[data-review-step="1"]')).to_contain_text("选择范围")
                expect(wizard.locator('[data-review-step="2"]')).to_contain_text("配置关系")
                expect(wizard.locator('[data-review-step="3"]')).to_contain_text("检查并生成")
                expect(wizard.locator('[data-review-candidate-filter]')).to_be_visible()
                assert page.evaluate("location.hash").startswith("#workbench/review")
                page.goto(f"{base_url}/#workbench/review/create")
                expect(page.locator('[data-review-workflow]')).to_be_visible()
                assert page.evaluate("location.hash").startswith("#workbench/review")
                assert "/create" not in page.evaluate("location.hash")

                page.goto(f"{base_url}/#summary")
                expect(page.locator(".module-heading")).to_be_hidden()
                expect(page.locator(".month-metrics")).to_be_visible()
                assert page.evaluate("location.hash").startswith("#overview")

                for width, height in (
                    (390, 844),
                    (768, 900),
                    (1440, 900),
                    (1920, 600),
                    (3440, 1440),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    for route_name, selector in (
                        ("details/transaction-fact", '[data-form="fact-filter"]'),
                        ("overview", '.month-metrics'),
                        ("workbench/review", '[data-review-workflow]'),
                    ):
                        page.goto(f"{base_url}/#{route_name}")
                        expect(page.locator(selector)).to_be_visible()
                        expect(page.locator(".module-heading")).to_be_hidden()
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
