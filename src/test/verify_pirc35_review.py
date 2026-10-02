"""Real browser: complete impact, exact IDs, actual busy rollback and DUP restore."""
import os
from datetime import datetime, timezone
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-review-") as temporary:
        directory = Path(temporary)
        app = prepare_app(directory)
        from backend.core import target_database
        from backend.entity import TransactionFact, LedgerAccountRef
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        with target_database.SessionLocal() as db:
            db.add_all([LedgerAccountRef(id=identifier, account_id=0, name=f"Mock source {identifier}",
                source_namespace=f"mock-browser-{identifier}", source_identity=f"mock-own-{identifier}",
                identity_strength=1, status="ACTIVE") for identifier in (1, 2)])
            db.add_all([TransactionFact(id=identifier, fact_key=f"mock-audit-{identifier}",
                occurred_time=datetime(2026 if identifier >= 63 else 2024, 1, 1, tzinfo=timezone.utc),
                cash_direction=2, amount=10000, currency_code="CNY", account_code="",
                summary=f"Mock audit cash {identifier}") for identifier in range(3, 65)])
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults(list(range(3, 65)), account_refs={63: 1, 64: 2})
            db.commit()
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
                response = client.post("/paam/ledger/v1/account-party", json={"name": "Mock review owner"})
                assert response.status_code == 200, response.text
                party_id = response.json()["body"]["id"]

                def execute(intent):
                    response = client.post("/paam/ledger/v1/review/preview", json=intent)
                    assert response.status_code == 200, response.text
                    plan = response.json()["body"]
                    assert plan["blocking_issues"] == [], plan
                    response = client.post("/paam/ledger/v1/review/command", json=intent | dict(
                        preview_digest=plan["preview_digest"], expected_reviews=plan["expected_reviews"]))
                    assert response.status_code == 200, response.text
                    return response.json()["body"]

                def counts():
                    with sqlite3.connect(directory / "m2-ui.db") as connection:
                        return tuple(connection.execute(f"SELECT COUNT(id) FROM {table}").fetchone()[0]
                            for table in ("transaction_fact", "review_case", "ledger_entry",
                                "review_transaction_ledger_allocation", "ledger_entry_tag"))

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    errors, position_writes = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: position_writes.append(request.post_data_json)
                        if request.method == "POST" and request.url.endswith("/paam/financial/v1/position") else None)
                    page.goto(base + "/#workbench/position")
                    page.locator("[data-position-create]").click()
                    form = page.locator("dialog[open] form")
                    form.locator('[name="title"]').fill("Mock exact ID")
                    # The normal UI no longer exposes an ID input. Deliberately
                    # tamper with its hidden transport value to retain R18's
                    # final-boundary rejection proof, not as a user workflow.
                    form.locator('[name="party_id"]').evaluate("node => {node.value='9007199254740993'; node.dispatchEvent(new Event('change',{bubbles:true}));}")
                    form.locator('[type="submit"]').click()
                    expect(form.locator('[role="status"]')).to_contain_text("INVALID_ID")
                    expect(form.locator('[type="submit"]')).to_be_enabled()
                    assert position_writes == []
                    form.locator('[data-pick-party]').click()
                    page.locator(f'dialog[open] [data-party-picker] [data-picker-id="{party_id}"]').click()
                    form.locator('[type="submit"]').click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    assert len(position_writes) == 1

                    # Real workflow selection and real server preview: more than 100
                    # mappings must be reachable, not silently truncated.
                    facts = ",".join(str(identifier) for identifier in range(3, 63))
                    page.goto(base + "/#workbench/review?facts=" + facts)
                    review = page.locator("[data-immutable-review]")
                    expect(review.locator("[data-cash-row]")).to_have_count(60, timeout=30000)
                    # R02: the shared Fact picker gives the summary the main width.
                    candidate = review.locator("[data-fact-picker] [data-picker-items] article").first
                    assert candidate.get_attribute("class") == "picker-list-row"
                    assert candidate.locator("span").bounding_box()["width"] > candidate.bounding_box()["width"] * .65
                    assert candidate.locator("button").bounding_box()["width"] < 140
                    assert candidate.bounding_box()["height"] <= 80
                    review.locator('[name="title"]').fill("Mock complete 60-Fact impact")
                    with page.expect_response("**/paam/ledger/v1/review/preview") as response:
                        review.locator("[data-review-preview]").click()
                    plan = response.value.json()["body"]
                    assert plan["blocking_issues"] == []
                    total = len(plan["tag_effect"]["mappings"])
                    assert total > 100, total
                    impact = review.locator("[data-tag-impact]")
                    expect(impact.locator("[data-impact-count]")).to_contain_text(f"完整影响共 {total} 项")
                    expect(impact).to_contain_text("虚构")
                    seen = set()
                    for _ in range((total + 49) // 50):
                        seen.update(int(value) for value in impact.locator("[data-tag-row-index]").evaluate_all(
                            "nodes => nodes.map(node => node.dataset.tagRowIndex)"))
                        expect(review.locator("[data-review-command]")).to_be_enabled()
                        if impact.locator("[data-impact-next]").is_enabled():
                            impact.locator("[data-impact-next]").click()
                    assert seen == set(range(total)), (len(seen), total)
                    impact.locator("[data-impact-kind]").select_option("KEEP")
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    impact.locator("[data-impact-kind]").select_option("")
                    impact.locator("[data-impact-size]").select_option("100")
                    expect(review.locator("[data-review-command]")).to_be_enabled()

                    # Actual SQLite BEGIN IMMEDIATE contention, not an injected reply.
                    before = counts()
                    lock = sqlite3.connect(directory / "m2-ui.db")
                    try:
                        lock.execute("BEGIN IMMEDIATE")
                        with page.expect_response("**/paam/ledger/v1/review/command") as response:
                            review.locator("[data-review-command]").click()
                        failed = response.value
                        assert failed.status == 503 and failed.json()["body"]["code"] == "WRITE_BUSY", failed.text()
                    finally:
                        lock.rollback()
                        lock.close()
                    assert counts() == before
                    expect(review.locator("[data-review-status]")).to_contain_text("本次未提交")
                    expect(review.locator('[name="title"]')).to_have_value("Mock complete 60-Fact impact")
                    expect(review.locator("[data-review-preview]")).to_be_enabled()
                    expect(review.locator("[data-review-command]")).to_be_disabled()
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-command]")).to_be_enabled()
                    review.locator("[data-review-command]").click()
                    expect(review).to_have_count(0, timeout=15000)

                    # Real browser rejects the mutual cycle before publication.
                    page.goto(base + "/#workbench/review?facts=63,64&case_code=DUPLICATE")
                    expect(review.locator("[data-cash-row]")).to_have_count(2)
                    for excluded, kept, ref in [(63, 64, 1), (64, 63, 2)]:
                        review.locator("[data-add-duplicate]").click()
                        duplicate = review.locator("[data-duplicate-row]").last
                        duplicate.locator('[name="transaction_id"]').select_option(str(excluded))
                        duplicate.locator('[data-pick-kept]').click()
                        keeper_picker = page.locator('dialog[open] [data-kept-picker]')
                        keeper_picker.locator('[data-picker-word]').fill(f'Mock audit cash {kept}')
                        keeper_picker.locator('[data-picker-search]').click()
                        expect(keeper_picker.locator('[data-picker-scan-status]')).to_contain_text('本次扫描结束')
                        keeper_picker.locator(f'[data-picker-id="{kept}"]').click()
                        duplicate.locator('[data-pick-duplicate-ref]').click()
                        page.locator(f'dialog[open] [data-ref-picker] [data-picker-id="{ref}"]').click()
                    before = counts()
                    review.locator("[data-review-preview]").click()
                    expect(review.locator("[data-review-impact]")).to_contain_text("INVALID_DUPLICATE")
                    expect(review.locator("[data-review-command]")).to_be_disabled()
                    assert counts() == before

                    # Prepare an immutable historical DUP via public commands;
                    # reactivate it through the real explicit keeper picker.
                    duplicate = dict(case_code="DUPLICATE", parameters=dict(transaction_ids=[63]),
                        account_bindings=[dict(transaction_id=63, account_ref_id=1)],
                        duplicate_transactions=[dict(transaction_id=63, kept_transaction_id=64)])
                    duplicate_id = execute(dict(new_reviews=[duplicate]))["created_reviews"][0]["id"]
                    execute(dict(deactivate_review_ids=[duplicate_id]))
                    page.goto(base + "/#details/review")
                    page.locator(f'[data-action="economic-review-detail"][data-id="{duplicate_id}"]').click()
                    page.locator('[data-action="economic-review-transition"][data-kind="restore"]').click()
                    transition = page.locator("dialog[open] form").last
                    transition.locator("[data-review-preview]").click()
                    expect(transition.locator("[data-review-status]")).to_contain_text("先为每份重复证据选择保留交易")
                    expect(transition.locator("[data-review-command]")).to_be_disabled()
                    transition.locator('[data-activation-pick="63"]').click()
                    page.locator('[data-kept-picker] [data-picker-id="64"]').click()
                    transition.locator("[data-review-preview]").click()
                    expect(transition.locator("[data-review-command]")).to_be_enabled()
                    before = counts()
                    transition.locator("[data-review-command]").click()
                    expect(page.locator("dialog[open] form")).to_have_count(0)
                    detail = client.get(f"/paam/ledger/v1/review/{duplicate_id}").json()["body"]
                    assert detail["status"] == "CONFIRMED"
                    assert counts() == before  # Activation changes state, not old content/IDs.
                    assert errors == [], errors
                    browser.close()
                print(f"PIRC-35 browser passed: complete mappings={total}; exact ID no HTTP; actual WRITE_BUSY rollback/re-preview; mutual DUP rejection and explicit historical restore")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
