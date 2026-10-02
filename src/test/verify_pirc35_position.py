"""Position and immutable Review UI, entirely fictional isolated SQLite."""
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
from browser_artifact import viewport_evidence


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-position-") as temporary:
        app = prepare_app(Path(temporary))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        with httpx.Client(base_url=base, trust_env=False) as client:
            try:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("fictional app did not start")
                response = client.post("/paam/ledger/v1/account-party", json={"name": "Mock person"})
                response.raise_for_status()
                party_id = response.json()["body"]["id"]
                from datetime import datetime, timezone
                from backend.core import target_database
                from backend.entity import TransactionFact
                from backend.mapper.review_command_mapper import ReviewCommandMapper
                with target_database.SessionLocal() as db:
                    db.add_all([TransactionFact(id=identifier, fact_key=f"mock-browser-{identifier}",
                        occurred_time=datetime(2024, 1, identifier, tzinfo=timezone.utc), cash_direction=direction,
                        amount=amount, currency_code="CNY", account_code="", summary="Mock scenario cash")
                        for identifier, direction, amount in [(3, 2, 60000), (4, 1, 20000), (5, 2, 40000), (6, 2, 30000)]])
                    db.flush()
                    ReviewCommandMapper(db).create_initial_defaults([3, 4, 5, 6])
                    db.commit()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    errors, retired = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: retired.append(request.url) if request.method != "GET" and (
                        request.url.endswith("/paam/ledger/v1/review") or request.url.endswith("/revoke")
                        or request.url.endswith("/restore") or request.url.endswith("/account")) else None)
                    page.goto(base + "/#workbench/position")
                    expect(page.locator("[data-position-workbench]")).to_be_visible()
                    page.locator("[data-position-create]").click()
                    form = page.locator("dialog[open] form")
                    form.locator('[name="title"]').fill("Mock loan position")
                    form.locator('[data-pick-party]').click()
                    party_picker = page.locator("dialog[open] [data-party-picker]")
                    expect(party_picker.locator("[data-picker-word]")).to_be_enabled()
                    party_picker.locator("[data-picker-word]").fill("Mock person")
                    party_picker.locator("[data-picker-search]").click()
                    expect(party_picker.locator("[data-picker-scan-status]")).to_contain_text("本次扫描结束")
                    viewport_evidence(page, "fix-batch2-party-picker")
                    party_picker.locator("[data-picker-id]").first.click()
                    expect(form.locator('[name="party_id"]')).to_have_value(str(party_id))
                    form.locator('[name="usage_scenario"]').select_option("PERSONAL-LENDING")
                    form.locator('[type="submit"]').click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    page.get_by_role("link", name="#1 Mock loan position", exact=True).click()
                    expect(page.locator("[data-position-quantity]")).to_contain_text("UNKNOWN")
                    page.goto(base + "/#workbench/review?position=1&case_code=POS_OPENING")
                    review = page.locator("[data-immutable-review]")
                    expect(review.locator("[data-position-choices]")).to_contain_text("Mock loan position")
                    review.locator("[data-add-leg]").click()
                    leg = review.locator("[data-leg-row]")
                    leg.locator('[name="leg_amount"]').fill("400")
                    leg.locator('[name="occurred_time_timezone"]').select_option('UTC')
                    leg.locator('[name="occurred_time"]').fill("2024-01-01T00:00")
                    leg.locator('[name="basis"]').fill("Mock opening evidence")
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    expect(review.locator("[data-review-impact]")).to_contain_text("UNKNOWN")
                    # An edit invalidates the exact server approval.
                    leg.locator('[name="basis"]').fill("Mock corrected opening evidence")
                    expect(review.locator("[data-review-command]")).to_be_disabled()
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    review.locator("[data-review-command]").click()
                    expect(page.locator("[data-immutable-review]")).to_have_count(0)
                    page.goto(base + "/#workbench/position?id=1")
                    expect(page.locator("[data-position-quantity]")).to_contain_text("400.00 CNY")
                    details = client.get("/paam/financial/v1/position/1/leg/list").json()["body"]["items"]
                    source_id, opening_review_id = details[0]["id"], details[0]["review_id"]
                    page.goto(base + "/#workbench/review?position=1&case_code=POS_POSITION_SETTLE")
                    expect(review.locator("[data-position-choices]")).to_contain_text("Mock loan position")
                    review.locator("[data-add-leg]").click()
                    leg.locator('[name="leg_amount"]').fill("300")
                    leg.locator('[data-find-source]').click()
                    source_picker = page.locator("dialog[open] [data-source-picker]")
                    expect(source_picker.locator("[data-picker-word]")).to_be_enabled()
                    source_picker.locator("[data-picker-word]").fill("corrected opening")
                    source_picker.locator("[data-picker-search]").click()
                    expect(source_picker.locator("[data-picker-scan-status]")).to_contain_text("本次扫描结束")
                    viewport_evidence(page, "fix-batch2-leg-picker")
                    source_picker.locator("[data-picker-id]").first.click()
                    expect(leg.locator('[name="source"]')).to_have_value(str(source_id))
                    leg.locator('[name="occurred_time_timezone"]').select_option('UTC')
                    leg.locator('[name="occurred_time"]').fill("2024-01-02T00:00")
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    review.locator("[data-review-command]").click()
                    expect(review).to_have_count(0)
                    def start(case, fact, position=None):
                        page.goto(base + f"/#workbench/review?facts={fact}&case_code={case}"
                            + (f"&position={position}" if position else ""))
                        expect(review.locator("[data-cash-row]")).to_have_count(1)
                        if position:
                            expect(review.locator("[data-position-choices]")).to_contain_text(f"#{position}")
                    def add_new(title, nature, usage):
                        review.locator("[data-new-position]").click()
                        draft = review.locator("[data-position-draft]")
                        draft.locator('[name="title"]').fill(title)
                        draft.locator('[data-pick-party]').click()
                        page.locator('dialog[open] [data-party-picker] [data-picker-id]').first.click()
                        expect(draft.locator('[data-choice-label]')).to_contain_text('Mock person')
                        draft.locator('[name="type"]').select_option(nature)
                        draft.locator('[name="usage_scenario"]').select_option(usage)
                    def add_leg(amount, direction, source=0):
                        review.locator("[data-add-leg]").click()
                        leg.locator('[name="leg_amount"]').fill(amount)
                        leg.locator('[name="leg_direction"]').select_option(direction)
                        if source:
                            leg.locator('[data-find-source]').click()
                            page.locator(f'dialog[open] [data-source-picker] [data-picker-id="{source}"]').click()
                        leg.locator('[name="occurred_time_timezone"]').select_option('UTC')
                        leg.locator('[name="occurred_time"]').fill("2024-02-01T00:00")
                    def add_link(amount):
                        review.locator("[data-add-link]").click()
                        link = review.locator('[data-link-row]').last
                        link.locator('[name="allocation_ref"]').select_option(review.locator('[data-cash-row]').first.get_attribute('data-draft-id'))
                        link.locator('[name="leg_ref"]').select_option(review.locator('[data-leg-row]').first.get_attribute('data-draft-id'))
                        review.locator('[data-link-row] [name="cash_amount"]').fill(amount)
                    def publish():
                        review.locator("[data-review-preview]").click()
                        expect(review.locator("[data-review-command]")).to_be_enabled()
                        review.locator("[data-review-command]").click()
                        expect(review).to_have_count(0)
                    # AA: 600 payment -> 400 receivable + 200 personal expenditure.
                    start("SHARED_PAYMENT", 3)
                    review.locator('[data-cash-row] [name="cash_amount"]').fill("400")
                    review.locator("[data-add-cash]").click()
                    cash_two = review.locator("[data-cash-row]").nth(1)
                    cash_two.locator('[name="cash_amount"]').fill("200")
                    cash_two.locator('[name="economic_type"]').select_option("TRANSACTION")
                    add_new("Mock AA receivable", "ASSET", "SHARED-SETTLEMENT")
                    add_leg("400", "IN")
                    add_link("400")
                    publish()
                    aa = client.get("/paam/financial/v1/position/2").json()["body"]
                    assert aa["quantity"] == 40000
                    aa_source = client.get("/paam/financial/v1/position/2/leg/list").json()["body"]["items"][0]["id"]
                    start("SHARED_PAYMENT", 4, 2)
                    review.locator('[name="phase"]').select_option("COLLECT_IN")
                    add_leg("200", "OUT", aa_source)
                    add_link("200")
                    publish()
                    assert client.get("/paam/financial/v1/position/2").json()["body"]["quantity"] == 20000
                    # Credit: expense still counts; principal repayment is not another expense.
                    start("POS_CREDIT_PURCHASE", 5)
                    add_new("Mock credit debt", "LIABILITY", "CREDIT-CARD")
                    add_leg("400", "IN")
                    add_link("400")
                    publish()
                    credit_source = client.get("/paam/financial/v1/position/3/leg/list").json()["body"]["items"][0]["id"]
                    start("POS_CREDIT_REPAY", 6, 3)
                    add_leg("300", "OUT", credit_source)
                    add_link("300")
                    publish()
                    assert client.get("/paam/financial/v1/position/3").json()["body"]["quantity"] == 10000
                    # Lost command reply: do not resend a committed immutable Review.
                    page.goto(base + "/#workbench/review?position=3&case_code=POS_POSITION_OPEN")
                    expect(review.locator("[data-position-choices]")).to_contain_text("#3")
                    add_leg("1", "IN")
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    def lose_response(route):
                        response = route.fetch()
                        assert response.ok
                        route.fulfill(status=503, content_type="application/json",
                            body='{"status":503,"message":"synthetic response loss","body":{"code":"RESULT_UNKNOWN"}}')
                    page.route("**/paam/ledger/v1/review/command", lose_response)
                    review.locator("[data-review-command]").click()
                    expect(review.locator("[data-review-status]")).to_contain_text("提交结果未知")
                    expect(review.locator("[data-review-command]")).to_be_disabled()
                    expect(review.locator("[data-review-preview]")).to_be_disabled()
                    page.unroute("**/paam/ledger/v1/review/command", lose_response)
                    assert client.get("/paam/financial/v1/position/3").json()["body"]["quantity"] == 10100
                    page.goto(base + "/#workbench/position?id=1")
                    expect(page.locator("[data-position-quantity]")).to_contain_text("100.00 CNY")
                    # Review inspection is the canonical immutable content, not history/revisions.
                    page.goto(base + "/#details/review")
                    page.locator(f'[data-action="economic-review-detail"][data-id="{opening_review_id}"]').click()
                    expect(page.locator(".inspection-dashboard")).to_contain_text("原始数量腿")
                    page.locator('[data-action="economic-review-transition"]').click()
                    transition = page.locator("dialog[open] [data-review-preview]")
                    transition.click()
                    expect(page.locator("dialog[open] [data-review-command]")).to_be_enabled()
                    page.locator("dialog[open] [data-review-command]").click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    page.goto(base + "/#workbench/position?id=1")
                    expect(page.locator("[data-position-quantity]")).to_contain_text("NEEDS_REVIEW")
                    # Cash-only replacement selects the FULL Fact, not an available residual.
                    page.goto(base + "/#workbench/review?facts=1")
                    expect(review.locator("[data-cash-row]")).to_have_count(1)
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    expect(review.locator("[data-review-impact]")).to_contain_text("整体停用冲突")
                    review.locator("[data-review-command]").click()
                    expect(review).to_have_count(0)
                    page.goto(base + "/#workbench/position?id=1")
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert errors == [], errors
                    assert retired == [], retired
                    browser.close()
                print("PIRC-35 fictional Position and immutable Review browser workflow passed")
            finally:
                server.should_exit = True
                worker.join(timeout=10)
                from backend.core import target_database
                target_database.engine.dispose()


if __name__ == "__main__":
    run()
