from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, update, delete
from sqlalchemy.exc import OperationalError

from backend.entity import (AutoTagRule, LedgerEntry, LedgerEntryTag, ReviewAllocation, ReviewCase,
                            TagAssignmentRequest, TransactionFact, TargetTag)
from backend.error import TargetTagError, TargetEconomicError
from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
from backend.schema.target_tag import TargetTagAssignmentRequest
from backend.service.review_command_service import ReviewCommandService
from backend.service.tag_assignment_request_service import TagAssignmentRequestService
from backend.service.target_tag_assignment_service import TargetTagAssignmentService
from test_tag_batch_review import _seed, _batch
from test_tag_assignment_request_api import request_api, _assignment, _manual


def test_detail_eligibility_does_not_change_standard_list_shape(request_api):
    client, sessions, _, _, _ = request_api
    _, _, ids = _seed(request_api)
    detail = client.get(f"/paam/tag/v1/assignment_request/{ids[0]}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["body"]["eligibility"] == dict(can_approve=True, code="ELIGIBLE")
    listing = client.get("/paam/tag/v1/assignment_request/list").json()["body"]
    assert set(listing) == {"items", "total", "page_index", "page_size"}
    assert "eligibility" not in listing["items"][0]


def test_duplicate_blocks_automatic_but_allows_explicit_manual_mark(request_api):
    client, sessions, _, _, _ = request_api
    rule, ledgers, ids = _seed(request_api)
    with sessions() as db:
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ledgers[0]).values(entry_type=3))
        db.commit()
    eligibility = client.get(f"/paam/tag/v1/assignment_request/{ids[0]}").json()["body"]["eligibility"]
    assert eligibility == dict(can_approve=False, code="LEDGER_DUPLICATE")
    assert _batch(client, ids)[0]["code"] == "LEDGER_DUPLICATE"
    opened = _assignment(client, ledgers[0])
    state = opened["tag_state"] | dict(category="food")
    assert _manual(client, ledgers[0], state, opened["updated_time"]).status_code == 200
    with sessions() as db:
        assert db.get(LedgerEntry, ledgers[0]).entry_type == 3
        assert db.get(AutoTagRule, rule).accepted_count == 0


@pytest.mark.parametrize("changed,code", [("review", "SUGGESTION_STALE"), ("rule", "SUGGESTION_STALE"),
                                          ("value", "SUGGESTION_STALE")])
def test_enabled_request_is_not_a_replay_when_current_premises_differ(request_api, changed, code):
    client, sessions, _, _, tags = request_api
    rule, ledgers, ids = _seed(request_api)
    assert _batch(client, ids)[0]["code"] == "APPROVED"
    with sessions() as db:
        if changed == "review":
            rid = db.scalar(select(ReviewAllocation.review_case_id).where(ReviewAllocation.ledger_entry_id == ledgers[0]))
            db.execute(update(ReviewCase).where(ReviewCase.id == rid).values(status=1))
        elif changed == "rule":
            db.execute(update(AutoTagRule).where(AutoTagRule.id == rule).values(rule_revision=2))
        else:
            db.execute(update(LedgerEntryTag).where(LedgerEntryTag.ledger_id == ledgers[0],
                LedgerEntryTag.tag_id == tags["category"]["food"]).values(tag_id=tags["category"]["travel"]))
        db.commit()
    assert _batch(client, ids)[0]["code"] == code
    detail = client.get(f"/paam/tag/v1/assignment_request/{ids[0]}").json()["body"]
    assert detail["eligibility"] == dict(can_approve=False, code=code)
    with sessions() as db:
        assert db.get(AutoTagRule, rule).accepted_count == 1


