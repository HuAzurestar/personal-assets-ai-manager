"""Real-browser account workflow, using only an isolated fictional database."""
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
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-account-") as temporary:
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
                def post(path, payload):
                    response = client.post("/paam/ledger/v1" + path, json=payload)
                    response.raise_for_status()
                    return response.json()["body"]
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + "/#workbench/account")
                    expect(page.locator("[data-account-management]")).to_be_visible()
                    page.locator('[data-account-create="party"]').click()
                    form = page.locator("dialog[open] form")
                    form.locator('[name="name"]').fill("Mock person A")
                    form.locator('[type="submit"]').click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    page.locator('[data-account-create="account"]').click()
                    # A collection can be created before any card exists; its
                    # owner is chosen by name, not supplied as an editable ID.
                    form.locator('[data-named-choice="party_id"] [data-choice-pick]').click()
                    picker = page.locator('dialog[open] [data-choice-picker]')
                    expect(picker.locator('[data-picker-id]')).to_have_count(1)
                    picker.locator('[data-picker-id]').click()
                    expect(form.locator('[name="party_id"]')).to_have_attribute('type', 'hidden')
                    expect(form.locator('[data-choice-label]')).to_contain_text('Mock person A')
                    form.locator('[name="name"]').fill("Mock set A")
                    form.locator('[type="submit"]').click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    # Low-frequency maintenance must work for a collection
                    # before it has any card, not depend on the main list.
                    page.locator('[data-account-manage="account"]').click()
                    page.locator('dialog[open] [data-directory-action]').select_option('edit')
                    directory = page.locator('dialog[open] [data-account-directory]')
                    expect(directory.locator('[data-picker-id]')).to_have_count(1)
                    directory.locator('[data-picker-id]').click()
                    expect(form.locator('[name="name"]')).to_have_value('Mock set A')
                    form.locator('[name="name"]').fill('Mock set A maintained')
                    form.locator('[type="submit"]').click()
                    expect(page.locator('dialog[open]')).to_have_count(0)
                    groups = client.get('/paam/ledger/v1/account/list').json()['body']
                    assert groups['total'] == 1 and groups['items'][0]['name'] == 'Mock set A maintained'
                    page.locator('[data-account-create="ref"]').click()
                    form.locator('[data-named-choice="account_id"] [data-choice-pick]').click()
                    picker = page.locator('dialog[open] [data-choice-picker]')
                    expect(picker.locator('[data-picker-id]')).to_have_count(1)
                    picker.locator('[data-picker-id]').click()
                    expect(form.locator('[data-choice-label]')).to_contain_text('Mock set A maintained')
                    form.locator('[name="name"]').fill("Mock card")
                    form.locator('[name="reference"]').fill("990000000000001234")
                    form.locator('[type="submit"]').click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    expect(page.locator("[data-account-management]")).to_contain_text("****1234")
                    assert "990000000000001234" not in page.locator("[data-account-management]").inner_text()
                    person = post("/account-party", dict(name="Mock person B"))
                    target = post("/account", dict(name="Mock set B", party_id=person["id"]))
                    page.locator("[data-account-move]").click()
                    expect(form.locator('[name="account_id"]')).to_have_attribute('type', 'hidden')
                    form.locator('[data-choice-pick]').click()
                    picker = page.locator('dialog[open] [data-account-picker]')
                    picker.locator('[data-picker-word]').fill('Mock set B')
                    picker.locator('[data-picker-search]').click()
                    expect(picker.locator('[data-picker-scan-status]')).to_contain_text('本次扫描结束')
                    picker.locator('[data-picker-id]').click()
                    expect(form.locator('[data-choice-label]')).to_contain_text('Mock person B')
                    expect(form.locator('[data-choice-label]')).to_contain_text('Mock set B')
                    expect(form.locator('[name="account_id"]')).to_have_value(str(target['id']))
                    form.locator('[type="submit"]').click()
                    expect(form.locator("[data-impact]")).to_contain_text("跨个人变更")
                    expect(form.locator('[data-impact]')).to_contain_text('Mock person A / Mock set A maintained → Mock person B / Mock set B')
                    form.locator("[data-confirm]").click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    page.goto(base + "/#workbench/account")
                    page.locator('[data-account-edit="ref"]').click()
                    form.locator('[name="status"]').select_option("CLOSED")
                    form.locator('[type="submit"]').click()
                    expect(page.locator("dialog[open]")).to_have_count(0)
                    expect(page.locator("[data-account-management]")).to_contain_text("CLOSED")
                    # A response lost after server-side creation cannot enable another POST.
                    def lose_response(route):
                        response = route.fetch()
                        assert response.ok
                        route.fulfill(status=503, content_type="application/json",
                            body='{"status":503,"message":"synthetic response loss","body":{"code":"RESULT_UNKNOWN"}}')
                    page.route("**/paam/ledger/v1/account-party", lose_response)
                    page.locator('[data-account-create="party"]').click()
                    form.locator('[name="name"]').fill("Mock result unknown")
                    form.locator('[type="submit"]').click()
                    expect(form.locator("[role=status]")).to_contain_text("提交结果未知")
                    expect(form.locator('[type="submit"]')).to_be_disabled()
                    page.keyboard.press("Escape")
                    page.unroute("**/paam/ledger/v1/account-party", lose_response)
                    page.reload()
                    expect(page.locator("[data-account-management]")).to_be_visible()
                    page.locator('[data-account-manage="party"]').click()
                    directory = page.locator('dialog[open] [data-account-directory]')
                    expect(directory).to_contain_text("Mock result unknown")
                    # The persisted creation is discoverable, without another POST.
                    people = client.get('/paam/ledger/v1/account-party/list').json()['body']
                    assert len([row for row in people['items'] if row['name'] == 'Mock result unknown']) == 1
                    page.keyboard.press('Escape')
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert errors == [], errors
                    browser.close()
                print("PIRC-35 fictional account browser workflow passed")
            finally:
                server.should_exit = True
                worker.join(timeout=10)
                from backend.core import target_database
                target_database.engine.dispose()


if __name__ == "__main__":
    run()
