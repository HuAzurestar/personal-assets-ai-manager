from __future__ import annotations

import json
import socket
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    AutoTagRule,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TAG_REQUEST_STATUS_CANCELLED,
    TAG_REQUEST_STATUS_PENDING,
    TagAssignmentRequest,
    TransactionFact,
)
from backend.mapper.setting_mapper import SettingMapper
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.mapper.target_tag_mapper import TargetTagMapper
from backend.router.auto_tag_rule import router as auto_tag_rule_router
from backend.router.dependency import get_db
from backend.router.error import register_error_handlers
from backend.router.tag import router as tag_router


@pytest.fixture
def auto_rule_runtime(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auto-rule.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    init_target_db(bind=engine)
    now = datetime(2026, 9, 22, 6, tzinfo=timezone.utc)
    with sessions() as db:
        tag_mapper = TargetTagMapper(db)
        tag_mapper.begin_write()
        view_id = tag_mapper.create_view("Category", "category", now)
        tag_mapper.create_tag(view_id, "Food", "food", now)
        tag_mapper.commit()

        setting_mapper = SettingMapper(db)
        setting_mapper.begin_write()
        setting_mapper.save(
            {
                "schema_version": 1,
                "automation": {
                    "models": [
                        {
                            "id": 1,
                            "name": "Enabled model",
                            "enabled": True,
                            "litellm_params": {
                                "model": "openai/Qwen/Qwen3-8B",
                                "api_base": "https://api.example.test/v1",
                            },
                        },
                        {
                            "id": 2,
                            "name": "Disabled model",
                            "enabled": False,
                            "litellm_params": {
                                "model": "openai/Qwen/Qwen3-8B",
                                "api_base": "https://api.example.test/v1",
                            },
                        },
                    ],
                    "disclosure": {},
                },
            },
            now,
        )
        setting_mapper.commit()
    try:
        yield sessions, engine, view_id
    finally:
        engine.dispose()


def _client(sessions) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auto_tag_rule_router)
    app.include_router(tag_router)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


@pytest.mark.parametrize("counts, expected", [
    ((0, 0, 0, 0, 0), ("0", None, "0", None)),
    ((20, 2, 24, 6, 2), ("18", 0.9, "8", 0.75)),
    (
        (9_223_372_036_854_775_807, 1, 9_223_372_036_854_775_807,
         9_223_372_036_854_775_806, 1),
        ("9223372036854775806", 1.0, "9223372036854775807", 1.0),
    ),
])
def test_rule_summary_exact_counter_strings_and_rate_denominators(
    auto_rule_runtime, counts, expected,
):
    sessions, _, view_id = auto_rule_runtime
    with _client(sessions) as client:
        created = client.post("/paam/tag/v1/auto_rule", json=_rule_payload(view_id))
        assert created.status_code == 200
        rule_id = created.json()["body"]["id"]
        fields = (
            "analyzed_count", "failed_count", "suggested_count",
            "accepted_count", "rejected_count",
        )
        with sessions() as db:
            db.execute(update(AutoTagRule).where(AutoTagRule.id == rule_id).values(
                **dict(zip(fields, counts)),
            ))
            db.commit()
        response = client.get(f"/paam/tag/v1/auto_rule/{rule_id}/summary")
        assert response.status_code == 200
        body = response.json()["body"]
        assert [body[field] for field in fields] == [str(value) for value in counts]
        assert tuple(body[field] for field in (
            "execution_success_count", "execution_success_rate", "decision_count",
            "acceptance_rate",
        )) == expected
        assert "producing_analysis_rate" not in body
        assert "pending_count" not in body
    with _client(sessions) as client:
        assert client.get(f"/paam/tag/v1/auto_rule/{rule_id}/summary").json()["body"] == body


def _rule_payload(
    view_id: int,
    *,
    name: str = "Merchant rule",
    model_id: int = 1,
    prompt: str = "Classify by merchant",
    enabled: bool = True,
    cron: str = "*/5 * * * mon-fri",
    amount_mode: int = 1,
):
    return {
        "name": name,
        "view_id": view_id,
        "method": 1,
        "method_config": {
            "schema_version": 1,
            "model_id": model_id,
            "prompt": prompt,
        },
        "enabled": enabled,
        "cron": cron,
        "amount_mode": amount_mode,
    }


