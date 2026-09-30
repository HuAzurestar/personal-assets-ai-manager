"""Browser regression for themes, currency editing and shared inspection."""
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
from playwright.sync_api import expect, sync_playwright
import uvicorn

from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix="paam-presentation-") as temp:
        app = prepare_app(Path(temp))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + "/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#settings/automation")
                expect(page.locator("[data-auto-diagnostics]")).to_be_visible()
                expect(page.locator("body")).not_to_contain_text("PAAM_AUTOTAG")
                for width in [1440, 390]:
                    page.set_viewport_size({"width": width, "height": 900})
                    for theme in ["dark", "jade", "blue", "paper"]:
                        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                        tip = page.get_by_role("button", name="金额分档", exact=True)
                        tip.focus()
                        rect = tip.bounding_box()
                        assert abs(rect["width"] - rect["height"]) < 1, rect
                        popup = tip.locator("..").get_by_role("tooltip")
                        expect(popup).to_be_visible()
                        ratio = popup.evaluate("""el => {
                          const s = getComputedStyle(el);
                          const lum = value => {
                            const c = value.match(/[\\d.]+/g).slice(0,3).map(v => {
                              const x = Number(v)/255; return x <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4;
                            });
                            return .2126*c[0]+.7152*c[1]+.0722*c[2];
                          };
                          const a=lum(s.color), b=lum(s.backgroundColor);
                          return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                        }""")
                        assert ratio >= 4.5, (theme, ratio)
                        box = popup.bounding_box()
                        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
                page.set_viewport_size({"width": 1440, "height": 900})
                page.evaluate("document.documentElement.dataset.theme = 'dark'")
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator("[data-disclosure-form]")
                original = form.locator('[data-currency-row]').filter(has_text="CNY 区间")
                original.locator('[data-boundary]').nth(1).fill("35.00")
                form.locator('[data-new-currency]').select_option("USD")
                form.locator('[data-add-currency]').click()
                expect(form.locator('[data-currency-row]')).to_have_count(2)
                expect(original.locator('[data-boundary]').nth(1)).to_have_value("35.00")
                usd = form.locator('[data-currency-row]').filter(has_text="USD 区间")
                usd.locator('[data-add-boundary]').click()
                usd.locator('[data-boundary]').nth(1).fill("10.00")
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                page.reload()
                expect(page.locator('[data-auto-disclosure]')).to_contain_text("CNY")
                expect(page.locator('[data-auto-disclosure]')).to_contain_text("USD")
                data = httpx.get(base + "/paam/system/v1/setting/automation").json()["body"]
                assert data["disclosure"]["amount_bands"]["CNY"]["boundaries"][1] == 3500
                assert data["disclosure"]["amount_bands"]["USD"]["boundaries"] == [0, 1000]
                page.goto(base + "/#details/auto-rule")
                first = page.locator('[data-rule-row="1"] .detail-primary')
                first.click()
                drawer = page.locator('.inspection-workspace[open]')
                expect(drawer).to_have_attribute("data-layout", "wide")
                expect(drawer.locator('[data-auto-rule-detail]')).to_be_visible()
                assert drawer.bounding_box()["width"] < 1440
                drawer.locator('[data-inspect-next]').click()
                expect(drawer.locator('[data-rule-id="2"]')).to_be_visible()
                page.keyboard.press("Escape")
                expect(drawer).to_have_count(0)
                expect(first).to_be_focused()
                page.goto(base + "/#details/auto-rule?rule_id=1")
                expect(drawer.locator('[data-rule-id="1"]')).to_be_visible()
                page.set_viewport_size({"width": 900, "height": 900})
                expect(drawer).to_have_attribute("data-layout", "right")
                page.set_viewport_size({"width": 390, "height": 844})
                expect(drawer).to_have_attribute("data-layout", "full")
                evidence = os.environ.get("PAAM_UI_EVIDENCE_DIR")
                if evidence:
                    Path(evidence).mkdir(parents=True, exist_ok=True)
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.screenshot(path=str(Path(evidence) / "rule-drawer.png"))
                    page.goto(base + "/#settings/automation")
                    page.get_by_role("button", name="金额分档", exact=True).hover()
                    page.screenshot(path=str(Path(evidence) / "settings-help.png"))
                assert not errors, errors
                browser.close()
            print("PASS circular help, four-theme contrast, mobile bounds, CNY+USD persistence, visible diagnostics, shared rule drawer, deep links and focus return")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
