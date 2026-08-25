from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_SLUG = "personal-assets-ai-manager"
APP_DISPLAY_NAME = "Personal Assets AI Manager"
DATA_DIR = Path(os.getenv("PAAM_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.getenv("PAAM_DATABASE_URL", f"sqlite:///{DATA_DIR / f'{APP_SLUG}.db'}")
LLM_PROVIDER = os.getenv("PAAM_LLM_PROVIDER", "mock")
LLM_BASE_URL = os.getenv("PAAM_LLM_BASE_URL", "")
LLM_MODEL = os.getenv("PAAM_LLM_MODEL", "")
LLM_API_KEY = os.getenv("PAAM_LLM_API_KEY", "")


def ensure_data_dir() -> None:
    if DATABASE_URL.startswith("sqlite:///"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
