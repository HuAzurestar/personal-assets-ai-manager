"""Browser regression for serve_pirc24_usability_fixture.py ONLY; contains writes."""
import argparse
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import expect, sync_playwright


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:18824")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    base_url = urlparse(args.base)
    if not (
        base_url.scheme == "http"
        and base_url.hostname in {"127.0.0.1", "localhost"}
        and base_url.username is None
        and base_url.password is None
        and base_url.path in {"", "/"}
        and not base_url.query
        and not base_url.fragment
    ):
        raise ValueError("Only a local HTTP fixture URL without credentials or path is permitted")
    deadline = time.monotonic() + 30
    while True:
        try:
            response = httpx.get(args.base + "/__usability_fixture__", timeout=2)
            if response.status_code != 200:
                raise RuntimeError("Refusing writes: fixture marker missing")
            marker = response.json()
            break
        except httpx.HTTPError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    if marker != {"fixture": "pirc24-usability-disposable", "real_analysis": False}:
        raise RuntimeError("Refusing writes outside the disposable fixture")
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def visit(fragment, marker):
            page.goto(args.base + "/#" + fragment)
            expect(page.locator(marker)).to_be_visible()

        def capture(name):
            if args.screenshots:
                page.screenshot(path=str(args.screenshots / f"{name}.png"), full_page=True)

        def body(url):
            response = page.request.get(args.base + url)
            assert response.ok, response.text()
            return response.json()["body"]

        visit("details/auto-rule", "[data-auto-rule-rows]")
        expect(page.locator("[data-rule-row='1']")).to_contain_text("自动分析已关闭")
        expect(page.locator("[data-rule-row='3'] .automation-status")).to_have_text("已停用")
        capture("rules")
        before = body("/paam/tag/v1/auto_rule/1")
        page.locator("[data-action='rule-preview'][data-id='1']").click()
        expect(page.locator("dialog")).to_contain_text("最多 100 条")
        expect(page.locator("dialog")).to_contain_text("虚构测试餐厅")
        expect(page.locator("dialog")).not_to_contain_text("ELIGIBLE")
        capture("candidate-preview")
        page.locator("[data-preview-ledger]").first.click()
        expect(page.locator("dialog").last).to_contain_text("本地只读详情")
        expect(page.locator("dialog").last).to_contain_text("仅供隔离测试")
        page.locator("dialog").last.locator("[data-close]").first.click()
        page.locator("dialog [data-close]").first.click()
        assert before == body("/paam/tag/v1/auto_rule/1"), "Preview must not mutate the rule or cursor"

        page.locator("[data-action='rule-edit'][data-id='1']").click()
        expect(page.locator("[data-rule-pending-count]")).to_contain_text("3 条")
        # Template selection is a semantic edit even though it sets the textarea programmatically.
        form = page.locator("[data-form='automation-rule']")
        form.locator("[name='prompt_template']").select_option("purpose")
        expect(form.locator("[name='prompt']")).to_have_value(re.compile("仅判断消费用途"))
        expect(form.locator("[data-rule-edit-impact]")).to_contain_text("当前 3 条待确认建议将被取消")
        expect(form.locator("[data-rule-impact-ack]")).to_be_visible()
        form.locator("[name='acknowledged']").check()
        form.locator("[name='prompt_template']").select_option("channel")
        expect(form.locator("[name='prompt']")).to_have_value(re.compile("仅判断购买渠道"))
        expect(form.locator("[name='acknowledged']")).not_to_be_checked()
        form.locator("[name='prompt']").fill(before["method_config"]["prompt"])
        page.locator("dialog [name='name']").fill("按消费内容分类（已改名，虚构测试）")
        expect(page.locator("[data-rule-impact-ack]")).to_be_hidden()
        expect(page.locator("[data-rule-edit-impact]")).to_contain_text("不取消已有建议")
        page.locator("dialog button[type='submit']").click()
        expect(page.locator("dialog")).to_have_count(0)
        assert body("/paam/tag/v1/auto_rule/1")["rule_revision"] == before["rule_revision"]

        visit("workbench/tag-review?request_id=1", "[data-auto-request-detail]")
        expect(page.locator("[data-auto-request-detail]")).to_contain_text("其他待确认 1 条")
        expect(page.locator("[data-operation='approve']")).to_be_enabled()
        assert page.locator("[data-operation='approve']").bounding_box()["y"] < 1000
        capture("request-decision")
        page.locator("[data-action='tag-request-scope']").click()
        expect(page.locator("[data-tag-request-row]")).to_have_count(2)
        expect(page.locator("[data-action='tag-request-clear-scope']")).to_be_visible()
        page.locator("[data-action='tag-request-detail'][data-id='1']").click()
        assert "ledger_id=1" in page.url and "view_id=1" in page.url, "Detail links must preserve the comparison scope"
        page.locator("[data-operation='approve']").click()
        expect(page.locator("dialog")).to_contain_text("其他待确认建议会被取消")
        page.locator("[data-confirm-batch]").click()
        expect(page.locator("[data-auto-request-detail] .automation-status")).to_have_text("已通过")
        assert body("/paam/tag/v1/assignment_request/2")["status"] == 4

        visit("workbench/tag-review?request_id=3", "[data-auto-request-detail]")
        page.locator("[data-operation='reject']").click()
        expect(page.locator("dialog")).to_contain_text("不修改现有标签或其他建议")
        page.locator("[data-confirm-batch]").click()
        expect(page.locator("[data-auto-request-detail] .automation-status")).to_have_text("已拒绝")

        visit("details/auto-rule", "[data-auto-rule-rows]")
        page.locator("[data-action='rule-edit'][data-id='1']").click()
        expect(page.locator("[data-rule-pending-count]")).to_contain_text("1 条")
        page.locator("dialog [name='prompt']").fill("仅按合成消费内容判断；这是新的当前配置，不是旧建议的历史依据。")
        expect(page.locator("[data-rule-edit-impact]")).to_contain_text("当前 1 条待确认建议将被取消")
        page.locator("dialog button[type='submit']").click()
        expect(page.locator("dialog")).to_have_count(1)  # Required acknowledgment blocks accidental save.
        capture("edit-impact")
        page.locator("dialog [name='acknowledged']").check()
        page.locator("dialog button[type='submit']").click()
        expect(page.locator("dialog")).to_have_count(0)
        saved = body("/paam/tag/v1/auto_rule/1")
        assert saved["rule_revision"] == before["rule_revision"] + 1
        assert saved["scan_after_ledger_id"] == 0
        assert body("/paam/tag/v1/assignment_request/4")["status"] == 4
        assert body("/paam/tag/v1/assignment_request/1")["status"] == 2

        visit("workbench/tag-review?request_id=1", "[data-auto-request-detail]")
        expect(page.locator("[data-auto-request-detail]")).to_contain_text("仅供追溯")
        expect(page.locator("[data-operation='approve']")).to_have_count(0)
        source = page.locator("[data-auto-request-detail] section").filter(has=page.get_by_role("heading", name="来源规则", exact=True))
        expect(page.locator("[data-auto-request-detail]")).to_contain_text("非历史快照")
        page.wait_for_timeout(5500)
        expect(source).to_be_visible()
        capture("historical-request")

        # A failed impact read must not silently become zero or permit semantic save.
        visit("details/auto-rule", "[data-auto-rule-rows]")
        def fail_impact(route):
            route.fulfill(status=503, json={"message": "Synthetic impact read unavailable"})

        page.route("**/paam/tag/v1/assignment_request/list?**", fail_impact)
        page.locator("[data-action='rule-edit'][data-id='2']").click()
        expect(page.locator("[data-rule-pending-count]")).to_contain_text("读取失败")
        page.locator("dialog [name='prompt']").fill("合成修改，检查未知影响与并发保护")
        expect(page.locator("dialog button[type='submit']")).to_be_disabled()
        page.unroute("**/paam/tag/v1/assignment_request/list?**", fail_impact)
        page.locator("[data-rule-impact-retry]").click()
        expect(page.locator("[data-rule-pending-count]")).to_contain_text("0 条")
        page.locator("dialog [name='acknowledged']").check()

        # Concurrent edit: the form must retain its old token and preserve the draft on conflict.
        concurrent = body("/paam/tag/v1/auto_rule/2")
        payload = {key: concurrent[key] for key in ("name", "method", "method_config", "enabled", "cron", "amount_mode")}
        payload.update(name="另一个操作者的新名称（隔离测试）", expected_updated_time=concurrent["updated_time"])
        response = page.request.put(args.base + "/paam/tag/v1/auto_rule/2", data=payload)
        assert response.ok, response.text()
        page.locator("dialog button[type='submit']").click()
        expect(page.locator(".form-error-slot")).to_contain_text("不会自动覆盖其他修改")
        expect(page.locator("dialog [name='prompt']")).to_have_value("合成修改，检查未知影响与并发保护")
        assert body("/paam/tag/v1/auto_rule/2")["name"] == payload["name"]
        page.locator("dialog [data-close]").first.click()

        page.locator("[data-page='settings']").first.click()
        expect(page.locator("[data-auto-page='settings']")).to_be_visible()
        expect(page.locator("[data-auto-page='settings'] > section")).to_have_count(3)
        expect(page.locator("[data-auto-runtime]")).to_be_visible()
        expect(page.locator("[data-action='interaction-demo']")).to_have_count(0)
        capture("settings")
        page.wait_for_timeout(5500)
        expect(page.locator("[data-auto-runtime]")).to_be_visible()

        def mock_schedule(route):
            result = route.fetch().json()
            result["body"].update(tag_scan_guard="REAL_READY", scheduler_state="RUNNING", worker_state="HEALTHY", tasks=[
                {"task_key": "tag-scan:1", "queue_state": "RUNNING", "last_result": "FAILED"},
                {"task_key": "tag-scan:2", "queue_state": "QUEUED", "queue_position": 1},
            ])
            route.fulfill(json=result)

        page.route("**/paam/system/v1/schedule/status", mock_schedule)
        visit("details/auto-rule", "[data-auto-rule-rows]")
        expect(page.locator("[data-rule-row='1'] .automation-status")).to_have_text("分析中")
        expect(page.locator("[data-rule-row='2'] .automation-status")).to_have_text("排队中")
        expect(page.locator("[data-rule-row='1']")).to_contain_text("上次执行：失败")
        capture("running-queued")
        page.unroute("**/paam/system/v1/schedule/status", mock_schedule)
        for width in (1024, 768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            page.locator("[data-page='settings']").first.click()
            expect(page.locator("[data-auto-page='settings']")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Page overflow at {width}px"
            capture(f"settings-{width}")
        assert not errors, errors
        browser.close()
        print("PASS: preview is read-only; rename preserves revision; edit acknowledgment/cancellation; approve/reject and scope effects; historical provenance; polling preserves details; three settings sections; responsive widths; no JS errors")


if __name__ == "__main__":
    run()
