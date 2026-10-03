"""Keep each browser scenario's imported backend and SQLite lifecycle isolated."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.browser
@pytest.mark.parametrize("scenario", [
    "verify_pr9_fix.py",
    "verify_automation_repair.py",
    "verify_refresh_ui.py",
    "verify_automation_presentation.py",
    "verify_m2_ui.py",
    "verify_pirc35_account.py",
    "verify_pirc35_account_scope.py",
    "verify_pirc35_position.py",
    "verify_pirc35_import.py",
    "verify_pirc35_import_plan.py",
    "verify_pirc35_flow.py",
    "verify_pirc35_review.py",
    "verify_pirc35_draft.py",
    "verify_pirc35_unit.py",
    "verify_pirc35_filter.py",
    "verify_pirc35_scene.py",
    "verify_pirc35_member.py",
    "verify_pirc35_flow_state.py",
])
def test_browser_scenario(scenario):
    source = Path(__file__).parent / scenario
    # Never inherit a live database, credential store or analysis configuration.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PAAM_")}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PAAM_SQL_WEB_ENABLED="0")
    result = subprocess.run(
        [sys.executable, str(source)], cwd=source.parents[2], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
