"""Read-only deployment readiness check; never calls a model or changes rules."""

from __future__ import annotations

import argparse
import json
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


def assess(schedule: dict, rules: list[dict]) -> dict:
    problems = []
    if schedule.get("tag_scan_guard") != "REAL_READY":
        problems.append("REAL_ANALYSIS_NOT_ENABLED")
    if schedule.get("scheduler_state") != "RUNNING":
        problems.append("SCHEDULER_NOT_RUNNING")
    if schedule.get("worker_state") != "HEALTHY":
        problems.append("WORKER_NOT_HEALTHY")
    enabled = {rule["id"] for rule in rules if rule["enabled"]}
    if not enabled:
        problems.append("NO_ENABLED_RULES")
    tasks = {task["task_key"]: task for task in schedule.get("tasks", [])}
    for rule_id in sorted(enabled):
        task = tasks.get(f"tag-scan:{rule_id}")
        if task is None:
            problems.append(f"RULE_{rule_id}_NOT_REGISTERED")
        elif task["queue_state"] in {"BLOCKED", "PAUSED"}:
            problems.append(f"RULE_{rule_id}_{task['queue_state']}")
    return {
        "ready": not problems,
        "guard": schedule.get("tag_scan_guard"),
        "enabled_rule_ids": sorted(enabled),
        "problems": problems,
        "scope": "Scheduling readiness only; verify new requests and model results separately.",
    }


def check(base_url: str) -> dict:
    url = urlsplit(base_url)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
            or url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}):
        raise ValueError("Expected a local HTTP application URL without credentials or path")
    opener = build_opener(ProxyHandler({}))

    def get(path):
        with opener.open(base_url.rstrip("/") + path, timeout=10) as response:
            result = json.load(response)
        if result.get("status") != 200:
            raise ValueError("Unsuccessful API result")
        return result["body"]

    rules = []
    page = 1
    while True:
        result = get(f"/paam/tag/v1/auto_rule/list?page_index={page}&page_size=100")
        rules.extend(result["items"])
        if len(rules) >= result["total"]:
            break
        if not result["items"]:
            raise ValueError("Rules changed while checking; retry")
        page += 1
    return assess(get("/paam/system/v1/schedule/status"), rules)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    try:
        result = check(args.base_url)
    except Exception as error:
        # Do not print raw responses, request URLs or credentials in operator logs.
        result = {"ready": False, "problems": ["CHECK_FAILED"], "error_type": type(error).__name__}
    print(json.dumps(result))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
