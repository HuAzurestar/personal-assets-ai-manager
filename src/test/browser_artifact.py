"""Optional viewport evidence from isolated fictional browser scenarios only."""
import os
from pathlib import Path


def viewport_evidence(page, name):
    destination = os.getenv("PIRC35_BROWSER_EVIDENCE_DIR")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / f"{name}.png"), full_page=False)
