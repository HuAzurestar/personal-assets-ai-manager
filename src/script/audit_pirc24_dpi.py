"""Independent DPI/layout audit on a disposable, fictional PIRC-24 database."""

import json
import os
import subprocess
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright, expect
from serve_m2_ui import prepare_app

OUT = Path(
    os.environ.get(
        "PAAM_DPI_EVIDENCE_DIR",
        str(Path(__file__).resolve().parents[1] / "report" / "pirc24-ui-dpi-fixed"),
    )
)
MATRIX = [
    (1366, 768, 1),
    (1366, 768, 1.25),
    (1920, 1080, 1),
    (1920, 1080, 1.25),
    (1920, 1080, 1.5),
    (1920, 1080, 1.75),
    (1920, 1080, 2),
    (2560, 1440, 1.5),
    (3840, 2160, 2),
    (390, 844, 1),
    (768, 1024, 1),
]
AUDIT = r"""() => {
 const root=document.querySelector('dialog[open]') || document.documentElement;
 const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}};
 const visible=e=>e.checkVisibility({checkVisibilityCSS:true});
 const clips=[];
 for(const e of root.querySelectorAll('button,label,h2,h3,p,small,dt,dd,th,td,span')) {
   if(!visible(e))continue;
   const s=getComputedStyle(e);
   if((['hidden','clip'].includes(s.overflowX)&&e.scrollWidth>e.clientWidth+2)||
      (['hidden','clip'].includes(s.overflowY)&&e.scrollHeight>e.clientHeight+2))
     clips.push({tag:e.tagName,cls:e.className,text:e.textContent.trim().slice(0,100),box:box(e),sw:e.scrollWidth,sh:e.scrollHeight});
 }
 const alignment=[];
 for(const grid of root.querySelectorAll('.form-grid')) {
   const inputs=[...grid.querySelectorAll(':scope > label > input,:scope > label > select')].filter(visible);
   for(let i=1;i<inputs.length;i++) {
    const a=inputs[i-1],b=inputs[i],pa=a.parentElement.getBoundingClientRect(),pb=b.parentElement.getBoundingClientRect();
    if(Math.abs(pa.y-pb.y)<2&&Math.abs(a.getBoundingClientRect().y-b.getBoundingClientRect().y)>2)
      alignment.push({a:a.name,b:b.name,delta:b.getBoundingClientRect().y-a.getBoundingClientRect().y});
   }
 }
 const tables=[...root.querySelectorAll('table')].map(t=>{
   const heads=[...t.querySelectorAll('thead tr:first-child th')];
   const cells=[...t.querySelectorAll('tbody tr:first-child td')];
   return {columns:heads.length,aligned: heads.length===cells.length ? heads.every((h,i)=>Math.abs(h.getBoundingClientRect().x-cells[i].getBoundingClientRect().x)<1):null,
    parentWidth:t.parentElement.clientWidth,tableWidth:t.scrollWidth,scroll:getComputedStyle(t.parentElement).overflowX};
 });
 const nav=root.querySelector('.secondary-nav'),active=nav?.querySelector('[aria-pressed="true"]');
 const navigationVisible=!active || (active.getBoundingClientRect().left>=nav.getBoundingClientRect().left-1 && active.getBoundingClientRect().right<=nav.getBoundingClientRect().right+1);
 const wrappedButtons=[...root.querySelectorAll('.rule-operation button')].filter(visible).filter(e=>{
   const range=document.createRange();range.selectNodeContents(e);
   return new Set([...range.getClientRects()].map(r=>r.y)).size>1;
 }).map(e=>e.textContent);
 return {navigationVisible,wrappedButtons,viewport:{w:innerWidth,h:innerHeight,dpr:devicePixelRatio},pageOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
   dialog:root.tagName==='DIALOG'?{...box(root),overflow:root.scrollWidth-root.clientWidth}:null, clips,alignment,tables};
}"""


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    external = []
    with tempfile.TemporaryDirectory(prefix="pirc24-dpi-") as temp:
        app = prepare_app(Path(temp))
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
                version = browser.version
                for physical_w, physical_h, scale in MATRIX:
                    name = f"{physical_w}x{physical_h}-{int(scale * 100)}"
                    context = browser.new_context(
                        viewport={
                            "width": round(physical_w / scale),
                            "height": round(physical_h / scale),
                        },
                        device_scale_factor=scale,
                        locale="zh-CN",
                    )
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    page.on("pageerror", lambda e: errors.append(str(e)))

                    def route(req):
                        if req.request.url.startswith(base):
                            req.continue_()
                        else:
                            external.append(req.request.url)
                            req.abort()

                    page.route("**/*", route)

                    def capture(state):
                        record = {
                            "matrix": name,
                            "state": state,
                            **page.evaluate(AUDIT),
                        }
                        record["screenshot"] = f"{name}-{state}.png"
                        page.screenshot(
                            path=OUT / record["screenshot"], full_page=False
                        )
                        results.append(record)

                    def dialog(action, state):
                        page.locator(f'[data-action="{action}"]').first.click()
                        expect(page.locator("dialog[open]")).to_be_visible()
                        capture(state)
                        buttons = page.locator("dialog[open] button")
                        unreachable = []
                        for i in range(buttons.count()):
                            btn = buttons.nth(i)
                            if btn.is_visible() and btn.is_enabled():
                                try:
                                    btn.click(trial=True, timeout=2000)
                                except Exception:
                                    unreachable.append(btn.inner_text())
                        results[-1]["unreachableButtons"] = unreachable
                        page.locator("dialog[open] [data-close]").first.click()

                    try:
                        page.goto(base + "/#settings/automation")
                        expect(
                            page.locator('[data-action="model-edit"]')
                        ).to_be_visible()
                        capture("settings")
                        for action, state in [
                            ("model-edit", "model-edit"),
                            ("disclosure-edit", "disclosure-edit"),
                            ("disclosure-preview", "disclosure-preview"),
                        ]:
                            dialog(action, state)
                        page.goto(base + "/#details/auto-rule")
                        expect(
                            page.locator('[data-action="rule-edit"]').first
                        ).to_be_visible()
                        capture("rules")
                        dialog("rule-edit", "rule-edit")
                        dialog("rule-preview", "rule-preview")
                        page.goto(base + "/#details/auto-rule?rule_id=1")
                        expect(page.locator("[data-auto-rule-detail]")).to_be_visible()
                        capture("rule-detail")
                        page.goto(base + "/#workbench/tag-review?status=1")
                        expect(
                            page.locator("[data-tag-request-select]").first
                        ).to_be_visible()
                        capture("requests")
                        page.locator("[data-tag-request-select]").first.check()
                        dialog("tag-request-batch", "batch-confirm")
                        page.locator('a[href^="#workbench/tag-review?request_id="]').first.click()
                        expect(
                            page.locator("[data-auto-request-detail]")
                        ).to_be_visible()
                        capture("request-detail")
                        if scale == 1 and physical_w == 1366:
                            page.goto(base + "/#details/auto-rule")
                            expect(
                                page.locator('[data-action="rule-edit"]').first
                            ).to_be_visible()
                            for resize_width in (1920, 960, 390, 1366):
                                page.set_viewport_size(
                                    {"width": resize_width, "height": 768}
                                )
                                page.wait_for_function("""() => {
         const n=document.querySelector('.secondary-nav').getBoundingClientRect();
         const b=document.querySelector('.secondary-nav [aria-pressed="true"]').getBoundingClientRect();
         return b.left>=n.left-1 && b.right<=n.right+1;
        }""")
                    except Exception as e:
                        errors.append(f"{name}: {e}")
                        page.screenshot(path=OUT / f"{name}-failure.png")
                    finally:
                        context.close()
                    print(f"DONE {name}", flush=True)
                browser.close()
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                text=True,
            ).strip()
            (OUT / "result.json").write_text(
                json.dumps(
                    {
                        "base_sha": sha,
                        "browser": version,
                        "matrix": MATRIX,
                        "results": results,
                        "errors": errors,
                        "external": external,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                f"CAPTURES={len(results)} ERRORS={len(errors)} EXTERNAL={len(external)}",
                flush=True,
            )
            assert len(results) == len(MATRIX) * 11
            assert not errors and not external, (errors, external)
            failures = [
                r
                for r in results
                if r["pageOverflow"] > 1
                or r["alignment"]
                or r["wrappedButtons"]
                or not r["navigationVisible"]
                or r.get("unreachableButtons")
                or (r["dialog"] and r["dialog"]["overflow"] > 1)
            ]
            assert not failures, [(r["matrix"], r["state"]) for r in failures]
            print(
                "PASS alignment, active navigation including live resize, single-line rule actions, page/dialog overflow and dialog button reachability"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            from backend.core import target_database

            target_database.engine.dispose()


if __name__ == "__main__":
    run()
