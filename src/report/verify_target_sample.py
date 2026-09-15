"""Verify supplied statements against only the PIRC-9 target Fact write path."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
SAMPLE_SUFFIXES = {".csv", ".xls", ".xlsx", ".pdf", ".zip"}
sample_files = sorted(
    file
    for file in Path("E:/Worktable/Download").iterdir()
    if file.is_file() and file.suffix.lower() in SAMPLE_SUFFIXES
)


with tempfile.TemporaryDirectory(prefix="paam-target-samples-") as temp:
    os.environ["PAAM_DATA_DIR"] = temp
    os.environ["PAAM_DATABASE_URL"] = "sqlite:///" + str(Path(temp) / "target.db")

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.database import (
        Account,
        Bill,
        ImportEvidence,
        ImportIdentity,
        ImportPreview,
        SessionLocal,
        engine,
    )
    from app.main import app
    from backend.entity import (
        BillFact,
        BillRaw,
        ImportFile,
        LedgerEntry,
        LedgerEntrySource,
    )

    files = [{
        "filename": file.name,
        "content_base64": base64.b64encode(file.read_bytes()).decode(),
    } for file in sample_files]

    with TestClient(app) as client:
        preview_response = client.post(
            "/paam/import/v1/preview",
            json={"files": list(reversed(files))},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["body"]
        print("TARGET_PREVIEW", preview["counts"], flush=True)
        assert preview["can_confirm"]
        assert sum(len(doc["rows"]) for doc in preview["documents"]) == 827

        confirmation = client.post(
            f"/paam/import/v1/preview/{preview['token']}/confirm",
            json={"version": preview["version"]},
        )
        assert confirmation.status_code == 200, confirmation.text
        result = confirmation.json()["body"]

        with SessionLocal() as db:
            facts = db.scalars(select(BillFact).order_by(BillFact.id)).all()
            assert len(facts) == 803
            assert db.query(BillRaw).count() == 827
            assert db.query(ImportFile).count() == 7
            assert db.query(LedgerEntry).count() == 803
            assert db.query(LedgerEntrySource).count() == 803
            assert db.query(Bill).count() == 0
            assert db.query(Account).count() == 0
            assert db.query(ImportEvidence).count() == 0
            assert db.query(ImportIdentity).count() == 0
            assert db.query(ImportPreview).count() == 0
            signature = [(
                fact.fact_key,
                fact.occurred_time.isoformat(),
                fact.cash_direction,
                fact.amount_value,
                fact.amount_scale,
                fact.currency_code,
                fact.account_code,
            ) for fact in facts]
            print(
                "TARGET_FACT_SIGNATURE",
                hashlib.sha256(json.dumps(signature).encode()).hexdigest(),
                flush=True,
            )

        repeated = client.post(
            f"/paam/import/v1/preview/{preview['token']}/confirm",
            json={"version": preview["version"]},
        )
        assert repeated.status_code == 200
        assert repeated.json()["body"] == result

        replay_response = client.post(
            "/paam/import/v1/preview",
            json={"files": files},
        )
        assert replay_response.status_code == 200, replay_response.text
        replay = replay_response.json()["body"]
        assert replay["counts"].get("new", 0) == 0
        assert replay["counts"]["duplicate_file"] == 827
        replay_confirmation = client.post(
            f"/paam/import/v1/preview/{replay['token']}/confirm",
            json={"version": replay["version"]},
        )
        assert replay_confirmation.status_code == 200
        with SessionLocal() as db:
            assert db.query(BillFact).count() == 803
            assert db.query(BillRaw).count() == 827
            assert db.query(ImportFile).count() == 7
            assert db.query(LedgerEntry).count() == 803
            assert db.query(LedgerEntrySource).count() == 803

        print(
            "PASS target-only: 803 facts/projections, 827 raw rows, 7 files; compatibility tables empty",
            flush=True,
        )
    engine.dispose()
