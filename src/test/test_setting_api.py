from __future__ import annotations

import json
import socket
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.core.protected_secret_store import KeyringProtectedSecretStore
from backend.error import ProtectedSecretStoreError
from backend.entity import AutoTagRule, Setting
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.router.dependency import get_db, get_protected_secret_store
from backend.router.error import register_error_handlers
from backend.router.system_setting import router as system_setting_router


class FakeProtectedSecretStore:
    def __init__(self):
        self.values: dict[int, str] = {}

    def is_configured(self, model_id: int) -> bool:
        return model_id in self.values

    def set(self, model_id: int, secret: str) -> None:
        self.values[model_id] = secret

    def delete(self, model_id: int) -> None:
        self.values.pop(model_id, None)


@pytest.fixture
def setting_runtime(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'setting.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    store = FakeProtectedSecretStore()
    init_target_db(bind=engine)
    try:
        yield sessions, engine, store
    finally:
        engine.dispose()


def _client(sessions, store: FakeProtectedSecretStore) -> TestClient:
    api = FastAPI()
    register_error_handlers(api)
    api.include_router(system_setting_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    api.dependency_overrides[get_protected_secret_store] = lambda: store
    return TestClient(api)


def _model_payload(*, temperature: float = 0.0, name: str = "Free model"):
    return {
        "id": 1,
        "name": name,
        "enabled": False,
        "litellm_params": {
            "model": "openai/Qwen/Qwen3-8B",
            "api_base": "https://api.example.test/v1",
            "temperature": temperature,
            "max_tokens": 512,
            "timeout": 60,
            "provider_zero": 0,
            "provider_flag": False,
            "extra_body": {"enable_thinking": False},
        },
    }


def test_model_configuration_secret_lifecycle_and_restart(setting_runtime, monkeypatch):
    sessions, _, store = setting_runtime
    with _client(sessions, store) as client:
        initial = client.get("/paam/system/v1/setting/automation")
        assert initial.status_code == 200
        assert initial.json()["body"] == {
            "models": [],
            "disclosure": {
                "date_granularity": "DAY",
                "amount_bands": {
                    "CNY": {"boundaries": [0, 3000, 10000, 50000, 300000]},
                },
            },
            "updated_time": None,
        }

        created = client.put(
            "/paam/system/v1/setting/automation",
            json={"expected_updated_time": None, "models": [_model_payload()]},
        )
        assert created.status_code == 200, created.text
        created_body = created.json()["body"]
        token = created_body["updated_time"]
        params = created_body["models"][0]["litellm_params"]
        assert params["temperature"] == 0
        assert params["provider_zero"] == 0
        assert params["provider_flag"] is False
        assert params["extra_body"]["enable_thinking"] is False
        assert created_body["models"][0]["key_configured"] is False

        missing_key = client.put(
            "/paam/system/v1/setting/automation",
            json={
                "expected_updated_time": token,
                "models": [{**_model_payload(), "enabled": True}],
            },
        )
        assert missing_key.status_code == 422
        assert missing_key.json()["body"]["code"] == "MODEL_KEY_REQUIRED"

        secret = client.put(
            "/paam/system/v1/setting/automation/model/1/secret",
            json={"secret": "sk-private-first"},
        )
        assert secret.status_code == 200
        assert secret.json()["body"] == {"model_id": 1, "key_configured": True}
        assert store.values[1] == "sk-private-first"

        rotated = client.put(
            "/paam/system/v1/setting/automation/model/1/secret",
            json={"secret": "sk-private-second"},
        )
        assert rotated.status_code == 200
        assert store.values[1] == "sk-private-second"

        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda *args, **kwargs: pytest.fail("connection test attempted network I/O"),
        )
        tested = client.post("/paam/system/v1/setting/automation/model/1/test")
        assert tested.status_code == 200
        assert tested.json()["body"] == {
            "model_id": 1,
            "connected": False,
            "mode": "SIMULATED",
            "key_configured": True,
            "message": "M1-UI demo only; no provider request was sent",
        }

        ordinary = client.get("/paam/system/v1/setting/automation")
        assert ordinary.json()["body"]["models"][0]["key_configured"] is True
        assert "sk-private" not in ordinary.text

        deleted = client.delete(
            "/paam/system/v1/setting/automation/model/1/secret"
        )
        assert deleted.status_code == 200
        assert deleted.json()["body"]["key_configured"] is False

    with sessions() as db:
        persisted = db.scalar(select(Setting.value_json).where(Setting.id == 1))
    assert persisted is not None
    assert "secret" not in persisted.casefold()
    assert "api_key" not in persisted.casefold()
    persisted_params = json.loads(persisted)["automation"]["models"][0][
        "litellm_params"
    ]
    assert persisted_params["provider_zero"] == 0
    assert persisted_params["provider_flag"] is False

    # A new app/client and fresh SQLAlchemy sessions recover the same profile.
    with _client(sessions, store) as restarted_client:
        recovered = restarted_client.get("/paam/system/v1/setting/automation")
    assert recovered.status_code == 200
    assert recovered.json()["body"]["models"][0]["id"] == 1
    assert recovered.json()["body"]["models"][0]["key_configured"] is False


def test_parameter_change_invalidates_rules_and_uses_exact_lock_token(
    setting_runtime,
):
    sessions, _, store = setting_runtime
    with _client(sessions, store) as client:
        created = client.put(
            "/paam/system/v1/setting/automation",
            json={"expected_updated_time": None, "models": [_model_payload()]},
        )
        first_token = created.json()["body"]["updated_time"]

        with sessions() as db:
            mapper = AutoTagRuleMapper(db)
            mapper.begin_write()
            mapper.create(
                name="Synthetic rule",
                view_id=3,
                method_config={
                    "schema_version": 1,
                    "model_id": 1,
                    "prompt": "synthetic only",
                },
                now=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            mapper.commit()

        changed = client.put(
            "/paam/system/v1/setting/automation",
            json={
                "expected_updated_time": first_token,
                "models": [_model_payload(temperature=0.25)],
            },
        )
        assert changed.status_code == 200, changed.text
        second_token = changed.json()["body"]["updated_time"]
        assert second_token != first_token
        with sessions() as db:
            rule = db.scalar(select(AutoTagRule))
            assert rule is not None
            assert (rule.rule_revision, rule.scan_epoch, rule.scan_after_ledger_id) == (
                2,
                2,
                0,
            )

        renamed = client.put(
            "/paam/system/v1/setting/automation",
            json={
                "expected_updated_time": second_token,
                "models": [_model_payload(temperature=0.25, name="Renamed")],
            },
        )
        assert renamed.status_code == 200
        third_token = renamed.json()["body"]["updated_time"]
        with sessions() as db:
            rule = db.scalar(select(AutoTagRule))
            assert rule is not None
            assert rule.rule_revision == 2

        typed_change_model = _model_payload(temperature=0.25, name="Renamed")
        typed_change_model["litellm_params"]["provider_zero"] = False
        typed_change = client.put(
            "/paam/system/v1/setting/automation",
            json={
                "expected_updated_time": third_token,
                "models": [typed_change_model],
            },
        )
        assert typed_change.status_code == 200
        fourth_token = typed_change.json()["body"]["updated_time"]
        with sessions() as db:
            rule = db.scalar(select(AutoTagRule))
            assert rule is not None
            assert rule.rule_revision == 3

        stale = client.put(
            "/paam/system/v1/setting/automation",
            json={"expected_updated_time": second_token, "models": [_model_payload()]},
        )
        assert stale.status_code == 409
        assert stale.json()["body"]["code"] == "SETTING_VERSION_CONFLICT"

        removed = client.put(
            "/paam/system/v1/setting/automation",
            json={"expected_updated_time": fourth_token, "models": []},
        )
        assert removed.status_code == 422
        assert removed.json()["body"]["code"] == "MODEL_REMOVAL_NOT_SUPPORTED"


@pytest.mark.parametrize(
    "invalid_params",
    [
        {"api_key": "sk-must-not-leak"},
        {"extra_body": {"callbacks": ["must-not-run"]}},
        {"class_path": "package.Provider"},
    ],
)
def test_unsafe_provider_parameters_are_rejected_and_redacted(
    setting_runtime,
    invalid_params,
):
    sessions, _, store = setting_runtime
    model = _model_payload()
    model["litellm_params"].update(invalid_params)
    with _client(sessions, store) as client:
        response = client.put(
            "/paam/system/v1/setting/automation",
            json={"expected_updated_time": None, "models": [model]},
        )
    assert response.status_code == 422
    assert response.json()["body"]["code"] == "VALIDATION_ERROR"
    assert "sk-must-not-leak" not in response.text


def test_duplicate_ids_and_insecure_api_base_are_rejected(setting_runtime):
    sessions, _, store = setting_runtime
    with _client(sessions, store) as client:
        duplicate = client.put(
            "/paam/system/v1/setting/automation",
            json={
                "expected_updated_time": None,
                "models": [_model_payload(), _model_payload()],
            },
        )
        assert duplicate.status_code == 422

        model = _model_payload()
        model["litellm_params"]["api_base"] = "http://api.example.test/v1"
        insecure = client.put(
            "/paam/system/v1/setting/automation",
            json={"expected_updated_time": None, "models": [model]},
        )
        assert insecure.status_code == 422


def test_model_update_preserves_disclosure_and_other_root_settings(setting_runtime):
    sessions, _, store = setting_runtime
    with sessions() as db:
        mapper = SettingMapper(db)
        mapper.begin_write()
        mapper.save(
            {
                "schema_version": 1,
                "appearance": {"theme": "dark"},
                "automation": {
                    "models": [],
                    "disclosure": {
                        "date_granularity": "MONTH",
                        "amount_bands": {
                            "USD": {"boundaries": [0, 10000]},
                        },
                    },
                },
            },
            datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        mapper.commit()

    with _client(sessions, store) as client:
        current = client.get("/paam/system/v1/setting/automation").json()["body"]
        updated = client.put(
            "/paam/system/v1/setting/automation",
            json={
                "expected_updated_time": current["updated_time"],
                "models": [_model_payload()],
            },
        )
    assert updated.status_code == 200, updated.text
    with sessions() as db:
        value = SettingMapper(db).get()["value"]
    assert value["appearance"] == {"theme": "dark"}
    assert value["automation"]["disclosure"] == {
        "date_granularity": "MONTH",
        "amount_bands": {"USD": {"boundaries": [0, 10000]}},
    }


def test_keyring_adapter_uses_fixed_identity_and_never_falls_back(monkeypatch):
    calls: list[tuple[object, ...]] = []
    configured = {"value": None}

    def get_password(service, username):
        calls.append(("get", service, username))
        return configured["value"]

    def set_password(service, username, secret):
        calls.append(("set", service, username, secret))
        configured["value"] = secret

    def delete_password(service, username):
        calls.append(("delete", service, username))
        configured["value"] = None

    monkeypatch.setattr("keyring.get_password", get_password)
    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.delete_password", delete_password)
    store = KeyringProtectedSecretStore()
    assert store.is_configured(7) is False
    store.set(7, "private")
    assert store.is_configured(7) is True
    store.delete(7)
    assert calls == [
        ("get", "PAAM.llm", "model/7"),
        ("set", "PAAM.llm", "model/7", "private"),
        ("get", "PAAM.llm", "model/7"),
        ("get", "PAAM.llm", "model/7"),
        ("delete", "PAAM.llm", "model/7"),
    ]

    def unavailable(*_):
        from keyring.errors import NoKeyringError

        raise NoKeyringError("no backend")

    monkeypatch.setattr("keyring.get_password", unavailable)
    with pytest.raises(ProtectedSecretStoreError) as error:
        store.is_configured(7)
    assert error.value.status_code == 503
