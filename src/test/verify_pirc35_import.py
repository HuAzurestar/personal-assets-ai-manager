"""Actual browser, fictional statements, selected scope and lost response."""
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
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-import-") as temporary:
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
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    errors, confirmations = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: confirmations.append(request.post_data_json) if request.method == "POST" and request.url.endswith("/confirm") else None)
                    page.goto(base + "/#workbench/import")
                    expect(page.locator('[data-form="import-preview"]')).to_be_visible()
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    fixtures = Path(__file__).parent / "fixtures" / "pirc35"
                    upload.locator('[name="files"]').set_input_files(fixtures / "ccb-2.csv")
                    upload.locator('[data-action="preview-import"]').click()
                    rows = page.locator("[data-batch-row]")
                    expect(rows).to_have_count(20, timeout=15000)
                    expect(page.locator("[data-batch-confirm]")).to_be_disabled()
                    page.locator("[data-batch-select-page]").click()
                    expect(page.locator("[data-batch-selection]")).to_contain_text("20 行")
                    page.locator("[data-batch-save]").click()
                    expect(page.locator("[data-batch-confirm]")).to_be_enabled()
                    # An intent edit invalidates approval until a successful PUT.
                    rows.first.locator("[data-row-decision]").select_option("SKIP")
                    expect(page.locator("[data-batch-confirm]")).to_be_disabled()
                    rows.first.locator("[data-row-decision]").select_option("ACCEPT")
                    page.locator("[data-batch-save]").click()
                    expect(page.locator("[data-batch-confirm]")).to_be_enabled()
                    page.locator("[data-batch-confirm]").click()
                    expect(page.locator("[data-batch-files]")).to_contain_text("已接受 20", timeout=15000)
                    expect(page.locator("[data-batch-files]")).to_contain_text("剩余 4")
                    expect(rows.first.locator("[data-row-select]")).to_be_disabled()
                    assert len(confirmations) == 1 and len(confirmations[0]["selected_rows"]) == 20
                    assert "version" not in confirmations[0]
                    page.locator("[data-batch-next]").click()
                    expect(rows).to_have_count(4)
                    page.locator("[data-batch-select-page]").click()
                    page.locator("[data-batch-save]").click()
                    expect(page.locator("[data-batch-confirm]")).to_be_enabled()
                    # Server commits; browser receives an unknown-result error.
                    def dropped_reply(route):
                        response = route.fetch()
                        assert response.status == 200
                        route.fulfill(status=503, content_type="application/json",
                            body='{"status":503,"message":"fictional dropped reply","body":{"code":"RESULT_UNKNOWN","details":{}}}')
                    page.route("**/paam/import/v1/preview/*/confirm", dropped_reply)
                    page.locator("[data-batch-confirm]").click()
                    expect(page.locator("[data-batch-status]")).to_contain_text("结果未知", timeout=15000)
                    expect(page.locator("[data-batch-confirm]")).to_be_disabled()
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1')).rows.length") == 4
                    page.locator("[data-batch-verify]").click()
                    expect(page.locator("[data-batch-verification]")).to_contain_text("已接受", timeout=15000)
                    expect(page.locator("[data-batch-verification]")).to_contain_text("CONFIRMED")
                    assert len(confirmations) == 2
                    expect(page.locator("[data-batch-observed]")).to_be_enabled()
                    page.locator("[data-batch-observed]").click()
                    expect(page.locator("[data-batch-files]")).to_contain_text("已接受 24")
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert len(confirmations) == 2
                    page.unroute("**/paam/import/v1/preview/*/confirm", dropped_reply)
                    files = client.get("/paam/import/v1/import_file/list").json()["body"]["items"]
                    assert len(files) == 1 and files[0]["success_count"] == 24
                    # Persisted list is light; private raw evidence is fetched on demand.
                    page.goto(base + "/#details/import-file")
                    expect(page.locator('[data-action="import-file-detail"]')).to_have_count(1)
                    page.locator('[data-action="import-file-detail"]').click()
                    expect(page.locator(".inspection-workspace[open]")).to_be_visible()
                    page.locator("[data-source-evidence]").first.click()
                    expect(page.locator("dialog[open] .dialog-body").last).to_contain_text("当前可核验关系")
                    expect(page.locator("dialog[open] details summary").last).to_contain_text("JSON")
                    page.locator("dialog[open] [data-workbench-close]").click()
                    page.locator(".inspection-workspace[open] [data-close]").click()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.goto(base + "/#workbench/import")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert errors == [], errors
                    browser.close()
                print("PIRC-35 fictional selected import, raw detail and lost-response browser workflow passed")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
