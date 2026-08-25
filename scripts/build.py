"""Build a Windows executable after `pip install pyinstaller`.

PyInstaller uses a directory bundle so the editable SQLite database and static
web assets remain easy to upgrade and back up.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if sys.platform == "win32" else ":"

command = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "AssetMind",
    "--onedir", "--add-data", f"{ROOT / 'app' / 'templates'}{SEP}app/templates",
    "--add-data", f"{ROOT / 'app' / 'static'}{SEP}app/static", "--collect-all", "uvicorn",
    str(ROOT / "run.py"),
]
subprocess.run(command, cwd=ROOT, check=True)
