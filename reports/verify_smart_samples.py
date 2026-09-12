"""Verify private local samples without copying or printing transaction rows."""

from __future__ import annotations

import base64
import collections
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.statement_parser import parse_statement


SAMPLE_SUFFIXES = {".csv", ".xls", ".xlsx", ".pdf", ".zip"}
SAMPLE_DIR = Path("E:/Worktable/Download")


def samples() -> list[Path]:
    if not SAMPLE_DIR.is_dir():
        raise SystemExit(f"sample directory does not exist: {SAMPLE_DIR}")
    return sorted(
        file
        for file in SAMPLE_DIR.iterdir()
        if file.is_file() and file.suffix.lower() in SAMPLE_SUFFIXES
    )


def verify_parsers(sample_files: list[Path]) -> None:
    for file in sample_files:
        try:
            parsed = parse_statement(file.read_bytes(), file.name)
            print(json.dumps({
                "file": file.name,
                "source": parsed["source_type"],
                "rows": len(parsed["rows"]),
                "errors": dict(collections.Counter(
                    row["error"] for row in parsed["rows"] if row["error"]
                )),
                "dispositions": dict(collections.Counter(
                    row["disposition"] for row in parsed["rows"]
                )),
                "precision": dict(collections.Counter(
                    row.get("time_precision") for row in parsed["rows"]
                )),
                "references": sum(
                    bool(row.get("reference")) for row in parsed["rows"]
                ),
            }, ensure_ascii=False), flush=True)
        except Exception as error:
            print(file.name, type(error).__name__, str(error), flush=True)
            raise


def upload_payload(sample_files: list[Path]) -> list[dict[str, str]]:
    return [{
        "filename": file.name,
        "content_base64": base64.b64encode(file.read_bytes()).decode(),
    } for file in sample_files]


def preview(client, files):
    response = client.post("/paam/import/v1/preview", json={"files": files})
    assert response.status_code == 200, response.text
    return response.json()["body"]


def confirm(client, plan):
    return client.post(
        f"/paam/import/v1/preview/confirm/{plan['token']}",
        json={"version": plan["version"]},
    )


@contextmanager
def measured_selects(engine):
    from sqlalchemy import event

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def verify_target_commit(sample_files: list[Path]) -> None:
    with tempfile.TemporaryDirectory(prefix="paam-target-samples-") as temp:
        database_path = Path(temp) / "sample.db"
        os.environ["PAAM_DATA_DIR"] = temp
        os.environ["PAAM_DATABASE_URL"] = f"sqlite:///{database_path}"

        from fastapi.testclient import TestClient
        from sqlalchemy import inspect, select
        from app import target_database
        from app.models.target import BillFact, BillRaw, ImportFile, LedgerEntry
        from app.target_main import app

        files = upload_payload(sample_files)
        if "--reverse" in sys.argv:
            files.reverse()
        try:
            with TestClient(app) as client:
                started = perf_counter()
                plan = preview(client, files)
                preview_seconds = perf_counter() - started
                print("PREVIEW", plan["counts"], f"{preview_seconds:.3f}s", flush=True)
                assert plan["can_confirm"], "target preview needs an explicit decision"
                assert sum(len(doc["rows"]) for doc in plan["documents"]) == 827

                started = perf_counter()
                committed = confirm(client, plan)
                commit_seconds = perf_counter() - started
                assert committed.status_code == 200, committed.text

                with target_database.SessionLocal() as db:
                    fact_count = db.query(BillFact).count()
                    raw_count = db.query(BillRaw).count()
                    file_count = db.query(ImportFile).count()
                    ledger_count = db.query(LedgerEntry).count()
                    canonical = sorted(
                        (
                            fact.account_code,
                            fact.occurred_time.isoformat(),
                            fact.cash_direction,
                            fact.amount_value,
                            fact.amount_scale,
                            fact.currency_code,
                        )
                        for fact in db.scalars(select(BillFact)).all()
                    )
                assert (fact_count, raw_count, file_count, ledger_count) == (
                    803,
                    827,
                    7,
                    803,
                )
                signature = hashlib.sha256(
                    json.dumps(canonical, ensure_ascii=False).encode()
                ).hexdigest()

                replay = confirm(client, plan)
                assert replay.status_code == 200
                assert replay.json() == committed.json()
                reversed_plan = preview(client, list(reversed(files)))
                assert reversed_plan["counts"].get("new", 0) == 0
                assert reversed_plan["counts"]["duplicate_file"] == 827
                assert confirm(client, reversed_plan).status_code == 200

                with measured_selects(target_database.engine) as list_selects:
                    page = client.get(
                        "/paam/ledger/v1/entry/list?page=1&page_size=100"
                    )
                assert page.status_code == 200, page.text
                assert page.json()["total"] == 803
                assert len(list_selects) == 3, list_selects
                assert all("SELECT *" not in sql.upper() for sql in list_selects)

                with measured_selects(target_database.engine) as summary_selects:
                    summary = client.get("/paam/ledger/v1/summary")
                assert summary.status_code == 200, summary.text
                assert summary.json()["entry_count"] == 803
                assert len(summary_selects) == 2, summary_selects

                actual_tables = set(inspect(target_database.engine).get_table_names())
                assert actual_tables == set(
                    target_database.TARGET_TABLE_NAMES
                ), actual_tables
                print(
                    "PASS target samples:",
                    f"preview={preview_seconds:.3f}s",
                    f"commit={commit_seconds:.3f}s",
                    f"facts={fact_count}",
                    f"raw={raw_count}",
                    f"signature={signature}",
                    f"list_selects={len(list_selects)}",
                    f"summary_selects={len(summary_selects)}",
                    flush=True,
                )
        finally:
            target_database.engine.dispose()


if __name__ == "__main__":
    selected = samples()
    verify_parsers(selected)
    if "--commit" in sys.argv:
        verify_target_commit(selected)