def _update_payload(rule: dict, **changes):
    payload = {
        "expected_updated_time": rule["updated_time"],
        "name": rule["name"],
        "method": rule["method"],
        "method_config": rule["method_config"],
        "enabled": rule["enabled"],
        "cron": rule["cron"],
        "amount_mode": rule["amount_mode"],
    }
    payload.update(changes)
    return payload


def test_rule_crud_list_summary_and_restart(auto_rule_runtime):
    sessions, _, view_id = auto_rule_runtime
    with _client(sessions) as client:
        created = client.post(
            "/paam/tag/v1/auto_rule",
            json=_rule_payload(view_id, cron="  */5   * * * mon-fri  "),
        )
        assert created.status_code == 200, created.text
        first = created.json()["body"]
        assert first["cron"] == "*/5 * * * mon-fri"
        assert first["rule_revision"] == 1
        assert first["scan_after_ledger_id"] == 0
        assert first["scan_epoch"] == 1
        assert first["analyzed_count"] == "0"

        second = client.post(
            "/paam/tag/v1/auto_rule",
            json=_rule_payload(view_id, name="Second rule", cron="0 */10 * * * *"),
        )
        assert second.status_code == 200, second.text
        assert second.json()["body"]["view_id"] == view_id

        listed = client.get(
            "/paam/tag/v1/auto_rule/list",
            params={
                "query": json.dumps([{"key": "name", "word": "Merchant"}]),
                "filter": json.dumps({"key": "enabled", "op": "=", "val": True}),
                "sorter": json.dumps([{"key": "id", "direction": "asc"}]),
            },
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["body"]["total"] == 1
        assert listed.json()["body"]["items"][0]["id"] == first["id"]

        detail = client.get(f"/paam/tag/v1/auto_rule/{first['id']}")
        assert detail.status_code == 200
        summary = client.get(f"/paam/tag/v1/auto_rule/{first['id']}/summary")
        assert summary.status_code == 200
        assert summary.json()["body"]["suggested_count"] == "0"

        paths = client.get("/openapi.json").json()["paths"]
        assert not any("/run" in path or "/rescan" in path for path in paths)

    with _client(sessions) as restarted_client:
        recovered = restarted_client.get(f"/paam/tag/v1/auto_rule/{first['id']}")
    assert recovered.status_code == 200
    assert recovered.json()["body"]["method_config"]["prompt"] == (
        "Classify by merchant"
    )


def test_semantic_update_resets_checkpoint_and_cancels_old_pending(
    auto_rule_runtime,
):
    sessions, _, view_id = auto_rule_runtime
    with _client(sessions) as client:
        created = client.post(
            "/paam/tag/v1/auto_rule",
            json=_rule_payload(view_id),
        ).json()["body"]
        rule_id = created["id"]

        with sessions() as db:
            db.execute(
                update(AutoTagRule)
                .where(AutoTagRule.id == rule_id)
                .values(scan_after_ledger_id=42)
            )
            request_mapper = TagAssignmentRequestMapper(db)
            request_mapper.create_many([{
                "rule_id": rule_id,
                "rule_revision": 1,
                "ledger_id": 77,
                "view_id": view_id,
                "proposed_tag_id": 2,
                "reason_summary": "synthetic",
            }], datetime(2026, 9, 22, 6, 30, tzinfo=timezone.utc))
            db.commit()

        renamed = client.put(
            f"/paam/tag/v1/auto_rule/{rule_id}",
            json=_update_payload(
                created,
                name="Renamed",
                cron="*/10 * * * mon-fri",
                enabled=False,
            ),
        )
        assert renamed.status_code == 200, renamed.text
        ordinary = renamed.json()["body"]
        assert ordinary["rule_revision"] == 1
        assert ordinary["scan_epoch"] == 1
        assert ordinary["scan_after_ledger_id"] == 42

        same = client.put(
            f"/paam/tag/v1/auto_rule/{rule_id}",
            json=_update_payload(ordinary),
        )
        assert same.status_code == 200
        assert same.json()["body"]["updated_time"] == ordinary["updated_time"]

        next_config = dict(ordinary["method_config"])
        next_config["prompt"] = "Use merchant and summary"
        semantic = client.put(
            f"/paam/tag/v1/auto_rule/{rule_id}",
            json=_update_payload(ordinary, method_config=next_config),
        )
        assert semantic.status_code == 200, semantic.text
        changed = semantic.json()["body"]
        assert changed["rule_revision"] == 2
        assert changed["scan_epoch"] == 2
        assert changed["scan_after_ledger_id"] == 0
        with sessions() as db:
            request_status = db.scalar(select(TagAssignmentRequest.status))
        assert request_status == TAG_REQUEST_STATUS_CANCELLED

        stale = client.put(
            f"/paam/tag/v1/auto_rule/{rule_id}",
            json=_update_payload(ordinary, name="Stale"),
        )
        assert stale.status_code == 409
        assert stale.json()["body"]["code"] == "AUTO_TAG_RULE_VERSION_CONFLICT"


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"cron": "* * * * 1"}, "VALIDATION_ERROR"),
        ({"cron": "* * * * * * *"}, "VALIDATION_ERROR"),
        ({"cron": ""}, "VALIDATION_ERROR"),
        ({"model_id": 99}, "AUTO_TAG_RULE_MODEL_INVALID"),
        ({"model_id": 2}, "AUTO_TAG_RULE_MODEL_DISABLED"),
    ],
)
def test_invalid_rule_configuration_is_rejected(
    auto_rule_runtime,
    changes,
    expected_code,
):
    sessions, _, view_id = auto_rule_runtime
    payload = _rule_payload(
        view_id,
        cron=changes.get("cron", "*/5 * * * mon-fri"),
        model_id=changes.get("model_id", 1),
    )
    with _client(sessions) as client:
        response = client.post("/paam/tag/v1/auto_rule", json=payload)
    assert response.status_code == 422
    assert response.json()["body"]["code"] == expected_code


