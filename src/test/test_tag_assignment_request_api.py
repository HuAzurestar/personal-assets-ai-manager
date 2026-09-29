from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from backend.core.job_scheduler import JobRunContext
from backend.core.target_database import init_target_db
from backend.entity import (
    AutoTagRule,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TagAssignmentRequest,
    TransactionFact,
)
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.mapper.target_tag_mapper import TargetTagMapper
from backend.mapper.target_tag_assignment_mapper import TargetTagAssignmentMapper
from backend.router.dependency import get_db
from backend.router.error import register_error_handlers
from backend.router.ledger import router as ledger_router
from backend.router.tag_assignment_request import router as request_router
from backend.router.tag_assignment import router as assignment_router
from backend.schema.auto_tag_scan import SyntheticTagScanFixture
from backend.schema.llm_analysis import (
    LlmAmountDisclosure,
    LlmAnalysisResult,
    LlmResolvedSuggestion,
)
from backend.service.auto_tag_scan_service import AutoTagScanService

NOW = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)


@pytest.fixture
def request_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tag-requests.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    init_target_db(bind=engine)
    with sessions() as db:
        tags = TargetTagMapper(db)
        tags.begin_write()
        category_id = tags.create_view("Category", "category", NOW)
        tags.create_tag(category_id, "Food", "food", NOW)
        tags.create_tag(category_id, "Travel", "travel", NOW)
        mood_id = tags.create_view("Mood", "mood", NOW)
        tags.create_tag(mood_id, "Happy", "happy", NOW)
        category = tags.view(category_id)
        mood = tags.view(mood_id)
        tags.commit()
        tag_ids = {
            "category": {item.system_name: item.id for item in category.tags},
            "mood": {item.system_name: item.id for item in mood.tags},
        }
        settings = SettingMapper(db)
        settings.begin_write()
        settings.save({
            "schema_version": 1,
            "automation": {
                "models": [{
                    "id": 1,
                    "name": "Synthetic",
                    "enabled": True,
                    "litellm_params": {
                        "model": "openai/synthetic",
                        "api_base": "https://example.test/v1",
                    },
                }],
                "disclosure": {},
            },
        }, NOW)
        settings.commit()

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(request_router)
    app.include_router(ledger_router)
    app.include_router(assignment_router)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, sessions, category_id, mood_id, tag_ids
    finally:
        engine.dispose()


def _rule(sessions, view_id: int, name: str) -> int:
    with sessions() as db:
        mapper = AutoTagRuleMapper(db)
        mapper.begin_write()
        rule_id = mapper.create(
            name=name,
            view_id=view_id,
            method_config={
                "schema_version": 1,
                "model_id": 1,
                "prompt": "Choose one synthetic tag",
            },
            enabled=1,
            cron="*/5 * * * *",
            now=NOW,
        )
        mapper.commit()
        return rule_id


def _ledger(sessions, category_tag: int, mood_tag: int) -> int:
    with sessions() as db:
        fact = TransactionFact(
            fact_key=f"request-fixture-{secrets.token_hex(8)}",
            occurred_time=NOW,
            cash_direction=2,
            amount=2_500,
            currency_code="CNY",
            account_code="fixture",
            counterparty_name="Synthetic merchant",
            counterparty_account_ref="synthetic",
            summary="Synthetic purchase",
            created_time=NOW,
            updated_time=NOW,
        )
        db.add(fact)
        db.flush()
        ledger = LedgerEntry(
            entry_type=0,
            entry_direction=2,
            amount=2_500,
            currency_code="CNY",
            account_code="fixture",
            counterparty_account_ref="synthetic",
            occurred_time=NOW,
            created_time=NOW,
            updated_time=NOW,
        )
        db.add(ledger)
        db.flush()
        review = ReviewCase(
            behavior_type=0,
            status=0,
            title="Synthetic review",
            created_time=NOW,
            updated_time=NOW,
        )
        db.add(review)
        db.flush()
        db.add(ReviewAllocation(
            review_case_id=review.id,
            transaction_fact_id=fact.id,
            ledger_entry_id=ledger.id,
            amount=2_500,
            currency_code="CNY",
            created_time=NOW,
            updated_time=NOW,
        ))
        db.add_all([
            LedgerEntryTag(
                ledger_id=ledger.id,
                tag_id=tag_id,
                created_time=NOW,
                updated_time=NOW,
            )
            for tag_id in (category_tag, mood_tag)
        ])
        db.commit()
        return ledger.id


