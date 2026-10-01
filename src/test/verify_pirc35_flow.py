"""Actual Edge/browser canonical cash reads, masked search and paged fallback."""
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-flow-") as temporary:
        app = prepare_app(Path(temporary))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            with httpx.Client(base_url=base, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("fictional app did not start")
            from backend.core import target_database
            from backend.entity import TransactionFact, LedgerEntry, TargetTagView, TargetTag, LedgerEntryTag
            from backend.mapper.review_command_mapper import ReviewCommandMapper
            from sqlalchemy import select, func
            with target_database.SessionLocal() as db:
                first = (db.scalar(select(func.max(TransactionFact.id))) or 0) + 1
                ids = list(range(first, first + 4))
                db.add_all([TransactionFact(id=i, fact_key=f"mock-flow-browser-{i}",
                    occurred_time=datetime(2026, 9, 1, tzinfo=timezone.utc), cash_direction=1 if i % 2 else 2,
                    amount=1000, currency_code="CNY", account_code="1234567890123456", summary=
                        "Mock Café literal %_ 1234567890123456" if i == first else "Mock other") for i in ids])
                db.flush()
                ReviewCommandMapper(db).create_initial_defaults(ids)
                db.commit()
                flow_id = db.scalar(select(LedgerEntry.id).where(LedgerEntry.amount == 1000,
                    LedgerEntry.id >= first).order_by(LedgerEntry.id))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                errors, writes = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)
                page.goto(base + "/#details/ledger?sort_field=id&sort_order=desc&page_size=2&word=CAFÉ%20literal%20%25_&search_field=summary")
                expect(page.locator('[data-flow-scan-status]')).to_contain_text("已扫描 2 个候选；找到 0 项；总数未知")
                page.locator('[data-flow-continue]').click()
                expect(page.locator('[data-flow-scan-status]')).to_contain_text("已扫描 4 个候选；找到 1 项")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(1)
                page.locator('[data-flow-continue]').click()
                expect(page.locator('[data-flow-scan-status]')).to_contain_text("已扫描 6 个候选；找到 1 项")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(1)
                page.locator('[data-action="economic-detail"]').click()
                drawer = page.locator('.inspection-workspace[open]')
                expect(drawer).to_contain_text("Mock Café literal %_ ****3456")
                expect(drawer).to_contain_text("来源未识别（ref = 0）")
                expect(drawer).to_contain_text("现金到数量腿归因（不是额外现金）")
                assert "1234567890123456" not in drawer.inner_text()
                drawer.locator('[data-close]').click()
                page.locator('[data-form="economic-filter"] [name="word"]').fill("")
                page.locator('[data-form="economic-filter"] [name="sort"]').select_option("signed_cash_amount.asc")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(2)
                expect(page.locator('[data-form="economic-filter"]')).to_contain_text("带方向金额")
                # Add many archived relation rows in the isolated test DB only.
                with target_database.SessionLocal() as db:
                    view_id = (db.scalar(select(func.max(TargetTagView.id))) or 0) + 1
                    tag_start = (db.scalar(select(func.max(TargetTag.id))) or 0) + 1
                    db.add(TargetTagView(id=view_id, name="Mock archived view", system_name="mock_archived_view", status="ARCHIVED"))
                    db.add_all([TargetTag(id=i, view_id=view_id, name=f"Mock archived {i}", system_name=f"mock_archived_{i}", status="ARCHIVED") for i in range(tag_start, tag_start + 4000)])
                    db.add_all([LedgerEntryTag(ledger_id=flow_id, tag_id=i) for i in range(tag_start, tag_start + 4000)])
                    db.commit()
                page.goto(base + f"/#details/ledger?sort_field=id&sort_order=asc&account_ref_id=0")
                page.locator(f'[data-action="economic-detail"][data-id="{flow_id}"]').click()
                expect(drawer).to_contain_text("完整详情超出预算")
                tags = drawer.locator('[data-flow-relation="tag"]')
                expect(tags.locator('[data-rel-status]')).to_contain_text("第 1 页（仅本页）")
                assert len(tags.locator('.inspection-flow').all()) == 20
                tags.locator('[data-rel-next]').click()
                expect(tags.locator('[data-rel-status]')).to_contain_text("第 2 页（仅本页）")
                tags.locator('[data-rel-prev]').click()
                expect(tags.locator('[data-rel-status]')).to_contain_text("第 1 页（仅本页）")
                assert not errors, errors
                assert not writes, writes
                browser.close()
                print("PASS canonical Flow UI: empty batches, repeated continuation, masked detail, currency-grouped sort and complete paged fallback; zero writes/providers")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
