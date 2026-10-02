"""Actual current-ownership scope and card-first UI over a fresh fictional DB."""
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
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-account-scope-") as temporary:
        app = prepare_app(Path(temporary))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
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
                api = "/paam/ledger/v1"
                def post(path, body):
                    response = client.post(api + path, json=body)
                    response.raise_for_status()
                    return response.json()["body"]
                people = [post("/account-party", {"name": "Mock person " + name}) for name in ("A", "B", "empty")]
                groups = [post("/account", {"name": name, "party_id": people[index]["id"]}) for name, index in (
                    ("Mock A daily", 0), ("Mock A reserve", 0), ("Mock B daily", 1), ("Mock empty group", 2))]
                def card(name, account_id):
                    return post("/account-ref", {"name": name, "account_id": account_id,
                        "institution": "Mock bank", "reference": "990000001234"})
                card("Mock A card 00", groups[0]["id"])
                other = card("Mock B closed card", groups[2]["id"])
                closed = client.put(api + f"/account-ref/{other['id']}/metadata", json={
                    "name": other["name"], "account_id": other["account_id"], "institution": other["institution"],
                    "reference": other["reference"], "status": "CLOSED", "expected_updated_time": other["updated_time"]})
                assert closed.status_code == 200, closed.text
                card("Mock independent unassigned", 0)
                for number in range(1, 21):
                    card(f"Mock A card {number:02d}", groups[0]["id"])
                card("Mock A reserve card", groups[1]["id"])
                before = {name: client.get(api + path).json()["body"]["total"] for name, path in (
                    ("facts", "/transaction_fact/list"), ("flows", "/flow/list"), ("refs", "/account-ref/list"))}
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    errors, writes, reads = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda req: reads.append(req.url) if req.method == "GET" else writes.append(req.url))
                    page.goto(base + f"/#workbench/account?party={people[0]['id']}")
                    root = page.locator("[data-account-management]")
                    expect(root).to_be_visible()
                    # This assertion fails on the original UI even with the new API:
                    # a party-only scope used to include B and unassigned cards.
                    expect(root).not_to_contain_text("Mock B closed card")
                    expect(root).not_to_contain_text("Mock independent unassigned")
                    rows = root.locator("[data-account-list] tbody tr[data-ref-id]")
                    expect(rows).to_have_count(20)
                    expect(root.locator("[data-account-count]")).to_contain_text("共 22 项")
                    assert root.locator("table").count() == 1
                    assert rows.first.bounding_box()["height"] <= 100
                    sidebar = root.locator('.account-metadata-scope').bounding_box()
                    cards = root.locator('.account-card-surface').bounding_box()
                    assert sidebar['y'] == cards['y'] and sidebar['x'] + sidebar['width'] < cards['x']
                    viewport_evidence(page, "fix-r11-card-first")
                    root.locator("[data-account-next]").click()
                    expect(rows).to_have_count(2)
                    expect(root.locator("[data-account-count]")).to_contain_text("2 / 2")
                    root.locator("[data-account-prev]").click()
                    expect(rows).to_have_count(20)
                    form = root.locator('[data-account-filter]')
                    form.locator('[name="word"]').fill("Mock A card 20")
                    form.locator('[type="submit"]').click()
                    expect(root.locator('[data-account-scan-status]')).to_contain_text("本次扫描结束")
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text("Mock A card 20")
                    assert len([url for url in reads if "/account-ref/search?" in url]) == 2
                    root.locator('[data-account-clear-search]').click()
                    expect(rows).to_have_count(20)

                    def choose_scope(kind, word):
                        root.locator(f'[data-named-choice="{kind}"] [data-choice-pick]').click()
                        picker = page.locator("dialog[open] [data-choice-picker]")
                        picker.locator('[data-picker-word]').fill(word)
                        picker.locator('[data-picker-search]').click()
                        expect(picker.locator('[data-picker-scan-status]')).to_contain_text("本次扫描结束")
                        expect(picker.locator('[data-picker-id]')).to_have_count(1)
                        picker.locator('[data-picker-id]').click()

                    choose_scope("party_id", "Mock person B")
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text("Mock B closed card")
                    expect(rows).to_contain_text("CLOSED")
                    choose_scope("account_id", "Mock B daily")
                    expect(root).to_have_attribute("data-account", str(groups[2]["id"]))
                    expect(root).to_have_attribute("data-party", str(people[1]["id"]))
                    choose_scope("party_id", "Mock person empty")
                    expect(root).to_have_attribute("data-account", "0")
                    expect(rows).to_have_count(0)
                    expect(root).to_contain_text("当前范围没有来源卡")
                    choose_scope("account_id", "Mock empty group")
                    expect(rows).to_have_count(0)
                    root.locator('[data-account-unassigned]').click()
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text("Mock independent unassigned")
                    expect(root).to_have_attribute("data-party", "0")
                    expect(root).to_contain_text("未分组")
                    page.go_back()
                    expect(root).to_have_attribute("data-account", str(groups[3]["id"]))
                    expect(rows).to_have_count(0)

                    page.goto(base + f"/#workbench/account?account={groups[2]['id']}")
                    expect(root).to_have_attribute("data-party", str(people[1]["id"]))
                    expect(rows).to_have_count(1)
                    expect(root.locator('[data-named-choice="party_id"]')).to_contain_text("Mock person B")
                    prior = len([url for url in reads if "/account-ref/list?" in url])
                    page.goto(base + f"/#workbench/account?party={people[0]['id']}&account={groups[2]['id']}")
                    expect(page.locator("#page-content")).to_contain_text("不属于当前个人")
                    assert len([url for url in reads if "/account-ref/list?" in url]) == prior
                    page.goto(base + "/#workbench/account?party=9007199254740992")
                    expect(page.locator("#page-content .error")).to_be_visible()
                    assert len([url for url in reads if "/account-ref/list?" in url]) == prior

                    page.goto(base + "/#workbench/account")
                    expect(rows).to_have_count(20)
                    root.locator('[data-account-filter] [name="page_size"]').select_option("100")
                    expect(rows).to_have_count(24)
                    root.locator('[data-account-filter] [name="status"]').select_option("CLOSED")
                    expect(rows).to_have_count(1)
                    expect(rows).to_contain_text("Mock B closed card")
                    root.locator('[data-account-filter] [name="status"]').select_option("")
                    expect(rows).to_have_count(24)
                    # Directories are low-frequency dialogs, not extra page tables.
                    root.locator('[data-account-manage="party"]').click()
                    directory = page.locator('dialog[open] [data-account-directory]')
                    expect(directory.locator('.picker-list-row')).to_have_count(3)
                    expect(directory).to_contain_text("Mock person empty")
                    page.keyboard.press("Escape")
                    assert "990000001234" not in root.inner_text()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.reload()
                    expect(rows).to_have_count(24)
                    expect(root.locator('.account-metadata-scope details')).not_to_have_attribute('open', '')
                    assert rows.first.bounding_box()['y'] < 844
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    viewport_evidence(page, "fix-r11-card-mobile")
                    root.locator('.account-metadata-scope summary').click()
                    expect(root.locator('[data-account-unassigned]')).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert not errors and not writes, (errors, writes)
                    browser.close()
                after = {name: client.get(api + path).json()["body"]["total"] for name, path in (
                    ("facts", "/transaction_fact/list"), ("flows", "/flow/list"), ("refs", "/account-ref/list"))}
                assert before == after
                print("PIRC-35 account scope/card-first browser passed; zero UI writes, unchanged business counts")
            finally:
                server.should_exit = True
                worker.join(timeout=10)
                from backend.core import target_database
                target_database.engine.dispose()


if __name__ == "__main__":
    run()
