"""Optional local sample verification. Does not copy or print private row data."""

import collections
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.statement_parser import parse_statement

SAMPLE_SUFFIXES = {".csv", ".xls", ".xlsx", ".pdf", ".zip"}
sample_files = sorted(
    file
    for file in Path("E:/Worktable/Download").iterdir()
    if file.is_file() and file.suffix.lower() in SAMPLE_SUFFIXES
)

for file in sample_files:
    try:
        parsed = parse_statement(file.read_bytes(), file.name)
        print(
            json.dumps(
                {
                    "file": file.name,
                    "source": parsed["source_type"],
                    "rows": len(parsed["rows"]),
                    "errors": dict(
                        collections.Counter(
                            r["error"] for r in parsed["rows"] if r["error"]
                        )
                    ),
                    "dispositions": dict(
                        collections.Counter(r["disposition"] for r in parsed["rows"])
                    ),
                    "precision": dict(
                        collections.Counter(
                            r.get("time_precision") for r in parsed["rows"]
                        )
                    ),
                    "references": sum(bool(r.get("reference")) for r in parsed["rows"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as error:
        print(file.name, type(error).__name__, str(error), flush=True)

if "--commit" in sys.argv:
    with tempfile.TemporaryDirectory(prefix="paam-samples-") as temp:
        os.environ["PAAM_DATA_DIR"] = temp
        os.environ["PAAM_DATABASE_URL"] = "sqlite:///" + str(Path(temp) / "sample.db")
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import engine, SessionLocal, Bill, ImportEvidence, Account
        from sqlalchemy import select

        files = [
            {
                "filename": f.name,
                "content_base64": base64.b64encode(f.read_bytes()).decode(),
            }
            for f in sample_files
        ]
        with TestClient(app) as client:
            if "--reverse" in sys.argv:
                files = list(reversed(files))
            if "--sequential" in sys.argv:
                for item in files:
                    p = client.post(
                        "/api/intake/preview", json={"files": [item]}
                    ).json()
                    print("SEQUENTIAL", item["filename"], p["counts"], flush=True)
                    assert p["can_confirm"]
                    r = client.post(
                        f"/api/intake/{p['token']}/confirm",
                        json={"version": p["version"]},
                    )
                    assert r.status_code == 200, r.text
            response = client.post("/api/intake/preview", json={"files": files})
            assert response.status_code == 200, response.text
            preview = response.json()
            print("PREVIEW", preview["counts"], flush=True)
            assert preview["can_confirm"]
            assert sum(len(d["rows"]) for d in preview["documents"]) == 827
            commit = client.post(
                f"/api/intake/{preview['token']}/confirm",
                json={"version": preview["version"]},
            )
            assert commit.status_code == 200, commit.text
            with SessionLocal() as db:
                snapshot = [
                    (b.id, b.amount)
                    for b in db.scalars(select(Bill).order_by(Bill.id)).all()
                ]
                assert len(snapshot) == 803
                canonical = sorted(
                    (
                        db.get(Account, b.account_id).identity,
                        b.occurred_at.date().isoformat(),
                        round(b.amount * 100),
                        b.import_nature,
                    )
                    for b in db.scalars(select(Bill)).all()
                )
                print(
                    "MONEY_AND_ACCOUNT_SIGNATURE",
                    hashlib.sha256(json.dumps(canonical).encode()).hexdigest(),
                    flush=True,
                )
                assert db.query(ImportEvidence).count() == 827
                print(
                    "SAVED",
                    len(snapshot),
                    "facts;",
                    db.query(ImportEvidence).count(),
                    "evidence rows",
                    flush=True,
                )
            again = client.post(
                f"/api/intake/{preview['token']}/confirm",
                json={"version": preview["version"]},
            )
            assert again.status_code == 200
            replay = client.post(
                "/api/intake/preview", json={"files": list(reversed(files))}
            ).json()
            assert replay["counts"].get("new", 0) == 0
            assert replay["counts"]["duplicate_file"] == 827
            replay_commit = client.post(
                f"/api/intake/{replay['token']}/confirm",
                json={"version": replay["version"]},
            )
            assert replay_commit.status_code == 200, replay_commit.text
            with SessionLocal() as db:
                assert snapshot == [
                    (b.id, b.amount)
                    for b in db.scalars(select(Bill).order_by(Bill.id)).all()
                ]
            print(
                "PASS real files: all rows retained, retry and reverse-order reupload unchanged",
                flush=True,
            )
        engine.dispose()
