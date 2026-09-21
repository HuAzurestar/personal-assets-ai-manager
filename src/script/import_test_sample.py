"""Import the supplied local samples into the dedicated 8765 test ledger."""

import base64
from pathlib import Path

import httpx


if __name__ == "__main__":
    sample_dir = Path("E:/Worktable/Download")
    files = [
        {
            "filename": file.name,
            "content_base64": base64.b64encode(file.read_bytes()).decode(),
        }
        for file in sample_dir.iterdir()
        if file.suffix.lower() in {".csv", ".xlsx", ".xls", ".pdf"}
    ]
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=120) as client:
        preview = client.post("/paam/import/v1/preview", json={"files": files})
        preview.raise_for_status()
        plan = preview.json()["body"]
        assert plan["can_confirm"], "Preview needs attention; no confirmation was sent"
        print("Preview:", plan["counts"], flush=True)
        response = client.post(
            f"/paam/import/v1/preview/{plan['token']}/confirm",
            json={"version": plan["version"]},
        )
        response.raise_for_status()
        print("Confirmed:", response.json()["body"], flush=True)
        records = client.get(
            "/paam/ledger/v1/flow/list", params={"page_size": 100}
        )
        records.raise_for_status()
        assert records.json()["body"]["total"] == 803
        repeated = client.post(
            f"/paam/import/v1/preview/{plan['token']}/confirm",
            json={"version": plan["version"]},
        )
        repeated.raise_for_status()
        assert repeated.json() == response.json()
        again = client.post(
            "/paam/import/v1/preview", json={"files": list(reversed(files))}
        ).json()["body"]
        assert again["counts"].get("new", 0) == 0
        assert again["counts"]["duplicate_file"] == 827
        print(
            "PASS: 8765 holds 803 facts; all 827 source rows retained; repeat upload creates no new facts",
            flush=True,
        )
