"""Deployment defaults are checked in fresh processes, without provider calls."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(("overrides", "expected"), [
    ({}, [True, False]),
    ({"PAAM_AUTOTAG_REAL_ANALYSIS": "0"}, [False, False]),
    ({"PAAM_AUTOTAG_REAL_ANALYSIS": "1"}, [True, False]),
    ({"PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE": "1", "PAAM_DATABASE_URL": "sqlite:///synthetic.db"}, [False, True]),
    ({"PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE": "1", "PAAM_DATABASE_URL": "sqlite:///ordinary.db"}, [False, False]),
    ({"PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE": "1"}, [False, False]),
    ({"PAAM_AUTOTAG_REAL_ANALYSIS": "1", "PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE": "1", "PAAM_DATABASE_URL": "sqlite:///synthetic.db"}, None),
    ({"PAAM_AUTOTAG_REAL_ANALYSIS": "false"}, None),
    ({"PAAM_AUTOTAG_REAL_ANALYSIS": ""}, None),
])
def test_deployment_analysis_defaults(overrides, expected):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PAAM_")}
    env.update(overrides)
    config = Path(__file__).resolve().parents[1] / "backend/core/config.py"
    result = subprocess.run([sys.executable, "-c",
        "import runpy,json,sys; c=runpy.run_path(sys.argv[1]); print(json.dumps([c['AUTOTAG_REAL_ANALYSIS'],c['AUTOTAG_SYNTHETIC_ACCEPTANCE']]))", str(config)],
        env=env, capture_output=True, text=True, timeout=15)
    if expected is None:
        assert result.returncode != 0
        assert "RuntimeError" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected
