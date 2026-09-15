"""Compare canonical money/account semantics across complete import orders."""

import base64
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="paam-order-comparison-") as temp:
    os.environ["PAAM_DATA_DIR"] = temp
    os.environ["PAAM_DATABASE_URL"] = "sqlite:///" + str(Path(temp) / "base.db")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app import database, main
    from app.database import Bill, Account, ImportIdentity

    files = [
        {
            "filename": f.name,
            "content_base64": base64.b64encode(f.read_bytes()).decode(),
        }
        for f in Path("E:/Worktable/Download").iterdir()
    ]
    results = []
    for mode in ["batch", "reverse"]:
        engine = create_engine(
            "sqlite:///" + str(Path(temp) / (mode + ".db")),
            connect_args={"check_same_thread": False},
        )
        sessions = sessionmaker(bind=engine, autoflush=False)
        database.engine = engine
        database.SessionLocal = sessions
        main.SessionLocal = sessions
        with TestClient(main.app) as client:
            groups = (
                [files] if mode == "batch" else [[item] for item in reversed(files)]
            )
            for group in groups:
                p = client.post("/api/intake/preview", json={"files": group}).json()
                assert p["can_confirm"]
                r = client.post(
                    f"/api/intake/{p['token']}/confirm", json={"version": p["version"]}
                )
                assert r.status_code == 200, r.text
            with sessions() as db:
                records = {}
                for bill in db.scalars(select(Bill)).all():
                    keys = tuple(
                        sorted(
                            db.scalars(
                                select(ImportIdentity.key).where(
                                    ImportIdentity.bill_id == bill.id
                                )
                            ).all()
                        )
                    )
                    account = db.get(Account, bill.account_id)
                    records[keys] = (
                        account.identity,
                        account.display_name,
                        bill.occurred_at.date().isoformat(),
                        round(bill.amount * 100),
                        bill.import_nature,
                        bill.aggregate_excluded,
                    )
                assert len(records) == 803
                results.append(records)
        engine.dispose()
    assert results[0].keys() == results[1].keys(), (
        "Different canonical identities across order"
    )
    differences = [
        (key, results[0][key], results[1][key])
        for key in results[0]
        if results[0][key] != results[1][key]
    ]
    for key, a, b in differences:
        print(
            "DIFFERENCE",
            key[0][:8],
            [
                (field, a[i], b[i])
                for i, field in enumerate(
                    [
                        "account identity",
                        "account display",
                        "date",
                        "amount cents",
                        "nature",
                        "excluded",
                    ]
                )
                if a[i] != b[i]
            ],
            flush=True,
        )
    assert not differences, f"{len(differences)} facts differ"
    print(
        "PASS: all 803 canonical identities, accounts, amounts, dates and nature agree across order",
        flush=True,
    )