class SuggestionAnalyzer:
    def __init__(self, tag_id: int, tag_name: str):
        self.tag_id = tag_id
        self.tag_name = tag_name

    async def analyze(self, payload, *, rule_id, model_id):
        del rule_id, model_id
        return LlmAnalysisResult(
            kind="SUGGESTED",
            item=payload.item,
            suggestions=[LlmResolvedSuggestion(
                tag_id=self.tag_id,
                tag_name=self.tag_name,
                reason="synthetic evidence",
            )],
        )


def _scan(sessions, rule_id: int, ledger_id: int, tag_id: int, name: str):
    fixture = SyntheticTagScanFixture(
        source="SYNTHETIC_FIXTURE",
        ledger_id=ledger_id,
        item=f"fixture-{ledger_id}",
        direction="OUT",
        merchant="Synthetic merchant",
        summary="Synthetic purchase",
        amount=LlmAmountDisclosure(
            mode="BAND",
            currency_code="CNY",
            band_code="0_30",
            band_label="0-30",
        ),
    )
    context = JobRunContext(
        task_key=f"tag-scan:{rule_id}",
        started_at=NOW,
        deadline_monotonic=10,
        page_limit=100,
        _monotonic=lambda: 0,
    )
    return asyncio.run(AutoTagScanService(
        sessions,
        SuggestionAnalyzer(tag_id, name),
    ).run_synthetic(rule_id, {ledger_id: fixture}, context))


def test_duplicate_request_filters_use_combination_error(request_api):
    client, *_ = request_api
    duplicate = client.get(
        "/paam/tag/v1/assignment_request/list",
        params={"filter": '{"op":"AND","expression":[{"key":"status","op":"=","val":1},{"key":"status","op":"=","val":2}]}'},
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["body"]["code"] == "LIST_COMBINATION_NOT_SUPPORTED"


def test_synthetic_request_requires_approval_and_reads_back_source(request_api):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions,
        tag_ids["category"]["unclassified"],
        tag_ids["mood"]["unclassified"],
    )
    food_rule = _rule(sessions, category_id, "Food rule")
    travel_rule = _rule(sessions, category_id, "Travel rule")
    _scan(sessions, food_rule, ledger_id, tag_ids["category"]["food"], "Food")
    _scan(
        sessions,
        travel_rule,
        ledger_id,
        tag_ids["category"]["travel"],
        "Travel",
    )

    pending = client.get(
        "/paam/tag/v1/assignment_request/list",
        params={
            "filter": '{"key":"status","op":"=","val":1}',
            "sorter": '[{"key":"created_time","direction":"asc"}]',
        },
    )
    assert pending.status_code == 200, pending.text
    items = pending.json()["body"]["items"]
    assert [item["rule_name"] for item in items] == ["Food rule", "Travel rule"]
    assert all(item["ledger_active"] for item in items)
    assert all(item["ledger_summary"] == "Synthetic purchase" for item in items)
    assert all(item["ledger_counterparty_name"] == "Synthetic merchant" for item in items)
    assert all(item["ledger_amount"] == 2_500 for item in items)
    assert all(item["ledger_currency_code"] == "CNY" for item in items)

    before = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    before_tags = {
        item["view_system_name"]: item for item in before["ledger_entry"]["tags"]
    }
    assert before_tags["category"]["tag_system_name"] == "unclassified"
    assert before_tags["category"]["source_type"] == "MANUAL"

    approved = client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": [items[0]["id"]]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["body"]["items"][0]["status"] == 2
    competing = client.get(
        f"/paam/tag/v1/assignment_request/{items[1]['id']}"
    ).json()["body"]
    assert competing["status"] == 4

    after = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    after_tags = {
        item["view_system_name"]: item for item in after["ledger_entry"]["tags"]
    }
    assert after_tags["category"] == {
        "view_name": "Category",
        "view_system_name": "category",
        "tag_name": "Food",
        "tag_system_name": "food",
        "source_type": "AUTO_RULE",
        "request_id": items[0]["id"],
        "rule_id": food_rule,
        "rule_revision": 1,
    }
    assert after_tags["mood"]["tag_system_name"] == "unclassified"
    with sessions() as db:
        assert db.get(AutoTagRule, food_rule).accepted_count == 1
        assert db.get(AutoTagRule, travel_rule).accepted_count == 0


