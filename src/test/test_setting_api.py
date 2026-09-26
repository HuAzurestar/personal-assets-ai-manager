from __future__ import annotations

import json
import socket
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.core.protected_secret_store import KeyringProtectedSecretStore
from backend.error import ProtectedSecretStoreError
from backend.entity import (
    AutoTagRule,
    Setting,
    TAG_REQUEST_STATUS_CANCELLED,
    TagAssignmentRequest,
)
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
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
            rule_id = mapper.create(
                name="Synthetic rule",
                view_id=3,
                method_config={
                    "schema_version": 1,
                    "model_id": 1,
                    "prompt": "synthetic only",
                },
                now=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
            TagAssignmentRequestMapper(db).create_many([{
                "rule_id": rule_id,
                "rule_revision": 1,
                "ledger_id": 11,
                "view_id": 3,
                "proposed_tag_id": 5,
                "reason_summary": "synthetic",
            }], datetime(2026, 9, 22, tzinfo=timezone.utc))
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
            request = db.scalar(select(TagAssignmentRequest))
            assert rule is not None
            assert (rule.rule_revision, rule.scan_epoch, rule.scan_after_ledger_id) == (
                2,
                2,
                0,
            )
            assert request is not None
            assert request.status == TAG_REQUEST_STATUS_CANCELLED

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


def _seed_disclosure_rules(sessions):
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    with sessions() as db:
        db.add_all([
            AutoTagRule(
                id=mode, name=f"Synthetic mode {mode}", view_id=3,
                method_config_json=json.dumps({
                    "schema_version": 1, "model_id": 1, "prompt": "synthetic only",
                }),
                amount_mode=mode, scan_after_ledger_id=20,
                created_time=now, updated_time=now,
            )
            for mode in (1, 2, 3)
        ])
        db.add_all([
            TagAssignmentRequest(
                id=mode, rule_id=mode, rule_revision=1, ledger_id=11,
                view_id=3, proposed_tag_id=5, status=1, reason_summary="synthetic",
                created_time=now, updated_time=now,
            )
            for mode in (1, 2, 3)
        ])
        db.add(TagAssignmentRequest(
            id=4, rule_id=1, rule_revision=1, ledger_id=12,
            view_id=3, proposed_tag_id=5, status=2, reason_summary="synthetic enabled",
            created_time=now, updated_time=now,
        ))
        db.commit()


def test_disclosure_partial_update_scoped_invalidation_and_restart(setting_runtime):
    sessions, engine, store = setting_runtime
    uri = "/paam/system/v1/setting/automation"
    disclosure = {
        "date_granularity": "DAY",
        "amount_bands": {
            "CNY": {"boundaries": [0, 3000, 350000]},
            "CNY_4": {"boundaries": [0, 300000, 9_000_000_000_000]},
            "USD": {"boundaries": [0]},
        },
    }
    with _client(sessions, store) as client:
        initial = client.put(uri, json={
            "expected_updated_time": None, "models": [_model_payload()],
        }).json()["body"]
        _seed_disclosure_rules(sessions)
        saved = client.put(uri, json={
            "expected_updated_time": initial["updated_time"], "disclosure": disclosure,
        })
        assert saved.status_code == 200, saved.text
        first = saved.json()["body"]
        assert first["disclosure"] == disclosure
        assert first["models"] == initial["models"]
        with sessions() as db:
            rules = db.scalars(select(AutoTagRule).order_by(AutoTagRule.id)).all()
            assert [rule.rule_revision for rule in rules] == [2, 1, 1]
            assert [rule.scan_epoch for rule in rules] == [2, 1, 1]
            assert [rule.scan_after_ledger_id for rule in rules] == [0, 20, 20]
            requests = db.scalars(select(TagAssignmentRequest).order_by(
                TagAssignmentRequest.id,
            )).all()
            assert [request.status for request in requests] == [4, 1, 1, 2]
            assert all(rule.analyzed_count == 0 for rule in rules)
        # Reusing the exact returned token succeeds; unchanged policy does not reset.
        repeated = client.put(uri, json={
            "expected_updated_time": first["updated_time"], "disclosure": disclosure,
        })
        assert repeated.status_code == 200, repeated.text
        second = repeated.json()["body"]
        assert second["updated_time"] != first["updated_time"]
        stale = client.put(uri, json={
            "expected_updated_time": first["updated_time"],
            "disclosure": {"date_granularity": "NONE", "amount_bands": {}},
        })
        assert stale.status_code == 409
        assert stale.json()["body"]["code"] == "SETTING_VERSION_CONFLICT"
        assert client.get(uri).json()["body"] == second
        with sessions() as db:
            assert db.get(AutoTagRule, 1).rule_revision == 2
    engine.dispose()
    with _client(sessions, store) as client:
        assert client.get(uri).json()["body"] == second


@pytest.mark.parametrize("disclosure", [
    {"amount_bands": {"CNY": {"boundaries": []}}},
    {"amount_bands": {"CNY": {"boundaries": [1, 2]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, 2, 2]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, 2, 1]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, -1]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, True]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, 1.5]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, "10"]}}},
    {"amount_bands": {"CNY": {"boundaries": [0, 9_000_000_000_001]}}},
    {"amount_bands": {"CNY": {"boundaries": list(range(65))}}},
    {"amount_bands": {"XYZ": {"boundaries": [0]}}},
    {"amount_bands": {"CNY_9": {"boundaries": [0]}}},
    {"amount_bands": {"cny": {"boundaries": [0]}}},
    {"amount_bands": {"CNY": {"boundaries": [0], "extra": True}}},
    {"date_granularity": "SECOND"},
    {"disable_identity_protection": True},
])
def test_invalid_disclosure_is_rejected_without_writes(setting_runtime, disclosure):
    sessions, _, store = setting_runtime
    with _client(sessions, store) as client:
        response = client.put("/paam/system/v1/setting/automation", json={
            "expected_updated_time": None, "disclosure": disclosure,
        })
    assert response.status_code == 422, response.text
    with sessions() as db:
        assert db.get(Setting, 1) is None


