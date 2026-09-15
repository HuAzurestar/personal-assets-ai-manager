"""Migration checks for resource and writable-data boundaries."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_entry_imports_backend_from_an_unrelated_working_directory(tmp_path):
    env = dict(os.environ)
    env.pop("PAAM_DATA_DIR", None)
    env.pop("PAAM_DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", (
            "import json, runpy, sys; runpy.run_path(sys.argv[1]); "
            "from backend.core.config import DATA_DIR, RESOURCE_DIR; "
            "print(json.dumps([str(DATA_DIR), str(RESOURCE_DIR)]))"
        ), str(ROOT / "run.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    data_dir, resource_dir = map(Path, json.loads(result.stdout))
    assert data_dir == ROOT / "data"
    assert (resource_dir / "frontend/target.html").is_file()
    assert (resource_dir / "asset/provider/wechat.svg").is_file()


def test_bundle_keeps_writable_data_outside_internal_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "paam.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.delenv("PAAM_DATA_DIR", raising=False)
    monkeypatch.delenv("PAAM_DATABASE_URL", raising=False)
    config = runpy.run_path(str(ROOT / "src/backend/core/config.py"))
    assert config["DATA_DIR"] == tmp_path / "data"
    assert config["RESOURCE_DIR"] == tmp_path / "_internal"
    monkeypatch.setenv("PAAM_DATA_DIR", str(tmp_path / "selected-data"))
    monkeypatch.setenv("PAAM_DATABASE_URL", "sqlite:///selected.db")
    config = runpy.run_path(str(ROOT / "src/backend/core/config.py"))
    assert config["DATA_DIR"] == tmp_path / "selected-data"
    assert config["DATABASE_URL"] == "sqlite:///selected.db"
