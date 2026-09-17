"""Exercise the four inspection pages in Edge/Chromium against disposable SQLite.

Optional --samples imports local statements only into the temporary database.
Screenshots use synthetic records; source statement contents are never printed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import httpx
from playwright.sync_api import expect, sync_playwright


def verify_filters(page, base):
    """Check the deployed baseline's filters through real list API responses."""
    from urllib.parse import parse_qs, urlparse

    def visit(route, form_name):
        page.goto(f"{base}/#{route}")
        page.wait_for_load_state("networkidle")
        form = page.locator(f'[data-form="{form_name}"]')
        try:
            expect(form).to_be_visible()
        except AssertionError:
            raise AssertionError(page.locator('body').inner_text()[:1200])
        return form

    def change(form, name, value, endpoint):
        with page.expect_response(lambda response: endpoint + "?" in response.url) as pending:
            form.locator(f'select[name="{name}"]').select_option(value)
        response = pending.value
        assert response.ok, response.status
        body = response.json()["body"]
        query = parse_qs(urlparse(response.url).query)
        page.wait_for_load_state("networkidle")
        expect(form.locator(f'select[name="{name}"]')).to_have_value(value)
        return body, query

    for route, form_name, endpoint, direction in [
        ("ledger", "fact-filter", "/transaction_fact/list", "cash_direction"),
        ("economy", "economic-filter", "/flow/list", "entry_direction"),
    ]:
        form = visit(route, form_name)
        for value in ["1", "2"]:
            body, query = change(form, direction, value, endpoint)
            assert all(row[direction] == int(value) for row in body["items"])
            assert json.loads(query["filter"][0]) == {"key": direction, "op": "=", "val": int(value)}
        body, _ = change(form, "currency_code", "CNY", endpoint)
        assert all(row["currency_code"] == "CNY" for row in body["items"])
        for value in ["amount.asc", "amount.desc", "occurred_time.asc", "occurred_time.desc"]:
            body, query = change(form, "sort", value, endpoint)
            field, order = value.split(".")
            assert json.loads(query["sorter"][0]) == [{"key": field, "direction": order}]
            values = [row[field] for row in body["items"]]
            assert values == sorted(values, reverse=order == "desc")
        # Choose both dates and the end minute through the restored calendar.
        form.locator('[data-date-time-range]').evaluate("control => control.dataset.month = '2026-09'")
        form.locator('[data-action="range-open"]').click()
        form.locator('[data-range-date="2026-09-17"]').click()
        form.locator('[data-range-date="2026-09-17"]').click()
        form.locator('[data-range-time="hour"]').select_option("10")
        form.locator('[data-range-time="minute"]').select_option("15")
        with page.expect_response(lambda response: endpoint + "?" in response.url) as pending:
            form.locator('[data-range-apply]').click()
        response = pending.value
        assert response.ok
        query = parse_qs(urlparse(response.url).query)
        expressions = json.loads(query["filter"][0])["expression"]
        end = next(item["val"] for item in expressions if item["op"] == "<")
        assert "T10:16:00" in end, end
        assert all("2026-09-17T00:00" <= row["occurred_time"] < "2026-09-17T10:16" for row in response.json()["body"]["items"])
        page.wait_for_load_state("networkidle")
        page.locator('[data-action="detail-clear"]').click()
        page.wait_for_load_state("networkidle")
        expect(form.locator(f'[name="{direction}"]')).to_have_value("")
        expect(form.locator('[name="currency_code"]')).to_have_value("")
        expect(form.locator('[name="date_from"]')).to_have_value("")

    form = visit("ledger-reviews", "detail-review-filter")
    for status in ["0", "1"]:
        body, _ = change(form, "status", status, "/review/list")
        assert all(row["status"] == int(status) for row in body["items"])
        for behavior in ["0", "1"]:
            body, query = change(form, "behavior_type", behavior, "/review/list")
            assert all(row["behavior_type"] == int(behavior) for row in body["items"])
            assert len(json.loads(query["filter"][0])["expression"]) == 2
    for value in ["updated_time.asc", "updated_time.desc", "created_time.asc", "created_time.desc"]:
        _, query = change(form, "sort", value, "/review/list")
        field, order = value.split(".")
        assert json.loads(query["sorter"][0]) == [{"key": field, "direction": order}]
    page.locator('[data-action="detail-clear"]').click()
    page.wait_for_load_state("networkidle")
    expect(form.locator('[name="behavior_type"]')).to_have_value("")

    form = visit("ledger-imports", "detail-import-filter")
    source = form.locator('[name="source_type"] option').nth(1).get_attribute("value")
    body, query = change(form, "source_type", source, "/import_file/list")
    assert all(str(row["source_type"]) == source for row in body["items"])
    change(form, "source_type", "", "/import_file/list")
    for value in ["created_time.asc", "created_time.desc", "updated_time.asc", "updated_time.desc", "id.asc", "id.desc"]:
        body, query = change(form, "sort", value, "/import_file/list")
        field, order = value.split(".")
        values = [row[field] for row in body["items"]]
        assert values == sorted(values, reverse=order == "desc")
    for value in ["0", "1", "2", "3"]:
        body, _ = change(form, "status", value, "/import_file/list")
        assert all(row["status"] == int(value) for row in body["items"])
    visit("ledger", "fact-filter")
    # Calendar stays inside short/narrow viewports and closes with Escape.
    for width, height in [(390, 844), (960, 540)]:
        page.set_viewport_size({"width": width, "height": height})
        page.locator('[data-action="range-open"]').click()
        popup = page.locator('[data-range-popover]')
        expect(popup).to_be_visible()
        page.wait_for_timeout(100)
        box = popup.bounding_box()
        assert box["x"] >= 0 and box["y"] >= 0
        assert box["x"] + box["width"] <= width and box["y"] + box["height"] <= height
        page.keyboard.press("Escape")
        expect(popup).to_be_hidden()
    page.set_viewport_size({"width": 1440, "height": 1000})
    print("All four list filter/sort regressions passed", flush=True)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="paam-inspection-") as temporary:
        os.environ["PAAM_DATA_DIR"] = temporary
        os.environ["PAAM_DATABASE_URL"] = f"sqlite:///{Path(temporary) / 'inspection.db'}"
        sys.path.insert(0, str(root / "src"))
        import uvicorn
        from backend.core import target_database

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config("backend.target_main:app", host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=base, timeout=120) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("Test server did not start")

                def get(path):
                    response = client.get(path)
                    response.raise_for_status()
                    return response.json()["body"]

                def post(path, data):
                    response = client.post(path, json=data)
                    response.raise_for_status()
                    return response.json()["body"]

                def import_files(files):
                    plan = post("/paam/import/v1/preview", {"files": files})
                    assert plan["can_confirm"], "Sample preview needs attention"
                    post(f"/paam/import/v1/preview/{plan['token']}/confirm", {"version": plan["version"]})

                if args.samples:
                    files = [{"filename": file.name, "content_base64": base64.b64encode(file.read_bytes()).decode()} for file in sorted(args.samples.iterdir()) if file.suffix.lower() in {".csv", ".xls", ".xlsx", ".pdf"}]
                    assert files, "No statement files found"
                    import_files(files)
                    print(f"Real samples imported: {len(files)} files, {get('/paam/ledger/v1/transaction_fact/list')['total']} facts", flush=True)

                statement = "\n".join([
                    "微信支付账单明细列表",
                    "交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注",
                    *[f"2026-09-17 10:{index:02d}:00,商户消费,界面验收商户{index},午餐与日常用品{'长摘要' * 24 if index == 26 else ''},支出,{12 + index / 100:.2f},零钱,支付成功,inspection-{index},mch-{index},验收" for index in range(1, 27)],
                ])
                import_files([{"filename": "界面验收账单.csv", "content_base64": base64.b64encode(statement.encode()).decode()}])
                files = get("/paam/import/v1/import_file/list?page_size=100")["items"]
                file_id = next(row["id"] for row in files if row["filename"] == "界面验收账单.csv")
                facts = get(f"/paam/import/v1/import_file/{file_id}/transaction_fact/list")["items"]
                fact = facts[0]
                review = post("/paam/ledger/v1/review", {
                    "behavior_type": 0, "title": "日常用品分配验收", "idempotency_key": "inspection-split",
                    "economics": [{"client_key": "first", "economic_type": "TRANSACTION"}, {"client_key": "second", "economic_type": "ACCOUNT_TRANSFER"}],
                    "allocations": [{"fact_id": fact["id"], "economic_key": "first", "amount": 500}, {"fact_id": fact["id"], "economic_key": "second", "amount": fact["amount"] - 500}],
                })
                review_id = review["id"]
                # Keep revoked history alongside fresh confirmed coverage.
                post(f"/paam/ledger/v1/review/{review_id}/revoke", {"idempotency_key": "inspection-revoke", "reason": "验收撤销历史", "actor": "ui-test"})
                detail = get(f"/paam/ledger/v1/transaction_fact/{fact['id']}")
                active = {row["id"] for row in detail["reviews"] if row["status"] == 0}
                ledger_id = next(row["ledger_entry_id"] for row in detail["allocations"] if row["review_case_id"] in active)
                summary = get(f"/paam/import/v1/import_file/{file_id}")["relation_summary"]
                assert summary["allocation_count"] == 26
                assert summary["totals"] == [{"currency_code": "CNY", "entry_direction": 2, "amount": sum(row["amount"] for row in facts)}]

                from datetime import datetime
                from backend.entity import TransactionFact
                from backend.service.target_economic_service import TargetEconomicService
                with target_database.SessionLocal() as db:
                    mixed_facts = [TransactionFact(
                        fact_key=f"inspection-{currency}", occurred_time=datetime(2026, 1, 1),
                        cash_direction=flow_direction, amount=value, currency_code=currency,
                        account_code="a" * 64, counterparty_name="", counterparty_account_ref="", summary="",
                        created_time=datetime(2026, 1, 1), updated_time=datetime(2026, 1, 1),
                    ) for currency, flow_direction, value in [("CNY_4", 1, 12345), ("USD", 2, 456)]]
                    db.add_all(mixed_facts)
                    db.commit()
                    mixed_ids = [row.id for row in mixed_facts]
                    TargetEconomicService(db).ensure_defaults(mixed_ids, commit=True)
                mixed_review = post("/paam/ledger/v1/review", {
                    "behavior_type": 0, "title": "多币种精度验收", "idempotency_key": "inspection-mixed",
                    "economics": [{"client_key": "cny", "economic_type": "TRANSACTION"}, {"client_key": "usd", "economic_type": "TRANSACTION"}],
                    "allocations": [{"fact_id": mixed_ids[0], "economic_key": "cny", "amount": 12345}, {"fact_id": mixed_ids[1], "economic_key": "usd", "amount": 456}],
                })

                errors = []
                cases = 0
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    verify_filters(page, base)
                    page.goto(base)
                    expect(page.locator('[data-action="fact-detail"]').first).to_be_visible()
                    displayed_dates = page.evaluate("""async () => {
                        const {date}=await import('/static/js/util/core.js');
                        return [date('2026-09-17T10:26:00'),date('2026-09-17T10:26:00Z')];
                    }""")
                    assert displayed_dates == ['2026-09-17 10:26', '2026-09-17 18:26']

                    def open_detail(kind, record_id):
                        page.evaluate("""async ({kind,id}) => {
                            const module = await import('/static/js/component/inspection.js');
                            await module.openInspection(kind,id,()=>{});
                        }""", {"kind": kind, "id": record_id})
                        expect(page.locator(".inspection-body .inspection-metrics").first).to_be_visible()
                        return page.locator("dialog.inspection-workspace[open]")

                    drawer = open_detail("review", mixed_review["id"])
                    expect(drawer.locator('.inspection-metrics').nth(1)).to_contain_text("CNY_4")
                    expect(drawer.locator('.inspection-metrics').nth(1)).to_contain_text("1.2345")
                    expect(drawer.locator('.inspection-metrics').nth(1)).to_contain_text("USD")
                    expect(drawer.locator('.inspection-metrics').nth(1)).to_contain_text("4.56")
                    drawer.locator('[data-close]').click()
                    drawer = open_detail("fact", mixed_ids[0])
                    expect(drawer.locator('#inspection-title')).to_have_text("未提供摘要")
                    expect(drawer.locator('.inspection-body')).to_contain_text("账户名称未识别")
                    drawer.locator('[data-close]').click()

                    # Failed loads remain recoverable without dismissing the workspace.
                    attempts = []
                    def transient(route):
                        attempts.append(1)
                        if len(attempts) == 1:
                            route.fulfill(status=503, content_type="application/json", body=json.dumps({"status": 503, "message": "暂时无法读取", "body": {}}))
                        else:
                            route.continue_()
                    pattern = f"**/paam/ledger/v1/transaction_fact/{fact['id']}"
                    page.route(pattern, transient)
                    page.evaluate("""async id => { const m=await import('/static/js/component/inspection.js'); await m.openInspection('fact',id,()=>{}); }""", fact["id"])
                    drawer = page.locator('dialog.inspection-workspace[open]')
                    expect(drawer.locator('[role="alert"]')).to_be_visible()
                    drawer.locator('[data-inspect-retry]').click()
                    expect(drawer.locator('.inspection-metrics').first).to_be_visible()
                    assert len(attempts) == 2
                    drawer.locator('[data-close]').click()
                    page.unroute(pattern, transient)

                    if args.samples:
                        real_cases = 0
                        for source_file in files:
                            if source_file["id"] == file_id:
                                continue
                            imported = get(f"/paam/import/v1/import_file/{source_file['id']}/transaction_fact/list")["items"]
                            drawer = open_detail("file", source_file["id"])
                            expect(drawer.locator(".inspection-body")).not_to_contain_text("undefined")
                            drawer.locator("[data-close]").click()
                            if imported:
                                source_fact = get(f"/paam/ledger/v1/transaction_fact/{imported[0]['id']}")
                                targets = [("fact", imported[0]["id"]), ("ledger", source_fact["ledgers"][0]["id"]), ("review", source_fact["reviews"][0]["id"])]
                                for related_kind, related_id in targets:
                                    drawer = open_detail(related_kind, related_id)
                                    expect(drawer.locator("#inspection-title")).not_to_be_empty()
                                    assert drawer.evaluate("d=>d.scrollWidth-d.clientWidth") <= 1
                                    drawer.locator("[data-close]").click()
                            real_cases += 1
                        print(f"Real statement inspection passed: {real_cases} file/fact/review/ledger chains", flush=True)

                    # Exercise the real list binding and navigation, not just the renderer.
                    page.locator('[data-action="fact-detail"]').first.click()
                    drawer = page.locator("dialog.inspection-workspace[open]")
                    expect(drawer.locator(".inspection-metrics").first).to_be_visible()
                    expect(drawer.locator(".inspection-rail")).to_be_visible()
                    title = drawer.locator("#inspection-title").inner_text()
                    drawer.locator("[data-inspect-next]").click()
                    expect(drawer.locator("#inspection-title")).not_to_have_text(title)
                    drawer.locator("[data-inspect-back]").click()
                    expect(drawer.locator("#inspection-title")).to_have_text(title)
                    drawer.locator("[data-inspect-full]").click()
                    expect(drawer.locator(".inspection-rail")).to_be_hidden()
                    drawer.locator("[data-inspect-full]").click()
                    drawer.locator("[data-close]").click()

                    # Keyboard open / Escape returns focus and leaves filtering intact.
                    row = page.locator('.detail-click-row').first
                    row.focus()
                    page.keyboard.press('Enter')
                    expect(page.locator('dialog.inspection-workspace[open]')).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(page.locator('dialog.inspection-workspace[open]')).to_have_count(0)
                    expect(row).to_be_focused()

                    drawer = open_detail("fact", fact["id"])
                    expect(drawer.locator(".inspection-metrics")).to_contain_text("¥0.00")
                    expect(drawer.locator('details[data-technical][open]')).to_have_count(0)
                    expect(drawer.locator('details[data-business]').filter(has=page.locator('summary', has_text="历史分配")).first).not_to_have_attribute("open", "")
                    drawer.locator("[data-expand]").click()
                    expect(drawer.locator('details[data-technical][open]')).to_have_count(0)
                    related = drawer.locator('.inspection-related[data-kind="review"]').first
                    related.locator(":scope > summary").click()
                    expect(related.locator(".inspection-inline-heading")).to_be_visible()
                    related.locator("[data-promote]").first.click()
                    expect(drawer.locator("[data-kind-label]")).to_have_text("审查记录")
                    drawer.locator("[data-inspect-back]").click()
                    expect(drawer.locator("[data-kind-label]")).to_have_text("事实流水")
                    drawer.locator("[data-close]").click()

                    drawer = open_detail("file", file_id)
                    relation_group = drawer.locator('.inspection-group').filter(has=page.locator(':scope > summary', has_text="关联事实"))
                    expect(relation_group.locator(".inspection-related")).to_have_count(20)
                    relation_group.locator("[data-rel-next]").click()
                    expect(relation_group.locator(".inspection-related")).to_have_count(6)
                    relation_group.locator("select").select_option("50")
                    expect(relation_group.locator(".inspection-related")).to_have_count(26)
                    drawer.locator("[data-close]").click()

                    drawer = open_detail("review", review_id)
                    historical = drawer.locator('.inspection-group').filter(has=page.locator(':scope > summary', has_text="历史分配"))
                    expect(historical).to_have_attribute("open", "")
                    expect(historical.locator('.inspection-allocation')).to_have_count(2)
                    drawer.locator('[data-close]').click()

                    sizes = [(1440, 1000, 1), (390, 844, 1), (1080, 1080, 1), (2700, 1080, 1)]
                    if args.matrix:
                        sizes = [(round(h * ratio / scale), round(h / scale), scale) for h in [1080, 2160, 3240] for ratio in [.4, 1, 2.5] for scale in [1, 1.5, 2, 3]]
                    for width, height, scale in sizes:
                        # OS scaling: physical pixels / scale is the logical viewport.
                        context.close()
                        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale, locale="zh-CN")
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base)
                        expect(page.locator('[data-action="fact-detail"]').first).to_be_visible()
                        if width >= 320:
                            for tab, action in [("ledger", "fact-detail"), ("economy", "economic-detail"), ("ledger-reviews", "economic-review-detail"), ("ledger-imports", "import-file-detail")]:
                                page.locator(f'.detail-tabs [data-page="{tab}"]').click()
                                expect(page.locator(f'[data-action="{action}"]').first).to_be_visible()
                                assert page.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth") <= 1, ("list", tab, width, height)
                        for kind, record_id in [("fact", fact["id"]), ("ledger", ledger_id), ("review", review_id), ("file", file_id)]:
                            if width >= 320:
                                tab = {"fact": "ledger", "ledger": "economy", "review": "ledger-reviews", "file": "ledger-imports"}[kind]
                                page.locator(f'.detail-tabs [data-page="{tab}"]').click()
                                expect(page.locator('.detail-primary').first).to_be_visible()
                            drawer = open_detail(kind, record_id)
                            geometry = drawer.evaluate("""d => ({
                                overflow: d.scrollWidth-d.clientWidth,
                                bodyOverflow: d.querySelector('.inspection-body').scrollWidth-d.querySelector('.inspection-body').clientWidth,
                                close: (()=>{const r=d.querySelector('[data-close]').getBoundingClientRect();return r.x>=0&&r.right<=innerWidth+1&&r.y>=0&&r.bottom<=innerHeight+1})()
                            })""")
                            assert geometry["overflow"] <= 1 and geometry["bodyOverflow"] <= 1 and geometry["close"], (kind, width, height, scale, geometry)
                            if args.screenshots and scale == 1:
                                args.screenshots.mkdir(parents=True, exist_ok=True)
                                page.screenshot(path=str(args.screenshots / f"inspection-{kind}-{width}x{height}.png"))
                            drawer.locator('[data-expand]').click()
                            assert drawer.evaluate("d=>d.scrollWidth-d.clientWidth") <= 1
                            drawer.locator("[data-close]").click()
                            cases += 1

                    # All supported palettes share the same structural layout.
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    for theme in ("focus", "jade", "blue", "paper"):
                        page.evaluate("value=>window.paamTheme.set(value)", theme)
                        drawer = open_detail("ledger", ledger_id)
                        expect(drawer.locator('[data-inspect-hero]')).to_be_visible()
                        assert drawer.evaluate("d=>d.scrollWidth-d.clientWidth") <= 1
                        drawer.locator('[data-close]').click()

                    # Browser zoom changes the layout viewport as well as raster density.
                    # Emulate 200% and 400% independently from the OS-scale matrix.
                    for zoom in (2, 4):
                        context.close()
                        context = browser.new_context(viewport={"width": round(1440 / zoom), "height": round(1000 / zoom)}, device_scale_factor=zoom)
                        page = context.new_page()
                        page.goto(base)
                        expect(page.locator('[data-action="fact-detail"]').first).to_be_visible()
                        for kind, record_id in [("fact", fact["id"]), ("ledger", ledger_id), ("review", review_id), ("file", file_id)]:
                            drawer = open_detail(kind, record_id)
                            assert drawer.evaluate("d=>d.scrollWidth-d.clientWidth") <= 1
                            expect(drawer.locator('[data-close]')).to_be_in_viewport()
                            drawer.locator('[data-close]').click()
                            cases += 1
                    assert not errors, errors
                    context.close()
                    browser.close()
                    print(f"PASS: {cases} detail/viewport cases; inline traversal, history, exact totals, pagination, full-screen and list navigation", flush=True)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
