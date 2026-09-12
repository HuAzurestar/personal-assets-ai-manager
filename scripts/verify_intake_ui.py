"""Exercise file selection, drop and clipboard against an isolated local ledger."""

import base64
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    with tempfile.TemporaryDirectory(prefix="paam-intake-ui-") as temp:
        os.environ["PAAM_DATA_DIR"] = temp
        os.environ["PAAM_DATABASE_URL"] = "sqlite:///" + str(Path(temp) / "ui.db")
        from app.main import app
        from app.database import engine

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + "/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(30000)
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/#summary")
                page.locator(".page-header [data-action=import]").click()
                real = Path("E:/Worktable/Download")
                assert real.exists(), (
                    "This local verification needs the user-provided sample folder"
                )
                files = [str(f) for f in real.iterdir()]
                page.locator("#intake-files").set_input_files(files)
                confirm = page.locator("[data-intake=confirm]")
                expect(confirm).to_be_enabled()
                assert page.locator("#intake-preview .import-file").count() == 7
                # 827 records remain in the plan, but only one page per file is rendered.
                assert page.locator("#intake-preview tbody tr").count() == 136
                assert page.locator("#intake-preview pre").count() == 0
                first_raw = page.locator("[data-raw-doc] summary").first
                first_raw.click()
                expect(page.locator("#intake-preview pre")).to_have_count(1)
                page.locator('[data-intake-page][data-step="1"]:enabled').first.click()
                assert page.locator("#intake-preview tbody tr").count() <= 175
                assert page.locator("#intake-preview pre").count() == 0
                assert page.locator(".steps").inner_text().splitlines() == [
                    "1 上传",
                    "2 预览",
                    "3 确认",
                ]
                confirm.click()
                expect(page.locator("#intake-preview .success")).to_contain_text(
                    "新增 803 笔"
                )
                page.locator("[data-intake=done]").click()
                expect(page).to_have_url(base + "/#data?scope=all")
                # Clipboard/drop use browser File objects; no second preview click.
                for event in ["drop", "paste"]:
                    page.locator(".page-header [data-action=import]").click()
                    data = base64.b64encode(Path(files[0]).read_bytes()).decode()
                    page.evaluate(
                        """({event,data,name})=>{const transfer=new DataTransfer();transfer.items.add(new File([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],name));const dialog=document.querySelector('#import-dialog');dialog.dispatchEvent(event==='drop'?new DragEvent('drop',{bubbles:true,dataTransfer:transfer}):new ClipboardEvent('paste',{bubbles:true,clipboardData:transfer}));}""",
                        {"event": event, "data": data, "name": Path(files[0]).name},
                    )
                    expect(page.locator("[data-intake=confirm]")).to_be_enabled()
                    expect(page.locator("#intake-preview")).to_contain_text("已导入")
                    page.locator("[data-intake=confirm]").click()
                    expect(page.locator("#intake-preview .success")).to_contain_text(
                        "新增 0 笔"
                    )
                    expect(page.locator("#intake-preview .success")).to_contain_text(
                        "全部记录已导入，本次未新增入账"
                    )
                    expect(page.locator("#intake-preview .success")).to_contain_text(
                        "跳过重复"
                    )
                    page.locator("[data-intake=done]").click()
                assert not errors, errors
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator(".page-header [data-action=import]").click()
                page.locator("#intake-files").set_input_files(files[-1])
                expect(page.locator("[data-intake=confirm]")).to_be_enabled()
                out = ROOT / "data" / "intake-verification"
                out.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out / "mobile-preview.png"))
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=window.innerWidth"
                )
                print(
                    "PASS: 7 files / 827 records / paged preview / lazy evidence / 803 facts; duplicate feedback; select, drop, clipboard; mobile; no browser errors",
                    flush=True,
                )
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    run()