@pytest.mark.parametrize("extra", [{}, {"models": None}, {"disclosure": None}])
def test_empty_or_null_setting_sections_do_not_silently_clear(setting_runtime, extra):
    sessions, _, store = setting_runtime
    with _client(sessions, store) as client:
        response = client.put("/paam/system/v1/setting/automation", json={
            "expected_updated_time": None, **extra,
        })
    assert response.status_code == 422


def test_combined_model_and_disclosure_change_advances_each_rule_once(setting_runtime):
    sessions, _, store = setting_runtime
    uri = "/paam/system/v1/setting/automation"
    with _client(sessions, store) as client:
        initial = client.put(uri, json={
            "expected_updated_time": None, "models": [_model_payload()],
        }).json()["body"]
        _seed_disclosure_rules(sessions)
        combined = client.put(uri, json={
            "expected_updated_time": initial["updated_time"],
            "models": [_model_payload(temperature=0.3)],
            "disclosure": {"date_granularity": "DAY", "amount_bands": {}},
        })
        assert combined.status_code == 200, combined.text
        with sessions() as db:
            assert [rule.rule_revision for rule in db.scalars(select(AutoTagRule))] == [2, 2, 2]
        changed_time = client.put(uri, json={
            "expected_updated_time": combined.json()["body"]["updated_time"],
            "disclosure": {"date_granularity": "MONTH", "amount_bands": {}},
        })
        assert changed_time.status_code == 200
        with sessions() as db:
            assert [rule.rule_revision for rule in db.scalars(select(AutoTagRule))] == [3, 3, 3]


def test_disclosure_invalidation_overflow_rolls_back_everything(setting_runtime):
    sessions, _, store = setting_runtime
    uri = "/paam/system/v1/setting/automation"
    with _client(sessions, store) as client:
        initial = client.put(uri, json={
            "expected_updated_time": None, "models": [_model_payload()],
        }).json()["body"]
        _seed_disclosure_rules(sessions)
        with sessions() as db:
            db.execute(update(AutoTagRule).where(AutoTagRule.id == 1).values(
                scan_epoch=9_223_372_036_854_775_807,
            ))
            db.commit()
        response = client.put(uri, json={
            "expected_updated_time": initial["updated_time"],
            "disclosure": {"date_granularity": "DAY", "amount_bands": {}},
        })
        assert response.status_code == 409
        assert client.get(uri).json()["body"] == initial
        with sessions() as db:
            rule = db.get(AutoTagRule, 1)
            assert (rule.rule_revision, rule.scan_after_ledger_id) == (1, 20)
            assert db.get(TagAssignmentRequest, 1).status == 1