@pytest.mark.parametrize("broken", ["fact", "proposed_tag", "rule", "extra_tag"])
def test_broken_positive_relations_are_not_silently_skipped(request_api, broken):
    client, sessions, _, _, tags = request_api
    _, ledgers, ids = _seed(request_api)
    with sessions() as db:
        if broken == "fact":
            fid = db.scalar(select(ReviewAllocation.transaction_fact_id).where(ReviewAllocation.ledger_entry_id == ledgers[0]))
            db.execute(delete(TransactionFact).where(TransactionFact.id == fid))
        elif broken == "proposed_tag":
            db.execute(delete(TargetTag).where(TargetTag.id == tags["category"]["food"]))
        elif broken == "rule":
            db.execute(update(TagAssignmentRequest).where(TagAssignmentRequest.id == ids[0]).values(rule_id=999999))
        else:
            db.add(LedgerEntryTag(ledger_id=ledgers[0], tag_id=999999))
        db.commit()
    detail = client.get(f"/paam/tag/v1/assignment_request/{ids[0]}")
    assert detail.status_code == 409, detail.text
    assert detail.json()["body"]["code"] == "TAG_RELATION_BROKEN"
    listing = client.get("/paam/tag/v1/assignment_request/list")
    assert listing.status_code == 409, listing.text
    assert listing.json()["body"]["code"] == "TAG_RELATION_BROKEN"
    batch = client.post("/paam/tag/v1/assignment_request/batch_approve", json=dict(request_ids=ids))
    assert batch.status_code == 409, batch.text
    assert batch.json()["body"]["code"] == "TAG_RELATION_BROKEN"


def publish(sessions, ledger_id):
    with sessions() as db:
        fid = db.scalar(select(ReviewAllocation.transaction_fact_id).where(ReviewAllocation.ledger_entry_id == ledger_id))
        intent = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(transaction_ids=[fid]))])
        service = ReviewCommandService(db)
        preview = service.preview(ReviewChangeInput(**intent))
        assert not preview["blocking_issues"], preview
        return service.command(ReviewCommandInput(**intent, expected_reviews=preview["expected_reviews"],
                                                   preview_digest=preview["preview_digest"]))


@pytest.mark.parametrize("approve_first", [True, False])
def test_both_serialized_publish_approval_orders_retire_the_old_request(request_api, approve_first):
    client, sessions, _, _, _ = request_api
    rule, ledgers, ids = _seed(request_api)
    if approve_first:
        assert _batch(client, ids)[0]["code"] == "APPROVED"
    publish(sessions, ledgers[0])
    if not approve_first:
        assert _batch(client, ids)[0]["code"] == "SUGGESTION_STALE"
    with sessions() as db:
        assert db.get(TagAssignmentRequest, ids[0]).status == (5 if approve_first else 4)
        assert db.get(AutoTagRule, rule).accepted_count == int(approve_first)
        assert db.get(AutoTagRule, rule).scan_epoch == 2
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == 0


def test_concurrent_approval_either_invalidates_preview_or_is_retired_by_publication(request_api):
    client, sessions, _, _, _ = request_api
    rule, ledgers, ids = _seed(request_api)
    barrier = Barrier(2)
    def compete(action):
        barrier.wait(3)
        if action == "publish":
            try:
                return publish(sessions, ledgers[0])
            except TargetEconomicError as error:
                assert error.code == "ENTITY_CHANGED"
                return "STALE_PREVIEW"
        with sessions() as db:
            try:
                return TagAssignmentRequestService(db).approve(ids)
            except TargetTagError as error:
                assert error.code == "SUGGESTION_STALE"
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ("publish", "approve")))
    if results[0] == "STALE_PREVIEW":
        with sessions() as db:
            assert db.get(TagAssignmentRequest, ids[0]).status == 2
        # A new explicit decision after inspecting current state, not a retry
        # of the old command or a production automatic retry.
        publish(sessions, ledgers[0])
    with sessions() as db:
        assert db.get(TagAssignmentRequest, ids[0]).status in (4, 5)
        assert db.get(AutoTagRule, rule).accepted_count in (0, 1)


def test_commit_response_fault_is_unknown_and_current_tag_proves_the_write(request_api, monkeypatch):
    client, sessions, _, _, _ = request_api
    _, ledgers, _ = _seed(request_api)
    opened = _assignment(client, ledgers[0])
    with sessions() as db:
        commit = db.commit
        def lose_response():
            commit()
            raise OperationalError("private mock SQL must not escape", {}, Exception("private mock"))
        monkeypatch.setattr(db, "commit", lose_response)
        with pytest.raises(TargetTagError) as error:
            TargetTagAssignmentService(db).assign(ledgers[0], TargetTagAssignmentRequest(
                expected_updated_time=opened["updated_time"], tag_state=opened["tag_state"] | dict(category="food")))
        assert error.value.code == "RESULT_UNKNOWN"
        assert "private" not in str(error.value)
    assert _assignment(client, ledgers[0])["tag_state"]["category"] == "food"
