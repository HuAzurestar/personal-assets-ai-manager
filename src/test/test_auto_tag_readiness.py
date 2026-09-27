"""Deployment checks must not confuse a healthy scheduler with enabled scans."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "script"))
from verify_auto_tag_runtime import assess  # noqa: E402


def snapshot(guard="REAL_READY", state="IDLE"):
    return {"tag_scan_guard": guard, "scheduler_state": "RUNNING",
            "worker_state": "HEALTHY", "tasks": [
                {"task_key": "system:import-preview-timeout", "queue_state": "IDLE"},
                {"task_key": "tag-scan:3", "queue_state": state}]}


def test_disabled_scan_cannot_pass_with_healthy_worker():
    status = snapshot("DISABLED")
    status["tasks"] = status["tasks"][:1]
    result = assess(status, [{"id": 3, "enabled": True}])
    assert result["problems"] == ["REAL_ANALYSIS_NOT_ENABLED", "RULE_3_NOT_REGISTERED"]
    assert not result["ready"]


@pytest.mark.parametrize("state", ["BLOCKED", "PAUSED"])
def test_registered_but_stopped_scan_is_not_ready(state):
    assert not assess(snapshot(state=state), [{"id": 3, "enabled": True}])["ready"]


def test_disabled_rules_do_not_require_registration():
    assert assess(snapshot(), [{"id": 2, "enabled": False}, {"id": 3, "enabled": True}])["ready"]


def test_synthetic_mode_does_not_prove_real_readiness():
    assert not assess(snapshot("SYNTHETIC_READY"), [{"id": 3, "enabled": True}])["ready"]


def test_no_enabled_rules_is_explicit():
    assert "NO_ENABLED_RULES" in assess(snapshot(), [])["problems"]
