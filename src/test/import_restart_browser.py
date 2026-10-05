"""Actual process restart after committed/lost HTTP response; fictional only."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import expect, sync_playwright

from browser_artifact import viewport_evidence
from import_browser_action import open_import_advanced


FINANCIAL_TABLES = ("transaction_fact", "review_case", "ledger_entry",
                    "review_transaction_ledger_allocation", "transaction_import_row")


def fixture_python():
    # Windows venv python.exe is a redirector: its PID is not the serving
    # Python PID, and terminating it leaves the child holding the log/port.
    # Keep the inherited environment (including the isolated PYTHONPATH),
    # but launch the actual interpreter so the exact Popen handle owns it.
    return sys._base_executable if os.name == "nt" else sys.executable


def snapshot(database, directory):
    """Read all twenty fixture tables in one actual SQLite read transaction."""
    database = database.resolve()
    assert database.is_relative_to(directory.resolve()), database
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        assert len(names) == 20, names
        result = {}
        for name in names:
            assert name.replace("_", "").isalnum(), name
            columns = [row[1] for row in db.execute(f'PRAGMA table_info("{name}")')]
            fields = ",".join('"' + field + '"' for field in columns)
            result[name] = [dict(zip(columns, row)) for row in db.execute(
                f'SELECT {fields} FROM "{name}" ORDER BY id')]
        return result


def run_import_restart():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-real-restart-") as temporary:
        directory = Path(temporary).resolve()
        database = directory / "m2-ui.db"
        source = Path(__file__).with_name("serve_m2_ui.py").resolve()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = {key: value for key, value in os.environ.items() if not key.startswith("PAAM_")}
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        processes, logs = [], []

        def start():
            log = (directory / f"fixture-process-{len(processes)}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen([fixture_python(), str(source), "--data-dir", str(directory),
                "--host", "127.0.0.1", "--port", str(port)], cwd=source.parents[2], env=env,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(process)
            with httpx.Client(base_url=base, trust_env=False, timeout=1) as client:
                for _ in range(150):
                    assert process.poll() is None, (directory / log.name).read_text(encoding="utf-8")
                    try:
                        if client.get("/api/health").status_code == 200:
                            return process
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
            raise AssertionError("The owned fictional process did not become healthy")

        def stop(process):
            # Only this exact Popen handle; never discover/terminate other apps.
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            assert process.poll() is not None

        try:
            first = start()
            before = snapshot(database, directory)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    errors, approvals, writes, committed = [], [], [], []
                    page.on("pageerror", lambda error: errors.append(error.stack or str(error)))

                    def track(request):
                        if request.method == "POST" and request.url.endswith("/operation-approve"):
                            approvals.append((request.url, request.post_data_json))
                        elif request.method == "POST" and request.url.endswith(("/operation-confirm", "/confirm")):
                            writes.append((request.url, request.post_data_json))

                    page.on("request", track)
                    header = "建设银行个人交易明细\n账号：990000000000008888\n姓名：Mock重启用户\n币种：人民币\n摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n"
                    content = (header + "".join(f"MockRestart{i},CNY,2024-02-01,-{i}.00,10000.00,Mock重启用途{i},Mock商户\n"
                        for i in range(1, 1002))).encode()

                    def upload():
                        page.goto(base + "/#workbench/import")
                        page.locator('[data-action="import-step"][data-step="2"]').last.click()
                        form = page.locator('[data-form="import-preview"]')
                        form.locator('[name="files"]').set_input_files({"name": "MockRestart1001.csv",
                            "mimeType": "text/csv", "buffer": content})
                        form.locator('[data-action="preview-import"]').click()
                        expect(page.locator('[data-batch-row]')).to_have_count(20, timeout=35000)

                    upload()
                    for width in (1280, 820, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        page.locator('[data-batch-row]').last.scroll_into_view_if_needed()
                        toolbar = page.locator('[data-batch-toolbar]').bounding_box()
                        topbar = page.locator('.module-topbar').bounding_box()
                        viewport_evidence(page, f"fix-import-restart-toolbar-{width}")
                        assert toolbar and 0 <= toolbar["y"] <= 2 and toolbar["height"] < 300, toolbar
                        assert topbar and topbar["y"] + topbar["height"] <= 2, (width, topbar, toolbar)
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text("1001 行", timeout=35000)
                    expect(page.locator('[data-batch-select-scope]')).to_be_enabled(timeout=35000)
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=35000)
                    open_import_advanced(page)
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(2, timeout=35000)
                    page.locator('[data-batch-consent]').check()
                    pattern = "**/paam/import/v1/preview/*/operation-confirm"

                    def lose_committed_response(route):
                        response = route.fetch(timeout=35000)
                        assert response.status == 200, response.text()
                        body = response.json()["body"]
                        assert body["new_fact_count"] == 1000 and body["remaining_count"] == 1
                        committed.append(body)
                        route.abort("failed")

                    page.route(pattern, lose_committed_response)
                    page.locator('[data-batch-execute]').click()
                    expect(page.locator('[data-batch-status]')).to_contain_text("提交结果尚待核对", timeout=35000)
                    page.unroute(pattern, lose_committed_response)
                    assert len(approvals) == len(writes) == len(committed) == 1
                    pending = page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1'))")
                    retained = page.evaluate("JSON.parse(localStorage.getItem('paam.import.remaining.v1'))")
                    assert len(pending["rows"]) == 1000 and len(retained["choices"]) == 1001
                    frozen = snapshot(database, directory)
                    assert [len(frozen[name]) - len(before[name]) for name in FINANCIAL_TABLES] == [1000] * 5
                    page.locator('[data-batch-execute]').evaluate("node=>node.onclick()")
                    assert len(writes) == 1
                    viewport_evidence(page, "fix-import-real-restart-before")

                    stop(first)
                    second = start()
                    assert first.pid != second.pid and first.poll() is not None and second.poll() is None
                    with httpx.Client(base_url=base, trust_env=False) as client:
                        expired = client.get(f'/paam/import/v1/preview/{pending["token"]}')
                    assert expired.status_code == 410, expired.text
                    assert snapshot(database, directory) == frozen, "restart/410 must not mutate financial or source state"
                    # Reload loses all JS/module state but keeps this origin's
                    # safe locator/draft. Re-upload the exact same bytes, not a
                    # new financial request or a copied receipt.
                    page.reload()
                    upload()
                    expect(page.locator('[data-batch-selection]')).to_contain_text("1001 行", timeout=35000)
                    expect(page.locator('[data-batch-selection]')).to_contain_text("保留范围须重读")
                    expect(page.locator('[data-batch-execute]')).to_be_disabled()
                    expect(page.locator('[data-batch-save]')).to_be_disabled()
                    page.locator('[data-batch-execute]').evaluate("node=>node.onclick()")
                    assert len(writes) == len(approvals) == 1
                    after_upload = snapshot(database, directory)
                    assert len(after_upload["transaction_import_file"]) == len(frozen["transaction_import_file"])
                    assert all(after_upload[name] == frozen[name] for name in FINANCIAL_TABLES)

                    page.locator('[data-batch-verify]').click()
                    expect(page.locator('[data-batch-observed]')).to_be_enabled(timeout=35000)
                    expect(page.locator('[data-batch-verification]')).to_contain_text("共 1000 行")
                    assert len(writes) == len(approvals) == 1
                    assert snapshot(database, directory) == after_upload, "reconciliation is wholly read-only"
                    page.locator('[data-batch-observed]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text("本次明确选择 1 行", timeout=35000)
                    expect(page.locator('[data-batch-restore]')).to_be_enabled(timeout=35000)
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.remaining.v1')).choices.length") == 1
                    page.locator('[data-batch-restore]').click()
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=35000)
                    viewport_evidence(page, "fix-import-real-restart-reconciled-remaining-one")
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=35000)
                    open_import_advanced(page)
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(1, timeout=35000)
                    expect(page.locator('[data-batch-execute]')).to_be_disabled()
                    page.locator('[data-batch-execute]').evaluate("node=>node.onclick()")
                    assert len(writes) == len(approvals) == 1
                    page.locator('[data-batch-consent]').check()
                    page.locator('[data-batch-execute]').click()
                    expect(page.locator('[data-batch-execution]')).to_contain_text("全部完成", timeout=35000)
                    expect(page.locator('[data-batch-selection]')).to_contain_text("0 行", timeout=35000)
                    expect(page.locator('[data-batch-refresh]')).to_be_enabled(timeout=35000)
                    assert [len(body["selected_rows"]) for _, body in writes] == [1000, 1]
                    assert [len(body["selected_rows"]) for _, body in approvals] == [1001, 1]
                    assert writes[0][0] != writes[1][0], "old financial POST must never replay after restart"
                    finished = snapshot(database, directory)
                    assert [len(finished[name]) - len(before[name]) for name in FINANCIAL_TABLES] == [1001] * 5
                    for name in FINANCIAL_TABLES:
                        assert finished[name][:len(frozen[name])] == frozen[name], name
                    assert len({row["transaction_fact_id"] for row in finished["transaction_import_row"]}) == 1001
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert page.evaluate("localStorage.getItem('paam.import.remaining.v1')") is None
                    assert not errors, errors
                    viewport_evidence(page, "fix-import-real-restart-complete")
                    evidence = dict(first_pid=first.pid, second_pid=second.pid, actual_restart=True,
                        old_preview_status=expired.status_code, committed_before_restart=1000,
                        pending_scope=1000, retained_draft=1001, readonly_reconciliation=True,
                        approved_scopes=[1001, 1], committed_scopes=[1000, 1],
                        exact_original_rows_preserved=True, no_financial_post_replay=True, page_errors=errors)
                    destination = os.getenv("PIRC35_BROWSER_EVIDENCE_DIR")
                    if destination:
                        Path(destination).mkdir(parents=True, exist_ok=True)
                        (Path(destination) / "fix-import-real-restart.json").write_text(
                            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                    print("PASS actual isolated OS process restart, actual410, same bytes and safe draft, "
                          "readonly1000 reconciliation, fresh explicit remaining1 approval, immutable originals, no replay")
                    page.goto(base + "/#details/transaction-fact")
                    expect(page.locator('.module-topbar')).to_have_css("position", "sticky")
                    assert len(writes) == len(approvals) == 2 and not errors, errors
                finally:
                    browser.close()
        finally:
            for process in processes:
                stop(process)
            for log in logs:
                log.close()


if __name__ == "__main__":
    run_import_restart()