def test_disabled_rule_may_be_prepared_without_cron(auto_rule_runtime):
    sessions, _, view_id = auto_rule_runtime
    with _client(sessions) as client:
        response = client.post(
            "/paam/tag/v1/auto_rule",
            json=_rule_payload(view_id, enabled=False, cron="", model_id=2),
        )
    assert response.status_code == 200, response.text
    assert response.json()["body"]["enabled"] is False


def test_effective_tag_dictionary_change_invalidates_related_rules(
    auto_rule_runtime,
):
    sessions, _, view_id = auto_rule_runtime
    with _client(sessions) as client:
        rule = client.post(
            "/paam/tag/v1/auto_rule",
            json=_rule_payload(view_id),
        ).json()["body"]
        with sessions() as db:
            db.execute(
                update(AutoTagRule)
                .where(AutoTagRule.id == rule["id"])
                .values(scan_after_ledger_id=25)
            )
            TagAssignmentRequestMapper(db).create_many([{
                "rule_id": rule["id"],
                "rule_revision": 1,
                "ledger_id": 99,
                "view_id": view_id,
                "proposed_tag_id": 2,
                "reason_summary": "synthetic",
            }], datetime(2026, 9, 22, 6, 45, tzinfo=timezone.utc))
            db.commit()

        same_status = client.put(
            f"/paam/tag/v1/view/{view_id}",
            json={"status": "ACTIVE"},
        )
        assert same_status.status_code == 200
        unchanged = client.get(
            f"/paam/tag/v1/auto_rule/{rule['id']}"
        ).json()["body"]
        assert unchanged["rule_revision"] == 1
        assert unchanged["scan_after_ledger_id"] == 25

        added = client.post(
            f"/paam/tag/v1/view/{view_id}/tag",
            json={"name": "Travel", "system_name": "travel"},
        )
        assert added.status_code == 200, added.text
        invalidated = client.get(
            f"/paam/tag/v1/auto_rule/{rule['id']}"
        ).json()["body"]
        assert invalidated["rule_revision"] == 2
        assert invalidated["scan_epoch"] == 2
        assert invalidated["scan_after_ledger_id"] == 0
        with sessions() as db:
            status = db.scalar(select(TagAssignmentRequest.status))
        assert status == TAG_REQUEST_STATUS_CANCELLED