def test_reject_keeps_tag_and_increments_rule_counter(request_api):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions,
        tag_ids["category"]["unclassified"],
        tag_ids["mood"]["unclassified"],
    )
    rule_id = _rule(sessions, category_id, "Reject rule")
    _scan(sessions, rule_id, ledger_id, tag_ids["category"]["food"], "Food")
    with sessions() as db:
        request_id = db.scalar(select(TagAssignmentRequest.id))

    rejected = client.post(
        "/paam/tag/v1/assignment_request/batch_reject",
        json={"request_ids": [request_id]},
    )

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["body"]["items"][0]["status"] == 3
    all_requests = client.get("/paam/tag/v1/assignment_request/list")
    pending_requests = client.get(
        "/paam/tag/v1/assignment_request/list",
        params={"filter": '{"key":"status","op":"=","val":1}'},
    )
    rejected_requests = client.get(
        "/paam/tag/v1/assignment_request/list",
        params={"filter": '{"key":"status","op":"=","val":3}'},
    )
    assert all_requests.json()["body"]["total"] == 1
    assert pending_requests.json()["body"]["total"] == 0
    assert rejected_requests.json()["body"]["items"][0]["id"] == request_id
    with sessions() as db:
        assert db.get(AutoTagRule, rule_id).rejected_count == 1
        current = db.scalar(select(LedgerEntryTag.tag_id).where(
            LedgerEntryTag.ledger_id == ledger_id,
            LedgerEntryTag.tag_id.in_(tag_ids["category"].values()),
        ))
        assert current == tag_ids["category"]["unclassified"]


def test_approval_conflicts_have_item_results_and_never_override_manual_tag(request_api):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions,
        tag_ids["category"]["unclassified"],
        tag_ids["mood"]["unclassified"],
    )
    first_rule = _rule(sessions, category_id, "First")
    second_rule = _rule(sessions, category_id, "Second")
    _scan(sessions, first_rule, ledger_id, tag_ids["category"]["food"], "Food")
    _scan(sessions, second_rule, ledger_id, tag_ids["category"]["travel"], "Travel")
    with sessions() as db:
        request_ids = list(db.scalars(select(
            TagAssignmentRequest.id,
        ).order_by(TagAssignmentRequest.id)).all())

    scope_conflict = client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": request_ids},
    )
    assert scope_conflict.status_code == 200
    assert [item["result"] for item in scope_conflict.json()["body"]["items"]] == ["SCOPE_CONFLICT", "SCOPE_CONFLICT"]
    with sessions() as db:
        assert set(db.scalars(select(TagAssignmentRequest.status)).all()) == {1}

        category_links = list(db.scalars(select(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id,
            LedgerEntryTag.tag_id.in_(tag_ids["category"].values()),
        )).all())
        for link in category_links:
            db.delete(link)
        db.add(LedgerEntryTag(
            ledger_id=ledger_id,
            tag_id=tag_ids["category"]["travel"],
            created_time=NOW,
            updated_time=NOW,
        ))
        db.commit()

    manual_conflict = client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": [request_ids[0]]},
    )
    assert manual_conflict.status_code == 200
    assert manual_conflict.json()["body"]["items"][0]["result"] == (
        "MANUAL_TAG_CONFLICT"
    )
    with sessions() as db:
        assert db.scalar(select(func.count(TagAssignmentRequest.id)).where(
            TagAssignmentRequest.status != 1,
        )) == 0
        assert db.get(AutoTagRule, first_rule).accepted_count == 0


def test_stale_rule_and_inactive_ledger_are_rejected(request_api):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions,
        tag_ids["category"]["unclassified"],
        tag_ids["mood"]["unclassified"],
    )
    rule_id = _rule(sessions, category_id, "Stale")
    _scan(sessions, rule_id, ledger_id, tag_ids["category"]["food"], "Food")
    with sessions() as db:
        request_id = db.scalar(select(TagAssignmentRequest.id))
        db.execute(update(AutoTagRule).where(
            AutoTagRule.id == rule_id,
        ).values(rule_revision=2))
        db.commit()

    stale = client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": [request_id]},
    )
    assert stale.status_code == 200
    assert stale.json()["body"]["items"][0]["result"] == "RULE_STALE"

    with sessions() as db:
        db.execute(update(AutoTagRule).where(
            AutoTagRule.id == rule_id,
        ).values(rule_revision=1))
        review_id = db.scalar(select(ReviewAllocation.review_case_id).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
        ))
        db.execute(update(ReviewCase).where(
            ReviewCase.id == review_id,
        ).values(status=1))
        db.commit()
    inactive = client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": [request_id]},
    )
    assert inactive.status_code == 200
    assert inactive.json()["body"]["items"][0]["result"] == "LEDGER_INACTIVE"


