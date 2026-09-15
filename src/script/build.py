"""Build a Windows executable after `pip install pyinstaller`.

PyInstaller uses a directory bundle so the editable SQLite database and static
web assets remain easy to upgrade and back up.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SEP = ";" if sys.platform == "win32" else ":"

command = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "personal-assets-ai-manager",
    "--onedir", "--paths", str(ROOT / "src"),
    "--add-data", f"{ROOT / 'src' / 'frontend'}{SEP}frontend",
    "--add-data", f"{ROOT / 'src' / 'asset'}{SEP}asset",
    "--hidden-import", "backend.target_main", "--collect-all", "uvicorn",
    str(ROOT / "run.py"),
]
subprocess.run(command, cwd=ROOT, check=True)