@pytest.mark.parametrize("ledger_count", [1, 25, 105])
def test_candidate_preview_business_samples_are_bounded_and_batched(auto_rule_runtime, ledger_count):
    sessions, engine, view_id = auto_rule_runtime
    now = datetime(2026, 9, 22, 7, tzinfo=timezone.utc)
    with _client(sessions) as client:
        rule = client.post("/paam/tag/v1/auto_rule", json=_rule_payload(view_id)).json()["body"]
        with sessions() as db:
            unclassified = next(tag.id for tag in TargetTagMapper(db).view(view_id).tags if tag.system_name == "unclassified")
            facts = [TransactionFact(
                fact_key=f"preview-only-{index}", occurred_time=now, cash_direction=2,
                amount=2500 + index, currency_code="CNY", account_code="private-account-marker",
                counterparty_account_ref="private-reference-marker", counterparty_name=f"合成商户 {index}",
                summary=f"合成午餐 {index}", created_time=now, updated_time=now,
            ) for index in range(ledger_count)]
            ledgers = [LedgerEntry(
                entry_type=0, entry_direction=2, amount=fact.amount, currency_code="CNY",
                account_code="private-account-marker", counterparty_account_ref="private-reference-marker",
                occurred_time=now, created_time=now, updated_time=now,
            ) for fact in facts]
            review = ReviewCase(behavior_type=0, status=0, title="synthetic preview", created_time=now, updated_time=now)
            db.add_all([review, *facts, *ledgers])
            db.flush()
            db.add_all([ReviewAllocation(
                review_case_id=review.id, transaction_fact_id=fact.id, ledger_entry_id=ledger.id,
                amount=ledger.amount, currency_code="CNY", created_time=now, updated_time=now,
            ) for fact, ledger in zip(facts, ledgers)])
            db.add_all([LedgerEntryTag(ledger_id=ledger.id, tag_id=unclassified, created_time=now, updated_time=now) for ledger in ledgers])
            cursor = ledgers[1].id if ledger_count == 105 else 0
            db.execute(update(AutoTagRule).where(AutoTagRule.id == rule["id"]).values(scan_after_ledger_id=cursor))
            db.commit()

        selects = []

        def count_selects(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            response = client.post(f"/paam/tag/v1/auto_rule/{rule['id']}/candidate_preview")
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        assert response.status_code == 200, response.text
        body = response.json()["body"]
        assert body["scan_after_ledger_id"] == cursor
        assert body["inspected_count"] == min(100, ledger_count)
        assert body["eligible_count"] == min(100, ledger_count)
        assert len(body["samples"]) == min(20, ledger_count)
        assert body["samples"][0]["amount"] == 2500 + (2 if cursor else 0)
        assert body["samples"][0]["counterparty_name"] == f"合成商户 {2 if cursor else 0}"
        assert body["samples"][0]["summary"] == f"合成午餐 {2 if cursor else 0}"
        assert "private-account-marker" not in response.text
        assert "private-reference-marker" not in response.text
        assert "preview-only-" not in response.text
        # One bounded summary query regardless of candidate count; no per-row reads.
        assert len([sql for sql in selects if "transaction_fact.summary" in sql]) == 1
        assert len(selects) <= 12
        with sessions() as db:
            assert db.scalar(select(AutoTagRule.scan_after_ledger_id).where(AutoTagRule.id == rule["id"])) == cursor
            assert db.scalar(select(TagAssignmentRequest.id)) is None


def test_candidate_preview_is_read_only_and_explicitly_simulated(
    auto_rule_runtime,
    monkeypatch,
):
    sessions, _, view_id = auto_rule_runtime
    now = datetime(2026, 9, 22, 7, tzinfo=timezone.utc)
    with _client(sessions) as client:
        rule = client.post(
            "/paam/tag/v1/auto_rule",
            json=_rule_payload(view_id),
        ).json()["body"]

        with sessions() as db:
            tags = TargetTagMapper(db).view(view_id).tags
            unclassified_id = next(
                tag.id for tag in tags if tag.system_name == "unclassified"
            )
            food_id = next(tag.id for tag in tags if tag.system_name == "food")
            ledgers = [
                LedgerEntry(
                    entry_type=0,
                    entry_direction=1,
                    amount=100 + index,
                    currency_code="CNY",
                    account_code="wallet",
                    counterparty_account_ref="",
                    occurred_time=now,
                    created_time=now,
                    updated_time=now,
                )
                for index in range(5)
            ]
            db.add_all(ledgers)
            db.flush()
            reviews = [
                ReviewCase(
                    behavior_type=0,
                    status=1 if index == 2 else 0,
                    title="synthetic",
                    created_time=now,
                    updated_time=now,
                )
                for index in range(5)
            ]
            db.add_all(reviews)
            db.flush()
            db.add_all([
                ReviewAllocation(
                    review_case_id=review.id,
                    transaction_fact_id=1000 + index,
                    ledger_entry_id=ledger.id,
                    amount=ledger.amount,
                    currency_code="CNY",
                    created_time=now,
                    updated_time=now,
                )
                for index, (review, ledger) in enumerate(zip(reviews, ledgers))
            ])
            db.add_all([
                LedgerEntryTag(
                    ledger_id=ledgers[0].id,
                    tag_id=unclassified_id,
                    created_time=now,
                    updated_time=now,
                ),
                LedgerEntryTag(
                    ledger_id=ledgers[1].id,
                    tag_id=food_id,
                    created_time=now,
                    updated_time=now,
                ),
                LedgerEntryTag(
                    ledger_id=ledgers[2].id,
                    tag_id=unclassified_id,
                    created_time=now,
                    updated_time=now,
                ),
                LedgerEntryTag(
                    ledger_id=ledgers[4].id,
                    tag_id=unclassified_id,
                    created_time=now,
                    updated_time=now,
                ),
            ])
            db.flush()
            TagAssignmentRequestMapper(db).create_many([{
                "rule_id": rule["id"],
                "rule_revision": rule["rule_revision"],
                "ledger_id": ledgers[0].id,
                "view_id": view_id,
                "proposed_tag_id": food_id,
                "reason_summary": "existing",
            }], now)
            eligible_id = ledgers[4].id
            db.commit()

        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda *args, **kwargs: pytest.fail("candidate preview attempted network I/O"),
        )
        preview = client.post(
            f"/paam/tag/v1/auto_rule/{rule['id']}/candidate_preview"
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()["body"]
        assert body["mode"] == "SIMULATED_LOCAL"
        assert body["inspected_count"] == 5
        assert body["eligible_count"] == 1
        assert body["reason_counts"] == {
            "EXISTING_REQUEST": 1,
            "ALREADY_CLASSIFIED": 1,
            "INACTIVE_LEDGER": 1,
            "MISSING_VIEW_TAG": 1,
            "ELIGIBLE": 1,
        }
        assert len(body["samples"]) == 1
        sample = body["samples"][0]
        assert sample == {
            "ledger_id": eligible_id, "reason": "ELIGIBLE",
            "amount": 104, "currency_code": "CNY",
            "occurred_time": sample["occurred_time"],
            "counterparty_name": None, "summary": None,
        }
        assert datetime.fromisoformat(sample["occurred_time"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc) == now

        unchanged = client.get(
            f"/paam/tag/v1/auto_rule/{rule['id']}"
        ).json()["body"]
        assert unchanged["scan_after_ledger_id"] == 0
        assert unchanged["analyzed_count"] == "0"
        assert unchanged["suggested_count"] == "0"
        with sessions() as db:
            assert len(list(db.scalars(select(LedgerEntryTag)).all())) == 4
            assert db.scalar(select(TagAssignmentRequest.status)) == (
                TAG_REQUEST_STATUS_PENDING
            )
