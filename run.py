from pathlib import Path
import sys

import uvicorn


if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    uvicorn.run("backend.target_main:app", host="0.0.0.0", port=8765, reload=False)
