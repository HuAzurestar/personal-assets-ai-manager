"""Browser/API regression for PR 9; disposable data and no provider calls."""
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
    with tempfile.TemporaryDirectory(prefix="paam-pr9-fix-") as temp:
        app = prepare_app(Path(temp))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        client = httpx.Client(base_url=base, trust_env=False)
        try:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("PR 9 browser fixture did not become healthy")

            def read(path):
                response = client.get(path)
                response.raise_for_status()
                return response.json()["body"]

            uri = "/paam/system/v1/setting/automation"
            rule_uri = "/paam/tag/v1/auto_rule"
            rules_url = rule_uri + "/list?page_index=1&page_size=100"
            requests_url = "/paam/tag/v1/assignment_request/list?page_index=1&page_size=100"
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors, writes = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda r: writes.append(r.url) if r.method in {"PUT", "DELETE"} else None)
                page.goto(base + "/#settings/automation")
                expect(page.locator('[data-auto-page="settings"]')).to_be_visible()
                expect(page.locator('[data-module="settings"]')).to_have_attribute("aria-pressed", "true")
                expect(page.locator('.topbar-actions [data-page="import"]')).to_have_text("导入账单")
                assert page.locator('[data-action="disclosure-preview"]').evaluate("el => parseFloat(getComputedStyle(el).borderTopWidth) > 0 && el.getBoundingClientRect().width < el.closest('section').clientWidth")
                before = read(rules_url)["items"]
                pending = read(requests_url)["items"]
                page.locator('[data-action="model-edit"]').click()
                form = page.locator('[data-form="automation-model"]')
                form.locator('[name="name"]').fill("Rename without resetting work")
                form.locator('button[type="submit"]').click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                assert read(rules_url)["items"] == before
                assert read(requests_url)["items"] == pending

                # Real parameter changes disclose all affected work before writing.
                page.locator('[data-action="model-edit"]').click()
                form.locator("summary").click()
                form.locator('[name="temperature"]').fill("0.25")
                count = len(writes)
                form.locator('button[type="submit"]').click()
                expect(form.locator('[data-model-impact]')).to_be_visible()
                expect(form.locator('[data-model-impact-copy]')).to_contain_text("3 条规则")
                expect(form.locator('[data-model-impact-copy]')).to_contain_text("3 条待审")
                assert len(writes) == count
                form.locator('[name="acknowledged"]').check()
                form.locator('[name="temperature"]').fill("0.5")
                expect(form.locator('[name="acknowledged"]')).not_to_be_checked()
                form.locator('button[type="submit"]').click()
                expect(form.locator('[data-model-impact]')).to_be_visible()
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(page.locator("dialog[open]")).to_have_count(0)
                assert {r["rule_revision"] for r in read(rules_url)["items"]} == {2}
                assert {r["status"] for r in read(requests_url)["items"]} == {4}

                # A stale open form must never adopt a later version token.
                page.locator('[data-action="model-edit"]').click()
                snapshot = read(uri)
                model = {k: v for k, v in snapshot["models"][0].items() if k != "key_configured"}
                model["name"] = "Concurrent saved name"
                response = client.put(uri, json={"expected_updated_time": snapshot["updated_time"], "models": [model]})
                response.raise_for_status()
                form.locator('[name="name"]').fill("Unsubmitted draft")
                form.locator('[name="secret"]').fill("fictional-replacement")
                count = len(writes)
                form.locator('button[type="submit"]').click()
                expect(form.locator(".form-error-slot")).not_to_be_empty()
                expect(form.locator('[name="name"]')).to_have_value("Unsubmitted draft")
                assert not any(url.endswith("/secret") for url in writes[count:])
                assert read(uri)["models"][0]["name"] == "Concurrent saved name"
                page.locator("dialog[open] [data-close]").first.click()
                page.reload()
                expect(page.locator('[data-auto-page="settings"]')).to_be_visible()

                def fail_setting(route):
                    route.fulfill(status=503, content_type="application/json", body='{"message":"temporary outage"}')

                page.route("**/setting/automation", fail_setting)
                page.locator("[data-timezone]").select_option("UTC")
                expect(page.locator("[data-refresh-error]")).to_be_visible()
                page.unroute("**/setting/automation", fail_setting)
                expect(page.locator("[data-refresh-error]")).to_have_count(0, timeout=15000)
                expect(page.locator("[data-auto-freshness]")).to_contain_text("已更新")
                page.goto(base + "/#workbench/tag-review?request_id=1")
                detail = page.locator("[data-auto-request-detail]")
                expect(detail).to_be_visible()
                ledger = read("/paam/ledger/v1/flow/1")
                expected = page.evaluate("""async value => (await import('/static/js/util/core.js')).date(value)""", ledger["ledger_entry"]["occurred_time"])
                expect(detail).to_contain_text(expected)
                old = detail.inner_text()
                page.locator("[data-timezone]").select_option("Asia/Hong_Kong")
                expect(detail).not_to_have_text(old)

                # Every rule and view remains available beyond the first API page.
                draft = before[0]
                payload = {key: draft[key] for key in ("name", "view_id", "method", "method_config", "enabled", "cron", "amount_mode")}
                payload["enabled"] = False
                for n in range(98):
                    response = client.post(rule_uri, json={**payload, "name": f"Pagination rule {n}"})
                    response.raise_for_status()
                response = client.post("/paam/tag/v1/view", json={"name": "Late view", "system_name": "late_view"})
                response.raise_for_status()
                late_id = response.json()["body"]["id"]
                # Views have a deliberate backend limit of 100; preserve it.
                view_count = read("/paam/tag/v1/view/list")["total"]
                for n in range(100 - view_count):
                    response = client.post("/paam/tag/v1/view", json={"name": f"Extra view {n}", "system_name": f"extra_view_{n}"})
                    response.raise_for_status()
                    late_id = response.json()["body"]["id"]
                page.goto(base + "/#details/auto-rule?page=6")
                expect(page.locator("[data-rule-row]")).to_have_count(1)
                expect(page.locator('[data-rule-row="101"]')).to_be_visible()
                page.locator('[data-action="rule-edit"]').click()
                expect(page.locator('[data-form="automation-rule"] [name="name"]')).to_have_value("Pagination rule 97")
                page.locator("dialog[open] [data-close]").first.click()
                search = page.locator('[data-form="auto-rule-filter"]')
                search.locator('[name="q"]').fill("Pagination rule 97")
                search.locator('button[type="submit"]').click()
                expect(page.locator("[data-rule-row]")).to_have_count(1)
                expect(page.locator("[data-auto-rule-pager]")).to_contain_text("共 1 条")
                page.locator('[data-action="rule-new"]').click()
                form = page.locator('[data-form="automation-rule"]')
                form.locator('[name="view_id"]').select_option(str(late_id))
                form.locator('[name="name"]').fill("Created beyond first page")
                form.locator('[name="prompt"]').fill("Classify fictional purchases")
                form.locator('button[type="submit"]').click()
                expect(page.locator('.inspection-workspace[open] [data-rule-id="102"]')).to_be_visible()
                def renamed_rule(route):
                    response = route.fetch()
                    body = response.json()
                    body["body"]["name"] = "Externally renamed rule"
                    route.fulfill(response=response, json=body)

                page.route("**/auto_rule/102", renamed_rule)
                expect(page.locator("#inspection-title")).to_have_text("Externally renamed rule", timeout=40000)
                page.unroute("**/auto_rule/102", renamed_rule)
                page.keyboard.press("Escape")
                expect(page.locator("[data-rule-row]").first).to_have_attribute("data-rule-row", "102")
                page.goto(base + "/#workbench/tag-review")
                expect(page.locator('[data-form="tag-request-filter"]')).to_be_visible()
                page.locator('[name="rule_id"]').select_option("102")
                expect(page.locator('[name="rule_id"]')).to_have_value("102")
                page.locator('[name="view_id"]').select_option(str(late_id))
                expect(page.locator('[name="view_id"]')).to_have_value(str(late_id))
                page.goto(base + "/#workbench/tag-review?status=")
                expect(page.locator('[data-tag-request-row]')).to_have_count(3)
                for width in (390, 700, 1440):
                    page.set_viewport_size({"width": width, "height": 900})
                    expect(page.locator('[data-tag-request-row]').first).to_be_visible()
                    assert page.locator('.request-table').evaluate("el => el.scrollWidth <= el.parentElement.clientWidth + 1")
                    if width <= 700:
                        assert page.locator('[data-tag-request-row]').first.evaluate("el => getComputedStyle(el).display") == "grid"
                    assert page.locator('[data-action="tag-request-batch"]').first.evaluate("el => getComputedStyle(el).whiteSpace") == "nowrap"
                # Models are a complete settings array, not a 100-row list API.
                snapshot = read(uri)
                original = [{k: v for k, v in item.items() if k != "key_configured"} for item in snapshot["models"]]
                extra = [{**original[0], "id": n, "name": f"Fictional model {n}", "enabled": False} for n in range(2, 102)]
                response = client.put(uri, json={"expected_updated_time": snapshot["updated_time"], "models": original + extra})
                response.raise_for_status()
                page.goto(base + "/#settings/automation")
                expect(page.locator('[data-model-card]')).to_have_count(101)
                page.locator('[data-model-card="101"] [data-action="model-edit"]').click()
                expect(page.locator('[data-form="automation-model"] [name="name"]')).to_have_value("Fictional model 101")
                assert not errors, errors
                browser.close()
            print("PASS model no-op, confirmed impact, stale form, reconnect, timezone, rule pagination/search, complete selectors and new-rule navigation; provider calls=0")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            client.close()
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
