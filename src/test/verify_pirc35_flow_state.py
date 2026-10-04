"""R19 actual list current/history modes and original read-only Review entry."""
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

from browser_artifact import viewport_evidence
from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix="paam-flow-state-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionFact, ReviewCase, LedgerEntry, LedgerAccountParty, LedgerAccount, LedgerAccountRef
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select, func
        with target_database.SessionLocal() as db:
            party = LedgerAccountParty(name="Mock flow owner")
            db.add(party)
            db.flush()
            group = LedgerAccount(name="Mock flow group", party_id=party.id)
            db.add(group)
            db.flush()
            ref = LedgerAccountRef(account_id=group.id, name="Mock flow card", institution="Mock bank",
                reference="990000008888", source_namespace="ccb:statement-v1", source_identity="880000008888", identity_strength=1)
            db.add(ref)
            db.flush()
            rows = [TransactionFact(fact_key=f"mock-flow-state-{i}", occurred_time=datetime(2026, 9, 12, tzinfo=timezone.utc),
                cash_direction=2, amount=1000, currency_code="CNY", account_code="990000008888",
                summary=f"Mock state {i} 1234567890123456 bill@example.invalid") for i in range(24)]
            db.add_all(rows)
            db.flush()
            ids = [row.id for row in rows]
            ReviewCommandMapper(db).create_initial_defaults(ids, account_refs={fid: ref.id for fid in ids})
            db.commit()
            ref_id = ref.id
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
                original = client.get(f"/paam/ledger/v1/flow/{ids[0]}").json()["body"]
                old_rid = original["reviews"][0]["id"]
                intent = dict(new_reviews=[dict(case_code="NORMAL", title="Mock new state Review", parameters=dict(transaction_ids=[ids[0]]))])
                response = client.post("/paam/ledger/v1/review/preview", json=intent)
                assert response.status_code == 200, response.text
                preview = response.json()["body"]
                response = client.post("/paam/ledger/v1/review/command", json=intent | dict(
                    preview_digest=preview["preview_digest"], expected_reviews=preview["expected_reviews"]))
                assert response.status_code == 200, response.text
                rid = response.json()["body"]["created_reviews"][0]["id"]
                new_lid = client.get(f"/paam/ledger/v1/review/{rid}").json()["body"]["ledger_entries"][0]["id"]
            with target_database.SessionLocal() as db:
                before = [db.scalar(select(func.count()).select_from(entity)) for entity in (TransactionFact, ReviewCase, LedgerEntry)]
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                writes, errors, reads = [], [], []
                page.on("request", lambda req: (reads if req.method in ("GET", "HEAD") else writes).append(req.url))
                page.on("pageerror", lambda error: errors.append(str(error)))
                location = f"/#details/ledger?account_ref_id={ref_id}&page_size=100&sort_field=id&sort_order=desc"
                page.goto(base + location)
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(24)
                expect(page.locator('[data-flow-status="historical"]')).to_have_count(0)
                expect(page.locator('[name="active"]')).to_have_value("true")
                new_row = page.locator(f'[data-economic-row="{new_lid}"]')
                expect(new_row).to_contain_text("Mock state 0")
                expect(new_row).to_contain_text("当前有效")
                expect(new_row).to_contain_text("Mock flow owner")
                expect(new_row).to_contain_text("Mock flow group")
                expect(new_row).to_contain_text("****8888")
                assert "990000008888" not in page.locator('[data-flow-read]').inner_text()
                assert "1234567890123456" not in page.locator('[data-flow-read]').inner_text()
                assert "bill@example.invalid" not in page.locator('[data-flow-read]').inner_text()
                assert new_row.bounding_box()["height"] <= 100
                viewport_evidence(page, "fix-r19-current-flow")
                form = page.locator('[data-form="economic-filter"]')
                # This scoped URL adds a real named-account condition chip.
                # The unfiltered default still has the original <=150 bound.
                assert form.bounding_box()["height"] <= 190, form.bounding_box()
                # Default visible mode must not make an untouched filter dirty:
                # exercise a real scheduled refresh and retain its selection.
                count = len([url for url in reads if "/flow/list?" in url])
                page.wait_for_timeout(5500)
                assert len([url for url in reads if "/flow/list?" in url]) > count
                expect(form.locator('[name="active"]')).to_have_value("true")
                new_row.locator('[data-action="flow-review-inspect"]').focus()
                new_row.locator('[data-action="flow-review-inspect"]').press("Enter")
                drawer = page.locator('.inspection-workspace[open]')
                expect(drawer).to_contain_text("Mock new state Review")
                assert drawer.locator('[data-action="economic-review-transition"]').count() == 0
                drawer.locator('[data-close]').click()
                expect(form.locator('[name="active"]')).to_have_value("true")
                form.locator('[name="active"]').select_option("all")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(25)
                expect(page.locator('[data-flow-status="historical"]')).to_have_count(1)
                assert "active=all" in page.url
                old_row = page.locator(f'[data-economic-row="{ids[0]}"]')
                expect(old_row).to_contain_text("已停用历史")
                expect(old_row.locator('[data-action="flow-review-inspect"]')).to_have_attribute("data-id", str(old_rid))
                old_row.locator('[data-action="flow-review-inspect"]').click()
                expect(drawer).to_contain_text("解释已停用")
                assert drawer.locator('[data-action="economic-review-transition"]').count() == 0
                drawer.locator('[data-close]').click()
                viewport_evidence(page, "fix-r19-history-flow")
                page.reload()
                expect(form.locator('[name="active"]')).to_have_value("all")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(25)
                form.locator('[name="active"]').select_option("false")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(1)
                expect(old_row).to_contain_text("已停用历史")
                page.go_back()
                expect(form.locator('[name="active"]')).to_have_value("all")
                page.go_forward()
                expect(form.locator('[name="active"]')).to_have_value("false")
                page.locator('[data-filter-chip="active"]').click()
                expect(form.locator('[name="active"]')).to_have_value("true")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(24)
                # Search current/history has identical explicit mode semantics.
                form.locator('[name="word"]').fill("Mock state")
                form.locator('[name="active"]').select_option("all")
                expect(page.locator('[data-flow-scan-status]')).to_contain_text("本次扫描结束")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(25)
                form.locator('[name="active"]').select_option("true")
                expect(page.locator('[data-flow-scan-status]')).to_contain_text("本次扫描结束")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(24)
                # Clear returns to current, not all; explicit history paginates.
                form.locator('[data-action="detail-clear"]').click()
                expect(form.locator('[name="active"]')).to_have_value("true")
                expect(page.locator('[data-filter-chip]')).to_have_count(0)
                assert form.bounding_box()["height"] <= 150, form.bounding_box()
                page.goto(base + location + "&active=all&page=1")
                page.locator('[data-action="detail-page-size"]').select_option("20")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(20)
                page.locator('[data-action="detail-page"][data-value="2"]').click()
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(5)
                expect(old_row).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                expect(old_row.locator('[data-action="flow-review-inspect"]')).to_be_visible()
                old_row.scroll_into_view_if_needed()
                viewport_evidence(page, "fix-r19-history-narrow")
                assert old_row.bounding_box()["height"] <= 190, old_row.bounding_box()
                assert old_row.locator('[data-action="flow-review-inspect"]').bounding_box()["height"] >= 44
                old_row.locator('[data-action="flow-review-inspect"]').click()
                expect(drawer).to_contain_text("解释已停用")
                drawer.locator('[data-close]').click()
                # Invalid URL mode never broadens the query to all history.
                before_reads = len([url for url in reads if "/flow/list?" in url])
                page.goto(base + location + "&active=invalid")
                expect(page.locator('#page-content')).to_contain_text("流水有效状态")
                assert len([url for url in reads if "/flow/list?" in url]) == before_reads
                assert not writes and not errors, (writes, errors)
                browser.close()
            with target_database.SessionLocal() as db:
                assert before == [db.scalar(select(func.count()).select_from(entity)) for entity in (TransactionFact, ReviewCase, LedgerEntry)]
                assert db.get(ReviewCase, old_rid).status == 1 and db.get(ReviewCase, rid).status == 0
                assert db.get(LedgerEntry, ids[0]).amount == db.get(LedgerEntry, new_lid).amount == 1000
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
    print("PASS current/history Flow list, original read-only Review, scopes/search/navigation and unchanged financial state")
