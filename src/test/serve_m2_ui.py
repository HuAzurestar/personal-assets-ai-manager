"""Shared fictional browser fixture; never attach a real ledger or keyring."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


FIXTURE_MARKER = "PIRC-24-M2-UI-FICTIONAL-v1"


def prepare_app(data_dir: Path):
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / "m2-ui-fixture.txt"
    database = data_dir / "m2-ui.db"
    if database.exists() and (not marker.exists() or marker.read_text() != FIXTURE_MARKER):
        raise RuntimeError("Refusing an unmarked existing database; use an empty dedicated directory")
    # These settings are set before backend imports. This entry never invokes a provider.
    os.environ["PAAM_DATA_DIR"] = str(data_dir)
    os.environ["PAAM_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    os.environ["PAAM_AUTOTAG_REAL_ANALYSIS"] = "0"
    os.environ["PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE"] = "0"
    os.environ["PAAM_SQL_WEB_ENABLED"] = "0"
    os.environ["PAAM_CREDENTIAL_STORE"] = "keyring"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "test"))

    from backend import target_main
    from backend.core import target_database
    from backend.router.dependency import get_protected_secret_store
    from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer

    async def forbid_provider(*_args, **_kwargs):
        raise AssertionError("A browser fixture must never call a model provider")

    ConfiguredLlmAnalyzer.analyze = forbid_provider

    class FixtureSecretStore:
        """Fake credential-presence indicator, not an OS or production credential store."""

        def __init__(self):
            self.values = {1: "FICTIONAL-NOT-A-PROVIDER-KEY"}

        def is_configured(self, model_id):
            return model_id in self.values

        def set(self, model_id, secret):
            self.values[model_id] = secret

        def delete(self, model_id):
            self.values.pop(model_id, None)

    try:
        target_database.init_target_db()
        if not marker.exists():
            seed_fixture(target_database.SessionLocal)
            marker.write_text(FIXTURE_MARKER)
    except Exception:
        target_database.engine.dispose()
        raise
    store = FixtureSecretStore()
    target_main.app.dependency_overrides[get_protected_secret_store] = lambda: store
    return target_main.app


def seed_fixture(sessions):
    # Reuse the synthetic scan / source helpers exercised by the request API tests.
    # This script is an acceptance harness, not a second production import path.
    from backend.mapper.setting_mapper import SettingMapper
    from backend.mapper.target_tag_mapper import TargetTagMapper
    from backend.service.setting_service import DEFAULT_DISCLOSURE
    from test_tag_assignment_request_api import NOW, _ledger, _rule, _scan

    with sessions() as db:
        mapper = SettingMapper(db)
        mapper.begin_write()
        if mapper.get() is not None:
            raise RuntimeError("Fixture initialization requires an empty setting")
        mapper.save({
            "schema_version": 1,
            "automation": {
                "models": [{
                    "id": 1, "name": "M2 虚构配置（不连接模型）", "enabled": True,
                    "litellm_params": {
                        "model": "openai/fictional-ui", "api_base": "https://example.test/v1",
                        "temperature": 0, "extra_body": {"enable_thinking": False},
                    },
                }],
                "disclosure": DEFAULT_DISCLOSURE,
            },
        }, NOW)
        mapper.commit()
        tags = TargetTagMapper(db)
        tags.begin_write()
        category_id = tags.create_view("虚构消费分类", "fictional_category", NOW)
        tags.create_tag(category_id, "虚构餐饮", "fictional_food", NOW)
        tags.create_tag(category_id, "虚构出行", "fictional_travel", NOW)
        mood_id = tags.create_view("虚构心情", "fictional_mood", NOW)
        tags.create_tag(mood_id, "虚构愉快", "fictional_happy", NOW)
        category = tags.view(category_id)
        mood = tags.view(mood_id)
        tags.commit()
        unclassified = next(tag.id for tag in category.tags if tag.system_name == "unclassified")
        mood_default = next(tag.id for tag in mood.tags if tag.system_name == "unclassified")
        food = next(tag.id for tag in category.tags if tag.system_name == "fictional_food")
        travel = next(tag.id for tag in category.tags if tag.system_name == "fictional_travel")
        happy = next(tag.id for tag in mood.tags if tag.system_name == "fictional_happy")
    ledger_one = _ledger(sessions, unclassified, mood_default)
    ledger_two = _ledger(sessions, unclassified, happy)
    first_rule = _rule(sessions, category_id, "M2 虚构餐饮规则")
    second_rule = _rule(sessions, category_id, "M2 虚构竞争规则")
    _rule(sessions, mood_id, "M2 虚构零样本规则")
    _scan(sessions, first_rule, ledger_one, food, "虚构餐饮")
    _scan(sessions, first_rule, ledger_two, food, "虚构餐饮")
    _scan(sessions, second_rule, ledger_one, travel, "虚构出行")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18773)
    args = parser.parse_args()
    import uvicorn

    app = prepare_app(args.data_dir)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
