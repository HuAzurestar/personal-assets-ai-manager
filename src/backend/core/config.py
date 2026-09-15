from __future__ import annotations

import os
import sys
from pathlib import Path

# Source runs keep the original PAAM/data location. Bundles keep writable data
# beside the executable, outside PyInstaller's internal resource directory.
BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[3]
)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
APP_SLUG = "personal-assets-ai-manager"
APP_DISPLAY_NAME = "个人账本与资产管家"
DATA_DIR = Path(os.getenv("PAAM_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.getenv("PAAM_DATABASE_URL", f"sqlite:///{DATA_DIR / f'{APP_SLUG}.db'}")
LLM_PROVIDER = os.getenv("PAAM_LLM_PROVIDER", "mock")
LLM_BASE_URL = os.getenv("PAAM_LLM_BASE_URL", "")
LLM_MODEL = os.getenv("PAAM_LLM_MODEL", "")
LLM_API_KEY = os.getenv("PAAM_LLM_API_KEY", "")


def ensure_data_dir() -> None:
    if DATABASE_URL.startswith("sqlite:///"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
