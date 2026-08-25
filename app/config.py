from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("ASSETMIND_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.getenv("ASSETMIND_DATABASE_URL", f"sqlite:///{DATA_DIR / 'assetmind.db'}")
LLM_PROVIDER = os.getenv("ASSETMIND_LLM_PROVIDER", "mock")
LLM_BASE_URL = os.getenv("ASSETMIND_LLM_BASE_URL", "")
LLM_MODEL = os.getenv("ASSETMIND_LLM_MODEL", "")
LLM_API_KEY = os.getenv("ASSETMIND_LLM_API_KEY", "")


def ensure_data_dir() -> None:
    if DATABASE_URL.startswith("sqlite:///"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