def _request_id(sessions, rule_id, ledger_id):
    with sessions() as db:
        return db.scalar(select(TagAssignmentRequest.id).where(
            TagAssignmentRequest.rule_id == rule_id,
            TagAssignmentRequest.ledger_id == ledger_id,
        ))


def _approve(client, request_id):
    response = client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": [request_id]},
    )
    assert response.status_code == 200, response.text


def _assignment(client, ledger_id):
    response = client.get(f"/paam/tag/v1/assignment/{ledger_id}")
    assert response.status_code == 200, response.text
    return response.json()["body"]


def _manual(client, ledger_id, state, token, *, view_names=None):
    return client.put(f"/paam/tag/v1/assignment/{ledger_id}", json={
        "expected_updated_time": token, "tag_state": state,
        "view_names": view_names,
    })


@pytest.mark.parametrize("approve_mood", [False, True])
def test_manual_reset_replaces_old_source_and_allows_new_approval(
    request_api, approve_mood,
):
    client, sessions, category_id, mood_id, tag_ids = request_api
    ledger_id = _ledger(
        sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["unclassified"],
    )
    food_rule = _rule(sessions, category_id, "Food")
    mood_rule = _rule(sessions, mood_id, "Mood")
    _scan(sessions, food_rule, ledger_id, tag_ids["category"]["food"], "Food")
    _scan(sessions, mood_rule, ledger_id, tag_ids["mood"]["happy"], "Happy")
    food_request = _request_id(sessions, food_rule, ledger_id)
    mood_request = _request_id(sessions, mood_rule, ledger_id)
    _approve(client, food_request)
    if approve_mood:
        _approve(client, mood_request)
    with sessions() as db:
        untouched = db.execute(select(
            LedgerEntryTag.id, LedgerEntryTag.updated_time,
        ).where(
            LedgerEntryTag.ledger_id == ledger_id,
            LedgerEntryTag.tag_id.in_(tag_ids["mood"].values()),
        )).one()
    opened = _assignment(client, ledger_id)
    response = _manual(client, ledger_id, {
        **opened["tag_state"], "category": "unclassified",
    }, opened["updated_time"])
    assert response.status_code == 200, response.text
    assert _assignment(client, ledger_id) == response.json()["body"]
    with sessions() as db:
        assert db.get(TagAssignmentRequest, food_request).status == 5
        assert db.get(TagAssignmentRequest, mood_request).status == (2 if approve_mood else 1)
        assert db.execute(select(
            LedgerEntryTag.id, LedgerEntryTag.updated_time,
        ).where(
            LedgerEntryTag.ledger_id == ledger_id,
            LedgerEntryTag.tag_id.in_(tag_ids["mood"].values()),
        )).one() == untouched
        food = db.get(AutoTagRule, food_rule)
        assert (food.accepted_count, food.rejected_count) == (1, 0)
    travel_rule = _rule(sessions, category_id, "Travel after manual reset")
    _scan(sessions, travel_rule, ledger_id, tag_ids["category"]["travel"], "Travel")
    _approve(client, _request_id(sessions, travel_rule, ledger_id))
    assert _assignment(client, ledger_id)["tag_state"]["category"] == "travel"


def test_same_value_manual_save_takes_ownership_and_is_then_idempotent(request_api):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["unclassified"],
    )
    rule_id = _rule(sessions, category_id, "Food")
    _scan(sessions, rule_id, ledger_id, tag_ids["category"]["food"], "Food")
    request_id = _request_id(sessions, rule_id, ledger_id)
    _approve(client, request_id)
    opened = _assignment(client, ledger_id)
    saved = _manual(client, ledger_id, opened["tag_state"], opened["updated_time"])
    assert saved.status_code == 200, saved.text
    current = saved.json()["body"]
    assert current["updated_time"] > opened["updated_time"]
    assert _assignment(client, ledger_id) == current
    replay = _manual(client, ledger_id, current["tag_state"], current["updated_time"])
    assert replay.status_code == 200
    assert replay.json()["body"] == current
    assert _manual(client, ledger_id, opened["tag_state"], opened["updated_time"]).status_code == 409
    tags = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]["ledger_entry"]["tags"]
    category = next(item for item in tags if item["view_system_name"] == "category")
    assert category["source_type"] == "MANUAL"
    assert category["request_id"] is None
    with sessions() as db:
        assert db.get(TagAssignmentRequest, request_id).status == 5
        assert db.get(AutoTagRule, rule_id).accepted_count == 1
        assert db.get(AutoTagRule, rule_id).rejected_count == 0


