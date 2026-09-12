"""Run the import test UI with a separate persistent test ledger."""

import os
from pathlib import Path
import sys

if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    data = ROOT / "data" / "import-test"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["PAAM_DATA_DIR"] = str(data)
    os.environ["PAAM_DATABASE_URL"] = "sqlite:///" + str(data / "ledger.db")
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, log_level="warning")
