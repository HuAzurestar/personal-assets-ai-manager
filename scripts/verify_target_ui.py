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

            statement = (
                "微信支付账单明细列表\n"
                "交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n"
                "2026-09-12 10:00:00,商户消费,浏览器测试商户,午餐,支出,12.34,零钱,支付成功,target-ui-1,mch-1,验收\n"
            ).encode()
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
                expect(page.get_by_role("heading", name="导入事实")).to_be_visible()
                page.locator('input[name="files"]').set_input_files({
                    "name": "target-ui.csv",
                    "mimeType": "text/csv",
                    "buffer": statement,
                })
                page.locator('[data-form="import-preview"] button.primary').click()
                expect(page.get_by_role("heading", name="预览结果")).to_be_visible()
                expect(page.locator('[data-action="confirm-import"]')).to_be_enabled()
                page.locator('[data-action="confirm-import"]').click()
                expect(page.get_by_text("target-ui.csv")).to_be_visible()

                page.locator('nav [data-page="ledger"]').click()
                expect(page.get_by_role("heading", name="实际流水")).to_be_visible()
                expect(page.get_by_text("浏览器测试商户")).to_be_visible()
                page.locator('[data-action="detail"]').click()
                expect(page.locator("dialog[open]")).to_contain_text("原始证据")
                expect(page.locator("dialog[open]")).to_contain_text("浏览器测试商户")
                if errors:
                    raise AssertionError(f"browser errors: {errors}")
                browser.close()

            from sqlalchemy import create_engine, inspect
            from app.database import TARGET_TABLE_NAMES

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
            from app import database

            database.engine.dispose()


if __name__ == "__main__":
    run()