@pytest.mark.parametrize("category", ["food", "unclassified"])
def test_manual_assignment_cancels_pending_only_for_target_ledger(request_api, category):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["unclassified"],
    )
    other_id = _ledger(
        sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["happy"],
    )
    rule_id = _rule(sessions, category_id, "Pending food")
    _scan(sessions, rule_id, ledger_id, tag_ids["category"]["food"], "Food")
    _scan(sessions, rule_id, other_id, tag_ids["category"]["food"], "Food")
    opened = _assignment(client, ledger_id)
    saved = _manual(client, ledger_id, {
        **opened["tag_state"], "category": category,
    }, opened["updated_time"])
    assert saved.status_code == 200, saved.text
    request_id = _request_id(sessions, rule_id, ledger_id)
    other_request = _request_id(sessions, rule_id, other_id)
    assert other_request is not None
    assert client.post(
        "/paam/tag/v1/assignment_request/batch_approve",
        json={"request_ids": [request_id]},
    ).json()["body"]["items"][0]["result"] == "REQUEST_STATE_CONFLICT"
    with sessions() as db:
        assert db.get(TagAssignmentRequest, request_id).status == 4
        assert db.get(TagAssignmentRequest, other_request).status == 1
        rule = db.get(AutoTagRule, rule_id)
        assert (rule.accepted_count, rule.rejected_count) == (0, 0)


@pytest.mark.parametrize("category", ["food", "unclassified"])
def test_manual_request_state_and_tag_write_roll_back_together(
    request_api, monkeypatch, category,
):
    client, sessions, category_id, _, tag_ids = request_api
    ledger_id = _ledger(
        sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["unclassified"],
    )
    rule_id = _rule(sessions, category_id, "Pending food")
    _scan(sessions, rule_id, ledger_id, tag_ids["category"]["food"], "Food")
    request_id = _request_id(sessions, rule_id, ledger_id)
    opened = _assignment(client, ledger_id)
    replace = TargetTagAssignmentMapper.replace

    def fail_after_write(self, *args, **kwargs):
        replace(self, *args, **kwargs)
        self.db.flush()
        raise RuntimeError("synthetic write failure")

    monkeypatch.setattr(TargetTagAssignmentMapper, "replace", fail_after_write)
    failed = _manual(client, ledger_id, {
        **opened["tag_state"], "category": category,
    }, opened["updated_time"])
    assert failed.status_code == 500
    assert _assignment(client, ledger_id) == opened
    with sessions() as db:
        assert db.get(TagAssignmentRequest, request_id).status == 1
        assert db.get(AutoTagRule, rule_id).accepted_count == 0


def test_scoped_same_value_takeover_preserves_other_auto_view(request_api):
    client, sessions, category_id, mood_id, tag_ids = request_api
    ledger_id = _ledger(
        sessions, tag_ids["category"]["unclassified"], tag_ids["mood"]["unclassified"],
    )
    category_rule = _rule(sessions, category_id, "Food")
    mood_rule = _rule(sessions, mood_id, "Mood")
    _scan(sessions, category_rule, ledger_id, tag_ids["category"]["food"], "Food")
    _scan(sessions, mood_rule, ledger_id, tag_ids["mood"]["happy"], "Happy")
    category_request = _request_id(sessions, category_rule, ledger_id)
    mood_request = _request_id(sessions, mood_rule, ledger_id)
    _approve(client, category_request)
    _approve(client, mood_request)
    opened = _assignment(client, ledger_id)
    noop = _manual(
        client, ledger_id, opened["tag_state"], opened["updated_time"], view_names=[],
    )
    assert noop.json()["body"] == opened
    for view_names in (["missing"], ["category", "category"], []):
        invalid = _manual(client, ledger_id, {
            **opened["tag_state"], "category": "unclassified",
        }, opened["updated_time"], view_names=view_names)
        assert invalid.status_code == 422, invalid.text
    saved = _manual(
        client, ledger_id, opened["tag_state"], opened["updated_time"],
        view_names=["category"],
    )
    assert saved.status_code == 200, saved.text
    current = saved.json()["body"]
    assert current["updated_time"] > opened["updated_time"]
    replay = _manual(
        client, ledger_id, current["tag_state"], current["updated_time"],
        view_names=["category"],
    )
    assert replay.json()["body"] == current
    with sessions() as db:
        assert db.get(TagAssignmentRequest, category_request).status == 5
        assert db.get(TagAssignmentRequest, mood_request).status == 2
