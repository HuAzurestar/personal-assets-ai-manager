"""Install the reviewed classification preset through local v1 APIs.

Read-only unless --apply is supplied. New rules are ALWAYS disabled; this
command cannot authorize model spending, alter old rules or approve requests.
Reruns reuse exact matches and refuse conflicting user-owned configuration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

DEFINITION_PATH = Path(__file__).resolve().parents[1] / "asset/tag/classification.json"


def body(response):
    response.raise_for_status()
    value = response.json()
    if value.get("status") != 200:
        raise ValueError("API response is not a successful business result")
    return value["body"]


def records(client, path):
    result = []
    page = 1
    while True:
        data = body(client.get(path, params={"page_index": page, "page_size": 100}))
        result.extend(data["items"])
        if len(result) >= data["total"]:
            return result
        if not data["items"]:
            raise ValueError("Configuration changed while reading; retry")
        page += 1


def install(client, model_id: int, *, apply: bool = False):
    definition = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    models = body(client.get("/paam/system/v1/setting/automation"))["models"]
    if not any(model["id"] == model_id for model in models):
        raise ValueError("Select an existing model before installing the preset")
    views = records(client, "/paam/tag/v1/view/list")
    rules = records(client, "/paam/tag/v1/auto_rule/list")
    plan = []
    # Validate the ENTIRE existing configuration before making the first write.
    for spec in definition["views"]:
        view = next((row for row in views if row["system_name"] == spec["system_name"]), None)
        existing = []
        if view:
            if view["name"] != spec["name"] or view["status"] != "ACTIVE":
                raise ValueError("Existing View conflicts with the preset; no overwrite")
            expected = {tag["system_name"]: tag["name"] for tag in spec["tags"]}
            for tag in view["tags"]:
                if tag["system_name"] == "unclassified":
                    continue
                if expected.get(tag["system_name"]) != tag["name"] or tag["status"] != "ACTIVE":
                    raise ValueError("Existing Tag conflicts with the preset; no overwrite")
            existing = [row for row in rules if row["view_id"] == view["id"]]
        rule_spec = spec["rule"]
        config = {"schema_version": 1, "model_id": model_id, "prompt": rule_spec["prompt"], "prompt_id": "tag-suggestion"}
        if len(existing) > 1 or any(
            row["name"] != rule_spec["name"] or row["method_config"] != config
            or row["amount_mode"] != rule_spec["amount_mode"] or row["cron"] != rule_spec["cron"]
            for row in existing
        ):
            raise ValueError("Existing rule conflicts with the preset; no overwrite")
        missing = [tag for tag in spec["tags"] if not view or not any(
            row["system_name"] == tag["system_name"] for row in view["tags"]
        )]
        if existing and missing:
            # Adding tags would invalidate an existing rule and its pending work.
            raise ValueError("Existing rule has an incomplete dictionary; no cursor reset")
        plan.append((spec, view, existing[0] if existing else None, missing, config))

    result = []
    for spec, view, rule, missing, config in plan:
        if apply:
            if view is None:
                view = body(client.post("/paam/tag/v1/view", json={
                    key: spec[key] for key in ("name", "system_name")
                }))
            for tag in missing:
                view = body(client.post(f"/paam/tag/v1/view/{view['id']}/tag", json=tag))
            if rule is None:
                rule = body(client.post("/paam/tag/v1/auto_rule", json={
                    "view_id": view["id"], "name": spec["rule"]["name"],
                    "enabled": False, "cron": spec["rule"]["cron"],
                    "amount_mode": spec["rule"]["amount_mode"], "method_config": config,
                }))
        result.append({
            "view": spec["name"], "view_id": view["id"] if view else None,
            "tag_count": len(spec["tags"]), "rule_id": rule["id"] if rule else None,
            "enabled": rule["enabled"] if rule else False,
        })
    return {"applied": apply, "configuration": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18775")
    parser.add_argument("--model-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.path not in {"", "/"} or url.query or url.fragment or url.username or url.password:
        parser.error("Use an explicit local application URL without credentials or path")
    with httpx.Client(base_url=args.base_url, timeout=30, trust_env=False) as client:
        print(json.dumps(install(client, args.model_id, apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
