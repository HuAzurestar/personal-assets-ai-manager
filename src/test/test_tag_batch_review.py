"""M2-CORE independent scopes, exact counters and serialized race protection."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from backend.entity import AutoTagRule, LedgerEntryTag, ReviewAllocation, ReviewCase, TagAssignmentRequest
from backend.entity.auto_tag_rule import MAX_COUNTER_VALUE
from backend.error import TargetTagError
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.schema.target_tag import TargetTagAssignmentRequest, TargetTagStatusRequest
from backend.service.tag_assignment_request_service import TagAssignmentRequestService
from backend.service.target_tag_assignment_service import TargetTagAssignmentService
from backend.service.target_tag_service import TargetTagService
from test_tag_assignment_request_api import (
    NOW,
    _assignment,
    _ledger,
    _manual,
    _rule,
    request_api as request_api,
)


def _seed(runtime, *, count=1):
    _, sessions, category_id, _, tags = runtime
    rule_id = _rule(sessions, category_id, "Batch fixture")
    ledgers = [_ledger(sessions, tags["category"]["unclassified"], tags["mood"]["happy"]) for _ in range(count)]
    with sessions() as db:
        mapper = TagAssignmentRequestMapper(db)
        mapper.begin_write()
        ids = mapper.create_many([{
            "rule_id": rule_id, "rule_revision": 1, "ledger_id": ledger_id,
            "view_id": category_id, "proposed_tag_id": tags["category"]["food"],
            "reason_summary": "餐饮语义",
        } for ledger_id in ledgers], NOW)
        db.execute(update(AutoTagRule).where(AutoTagRule.id == rule_id).values(
            analyzed_count=count, suggested_count=count,
        ))
        mapper.commit()
    return rule_id, ledgers, ids


def _batch(client, ids, operation="approve"):
    response = client.post(f"/paam/tag/v1/assignment_request/batch_{operation}", json={"request_ids": ids})
    body = response.json()["body"]
    if response.status_code == 409:
        assert body["code"] == "SUGGESTION_STALE", response.text
        assert all(row["code"] == "SUGGESTION_STALE" for row in body["details"]["results"])
        return body["details"]["results"]
    assert response.status_code == 200, response.text
    assert set(body) == {"results", "accepted", "rejected", "conflicts"}
    return body["results"]


def test_partial_results_keep_order_and_repeated_decisions_do_not_count(request_api):
    client, sessions, _, _, _ = request_api
    rule, ledgers, ids = _seed(request_api, count=2)
    first = _batch(client, [9999, ids[0]])
    assert [item["code"] for item in first] == ["NOT_FOUND", "APPROVED"]
    again = _batch(client, [ids[0], ids[1]])
    assert [item["code"] for item in again] == ["ALREADY_APPROVED", "APPROVED"]
    assert _batch(client, [ids[0]], "reject")[0]["code"] == "SUGGESTION_STALE"
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert (row.analyzed_count, row.failed_count, row.suggested_count, row.accepted_count, row.rejected_count) == (2, 0, 2, 2, 0)
    assert all(_assignment(client, ledger)["tag_state"]["mood"] == "happy" for ledger in ledgers)


def test_canonical_batch_counts_are_this_decision_not_durable_counter_replay(request_api):
    client, sessions, _, _, _ = request_api
    schema = client.app.openapi()["components"]["schemas"]
    assert set(schema["TagAssignmentBatchRead"]["properties"]) == {"results", "accepted", "rejected", "conflicts"}
    assert set(schema["TagAssignmentItemResult"]["properties"]) == {"id", "status", "code"}
    rule, _, ids = _seed(request_api, count=3)
    first = client.post("/paam/tag/v1/assignment_request/batch_approve", json=dict(request_ids=[ids[1], 9999, ids[0]]))
    assert first.status_code == 200
    assert first.json()["body"] == dict(results=[
        dict(id=ids[1], status=2, code="APPROVED"), dict(id=9999, status=None, code="NOT_FOUND"),
        dict(id=ids[0], status=2, code="APPROVED")], accepted=2, rejected=0, conflicts=1)
    again = client.post("/paam/tag/v1/assignment_request/batch_approve", json=dict(request_ids=ids[:2])).json()["body"]
    assert (again["accepted"], again["rejected"], again["conflicts"]) == (0, 0, 0)
    assert all(row["code"] == "ALREADY_APPROVED" for row in again["results"])
    _batch(client, ids[2:], "reject")
    terminal = client.post("/paam/tag/v1/assignment_request/batch_reject", json=dict(request_ids=ids[2:]))
    assert terminal.status_code == 409
    assert terminal.json()["body"] == dict(code="SUGGESTION_STALE", details=dict(
        results=[dict(id=ids[2], status=3, code="SUGGESTION_STALE")], accepted=0, rejected=0, conflicts=1))
    with sessions() as db:
        current = db.get(AutoTagRule, rule)
        assert (current.accepted_count, current.rejected_count) == (2, 1)


def test_mixed_stale_and_legal_batch_commits_only_legal_scope(request_api):
    client, sessions, _, _, _ = request_api
    rule, _, ids = _seed(request_api, count=2)
    _batch(client, ids[:1], "reject")
    response = client.post("/paam/tag/v1/assignment_request/batch_approve", json=dict(request_ids=ids))
    assert response.status_code == 200, response.text
    assert response.json()["body"] == dict(results=[dict(id=ids[0], status=3, code="SUGGESTION_STALE"),
        dict(id=ids[1], status=2, code="APPROVED")], accepted=1, rejected=0, conflicts=1)
    with sessions() as db:
        current = db.get(AutoTagRule, rule)
        assert (current.accepted_count, current.rejected_count) == (1, 1)


def test_reject_same_scope_conflicts_do_not_block_other_scopes(request_api):
    client, sessions, view_id, _, tags = request_api
    rule, ledgers, ids = _seed(request_api, count=2)
    rival_rule = _rule(sessions, view_id, "Fictional reject rival")
    with sessions() as db:
        mapper = TagAssignmentRequestMapper(db)
        mapper.begin_write()
        rival = mapper.create_many([dict(rule_id=rival_rule, rule_revision=1, ledger_id=ledgers[0],
            view_id=view_id, proposed_tag_id=tags["category"]["travel"])], NOW)[0]
        mapper.commit()
    response = client.post("/paam/tag/v1/assignment_request/batch_reject", json=dict(request_ids=[ids[0], ids[1], rival]))
    assert response.status_code == 200
    body = response.json()["body"]
    assert [row["code"] for row in body["results"]] == ["SCOPE_CONFLICT", "REJECTED", "SCOPE_CONFLICT"]
    assert (body["accepted"], body["rejected"], body["conflicts"]) == (0, 1, 2)
    with sessions() as db:
        assert db.get(TagAssignmentRequest, ids[0]).status == db.get(TagAssignmentRequest, rival).status == 1
        assert db.get(AutoTagRule, rule).rejected_count == 1


def test_scope_conflicts_do_not_block_other_scopes(request_api):
    client, sessions, view_id, _, tags = request_api
    rule, ledgers, ids = _seed(request_api, count=2)
    second_rule = _rule(sessions, view_id, "Competing")
    with sessions() as db:
        mapper = TagAssignmentRequestMapper(db)
        mapper.begin_write()
        rival = mapper.create_many([{
            "rule_id": second_rule, "rule_revision": 1, "ledger_id": ledgers[0],
            "view_id": view_id, "proposed_tag_id": tags["category"]["travel"],
        }], NOW)[0]
        mapper.commit()
    results = _batch(client, [ids[0], ids[1], rival])
    assert [row["code"] for row in results] == ["SCOPE_CONFLICT", "APPROVED", "SCOPE_CONFLICT"]
    with sessions() as db:
        assert db.get(AutoTagRule, rule).accepted_count == 1
        assert db.get(TagAssignmentRequest, ids[0]).status == 1
        assert db.get(TagAssignmentRequest, rival).status == 1


@pytest.mark.parametrize("operations", [("approve", "approve"), ("reject", "reject"), ("approve", "reject")])
def test_concurrent_decisions_have_only_one_first_transition(request_api, operations):
    _, sessions, _, _, _ = request_api
    rule, _, ids = _seed(request_api)
    ready = Barrier(2)

    def decide(operation):
        ready.wait(timeout=5)
        with sessions() as db:
            try:
                return getattr(TagAssignmentRequestService(db), operation)(ids).results[0].code
            except TargetTagError as error:
                assert error.code == "SUGGESTION_STALE"
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(decide, operations))
    assert sum(item in {"APPROVED", "REJECTED"} for item in result) == 1
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert row.accepted_count + row.rejected_count == 1


def test_manual_same_value_racing_approval_never_loses_source_protection(request_api):
    client, sessions, _, _, _ = request_api
    rule, ledgers, ids = _seed(request_api)
    opened = _assignment(client, ledgers[0])
    manual = TargetTagAssignmentRequest.model_validate({
        "tag_state": opened["tag_state"], "expected_updated_time": opened["updated_time"], "view_names": ["category"],
    })
    ready = Barrier(2)

    def decide(kind):
        ready.wait(timeout=5)
        with sessions() as db:
            if kind == "approve":
                try:
                    return TagAssignmentRequestService(db).approve(ids).results[0].code
                except TargetTagError as error:
                    assert error.code == "SUGGESTION_STALE"
                    return error.code
            try:
                TargetTagAssignmentService(db).assign(ledgers[0], manual)
                return "MANUAL"
            except TargetTagError as error:
                assert error.status_code == 409
                return "STALE"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(decide, ["approve", "manual"]))
    assert result in (["APPROVED", "STALE"], ["SUGGESTION_STALE", "MANUAL"])
    current = _assignment(client, ledgers[0])
    assert current["tag_state"]["category"] == ("food" if result[0] == "APPROVED" else "unclassified")
    assert current["tag_state"]["mood"] == "happy"
    with sessions() as db:
        assert db.get(AutoTagRule, rule).accepted_count == int(result[0] == "APPROVED")


def test_database_failure_rolls_back_tags_requests_and_counters(request_api, monkeypatch):
    client, sessions, _, _, _ = request_api
    rule, ledgers, ids = _seed(request_api, count=2)
    original = TagAssignmentRequestMapper.approve_rows

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.db.flush()
        raise OperationalError("fixture", {}, RuntimeError("storage unavailable"))

    monkeypatch.setattr(TagAssignmentRequestMapper, "approve_rows", fail)
    response = client.post("/paam/tag/v1/assignment_request/batch_approve", json={"request_ids": ids})
    assert response.status_code == 409
    assert response.json()["body"]["code"] == "TAG_REQUEST_WRITE_CONFLICT"
    assert "storage unavailable" not in response.text
    with sessions() as db:
        assert db.get(AutoTagRule, rule).accepted_count == 0
        assert set(db.scalars(select(TagAssignmentRequest.status))) == {1}
    assert all(_assignment(client, ledger)["tag_state"]["category"] == "unclassified" for ledger in ledgers)


def test_database_failure_preserves_internal_cause(request_api, monkeypatch):
    _, sessions, _, _, _ = request_api
    failure = OperationalError("fixture", {}, RuntimeError("storage unavailable"))

    def fail(_self):
        raise failure

    monkeypatch.setattr(TagAssignmentRequestMapper, "begin_write", fail)
    with sessions() as db, pytest.raises(TargetTagError) as caught:
        TagAssignmentRequestService(db).approve([1])

    assert caught.value.code == "TAG_REQUEST_WRITE_CONFLICT"
    assert caught.value.__cause__ is failure


def test_counter_overflow_rejects_only_affected_rule(request_api):
    client, sessions, _, _, _ = request_api
    exhausted, _, first = _seed(request_api)
    normal, _, second = _seed(request_api)
    with sessions() as db:
        db.execute(update(AutoTagRule).where(AutoTagRule.id == exhausted).values(
            accepted_count=MAX_COUNTER_VALUE, suggested_count=MAX_COUNTER_VALUE,
        ))
        db.commit()
    assert [item["code"] for item in _batch(client, first + second)] == ["COUNTER_EXHAUSTED", "APPROVED"]
    with sessions() as db:
        assert db.get(AutoTagRule, exhausted).accepted_count == MAX_COUNTER_VALUE
        assert db.get(AutoTagRule, normal).accepted_count == 1


def test_approval_advances_past_latest_other_view_token_and_preserves_that_view(request_api):
    client, sessions, _, _, tags = request_api
    _, ledgers, ids = _seed(request_api)
    future = NOW + timedelta(days=100)
    with sessions() as db:
        db.execute(update(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledgers[0], LedgerEntryTag.tag_id == tags["mood"]["happy"],
        ).values(updated_time=future))
        db.commit()
    opened = _assignment(client, ledgers[0])
    _batch(client, ids)
    current = _assignment(client, ledgers[0])
    assert current["updated_time"] > opened["updated_time"]
    assert _manual(client, ledgers[0], opened["tag_state"], opened["updated_time"]).status_code == 409
    taken = _manual(client, ledgers[0], current["tag_state"], current["updated_time"], view_names=["category"])
    assert taken.status_code == 200
    value = taken.json()["body"]
    assert _manual(client, ledgers[0], value["tag_state"], value["updated_time"], view_names=["category"]).json()["body"] == value
    with sessions() as db:
        assert db.scalar(select(LedgerEntryTag.updated_time).where(
            LedgerEntryTag.ledger_id == ledgers[0], LedgerEntryTag.tag_id == tags["mood"]["happy"],
        )) == future


def test_new_request_cannot_override_an_already_effective_nondefault_value(request_api):
    client, sessions, view_id, _, tags = request_api
    old_rule, ledgers, ids = _seed(request_api)
    _batch(client, ids)
    new_rule = _rule(sessions, view_id, "Explicit alternative")
    with sessions() as db:
        mapper = TagAssignmentRequestMapper(db)
        mapper.begin_write()
        fresh = mapper.create_many([{
            "rule_id": new_rule, "rule_revision": 1, "ledger_id": ledgers[0],
            "view_id": view_id, "proposed_tag_id": tags["category"]["travel"],
        }], NOW)
        mapper.commit()
    assert _batch(client, fresh)[0]["code"] == "MANUAL_TAG_CONFLICT"
    with sessions() as db:
        assert db.get(TagAssignmentRequest, ids[0]).status == 2
        assert db.get(AutoTagRule, old_rule).accepted_count == 1
        assert db.get(AutoTagRule, new_rule).accepted_count == 0
        assert db.scalar(select(func.count()).select_from(TagAssignmentRequest).where(TagAssignmentRequest.status == 2)) == 1
    assert _batch(client, ids)[0]["code"] == "ALREADY_APPROVED"


@pytest.mark.parametrize("scope", ["tag", "view"])
def test_archived_dictionary_retires_source_and_reactivation_does_not_resurrect_it(request_api, scope):
    client, sessions, view_id, _, tags = request_api
    rule, _, ids = _seed(request_api)
    _batch(client, ids)
    for status in ("ARCHIVED", "ACTIVE"):
        with sessions() as db:
            service = TargetTagService(db)
            request = TargetTagStatusRequest(status=status)
            if scope == "view":
                service.set_view_status(view_id, request)
            else:
                service.set_tag_status(view_id, tags["category"]["food"], request)
        with sessions() as db:
            assert db.get(TagAssignmentRequest, ids[0]).status == 5
            row = db.get(AutoTagRule, rule)
            assert (row.accepted_count, row.rejected_count) == (1, 0)


def test_inactive_ledger_blocks_automatic_and_manual_writes_but_preserves_reads(request_api):
    client, sessions, _, _, _ = request_api
    _, ledgers, ids = _seed(request_api)
    opened = _assignment(client, ledgers[0])
    with sessions() as db:
        review = db.scalar(select(ReviewAllocation.review_case_id).where(ReviewAllocation.ledger_entry_id == ledgers[0]))
        db.execute(update(ReviewCase).where(ReviewCase.id == review).values(status=1))
        db.commit()
    assert _batch(client, ids)[0]["code"] == "SUGGESTION_STALE"
    assert _manual(client, ledgers[0], opened["tag_state"], opened["updated_time"]).status_code == 409
    assert _assignment(client, ledgers[0])["tag_state"] == opened["tag_state"]


def test_batch_validation_select_count_does_not_scale_with_rows(request_api):
    client, sessions, _, _, _ = request_api
    _, _, single = _seed(request_api)
    _, _, many = _seed(request_api, count=20)
    engine = sessions.kw["bind"]
    statements = []

    def observed(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observed)
    try:
        _batch(client, single)
        first_count = len(statements)
        statements.clear()
        _batch(client, many)
        assert len(statements) == first_count
    finally:
        event.remove(engine, "before_cursor_execute", observed)


@pytest.mark.parametrize("ids", [[], [1, 1], [True], ["1"], [0], [2**63], list(range(1, 102))])
def test_invalid_batch_is_rejected_before_any_decision(request_api, ids):
    client, sessions, _, _, _ = request_api
    rule, _, _ = _seed(request_api)
    response = client.post("/paam/tag/v1/assignment_request/batch_approve", json={"request_ids": ids})
    assert response.status_code == 422
    with sessions() as db:
        assert db.get(AutoTagRule, rule).accepted_count == 0
        assert set(db.scalars(select(TagAssignmentRequest.status))) == {1}
