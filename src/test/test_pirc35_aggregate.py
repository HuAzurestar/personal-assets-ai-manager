"""Complete exact aggregation, using actual isolated fictional SQLite rows."""
from datetime import datetime, timezone
from time import monotonic

import pytest
from sqlalchemy import text, update

from backend.core import target_database
from backend.core.feature_observability import observability
from backend.entity import LedgerAccount, LedgerAccountParty, LedgerAccountRef, LedgerEntry, ReviewCase
from backend.error import TargetEconomicError
from backend.schema.ledger_entry import LedgerEntrySummaryQuery
from backend.service.ledger_entry_service import LedgerEntryService
from test_ledger_api import economic_api, _facts


def seed_contributions(count, *, amount=1, varied_days=False):
    target_database.ensure_target_schema()
    with target_database.SessionLocal() as db:
        # Bulk construction is test setup only, not a runtime import or bypass
        # endpoint. Every Fact has one exact original default/flow/allocation.
        occurred = "strftime('%Y-%m-%dT00:00:00.000000Z','2000-01-01','+'||i||' days')" if varied_days else "'2024-01-01T00:00:00.000000Z'"
        db.execute(text(f"""WITH RECURSIVE seq(i) AS (
            VALUES(1) UNION ALL SELECT i+1 FROM seq WHERE i < :count)
            INSERT INTO transaction_fact(id,fact_key,occurred_time,cash_direction,amount,currency_code,
                account_code,counterparty_name,counterparty_account_ref,summary,created_time,updated_time)
            SELECT i,'fictional-'||i,{occurred},1,:amount,'CNY','','Mock merchant','','Mock contribution',
                '2024-01-01T00:00:00.000000Z','2024-01-01T00:00:00.000000Z' FROM seq"""), dict(count=count, amount=amount))
        db.execute(text("""INSERT INTO review_case(id,behavior_type,status,title,created_time,updated_time)
            SELECT id,0,0,'Mock original',created_time,updated_time FROM transaction_fact"""))
        db.execute(text("""INSERT INTO ledger_entry(id,entry_type,entry_direction,cash_amount,cash_currency_code,
            account_ref_id,account_code,counterparty_account_ref,occurred_time,created_time,updated_time)
            SELECT id,0,1,amount,currency_code,0,'','',occurred_time,created_time,updated_time FROM transaction_fact"""))
        db.execute(text("""INSERT INTO review_transaction_ledger_allocation(id,review_id,transaction_id,ledger_id,
            cash_amount,cash_currency_code,created_time,updated_time)
            SELECT id,id,id,id,amount,currency_code,created_time,updated_time FROM transaction_fact"""))
        db.commit()


def test_fifty_thousand_actual_contributors_are_complete_and_bounded():
    seed_contributions(50000)
    with target_database.SessionLocal() as db:
        started = monotonic()
        result = LedgerEntryService(db).summary(LedgerEntrySummaryQuery())
        duration = monotonic() - started
    assert result.entry_count == 50000
    assert result.totals[0].income_and_expense_in_amount == 50000
    assert result.trend[0].income_amount == 50000
    assert duration < 2


def test_summary_scalar_fetch_keeps_integer_and_utc_microsecond_types():
    from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
    seed_contributions(1, amount=12345)
    occurred = "2024-01-01T00:00:00.000007Z"
    with target_database.SessionLocal() as db:
        db.execute(text("UPDATE ledger_entry SET occurred_time=:occurred"), dict(occurred=occurred))
        db.execute(text("UPDATE transaction_fact SET occurred_time=:occurred"), dict(occurred=occurred))
        db.commit()
        rows = LedgerEntryMapper(db).summary(LedgerEntrySummaryQuery())
        assert len(rows) == 1 and type(rows[0]["amount"]) is int
        assert rows[0]["amount"] == 12345 and rows[0]["currency_code"] == "CNY"
        assert rows[0]["occurred_time"] == datetime(2024, 1, 1, microsecond=7, tzinfo=timezone.utc)
        assert rows[0]["occurred_time"].tzinfo == timezone.utc


def test_fifty_thousand_and_one_actual_contributors_return_no_partial_sum():
    seed_contributions(50001)
    with target_database.SessionLocal() as db, pytest.raises(TargetEconomicError) as caught:
        LedgerEntryService(db).summary(LedgerEntrySummaryQuery())
    assert caught.value.code == "AGGREGATION_LIMIT" and caught.value.status_code == 413
    assert not caught.value.details
    assert any(row["name"] == "limit_count" and row["total"] == 1 for row in observability.snapshot()["metrics"])


def test_exact_integer_overflow_refuses_whole_result_even_below_row_limit():
    seed_contributions(2, amount=9_000_000_000_000)
    with target_database.SessionLocal() as db, pytest.raises(TargetEconomicError) as caught:
        LedgerEntryService(db).summary(LedgerEntrySummaryQuery())
    assert caught.value.code == "AGGREGATION_LIMIT"


def test_large_complete_trend_cannot_exceed_response_budget():
    seed_contributions(30000, varied_days=True)
    with target_database.SessionLocal() as db, pytest.raises(TargetEconomicError) as caught:
        LedgerEntryService(db).summary(LedgerEntrySummaryQuery())
    assert caught.value.code == "AGGREGATION_LIMIT"


def test_sql_interruption_is_explicit_not_partial(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
    seed_contributions(1)

    def interrupted(*args):
        raise OperationalError("fictional query", {}, Exception("fictional sensitive parameters"))

    monkeypatch.setattr(LedgerEntryMapper, "summary", interrupted)
    with target_database.SessionLocal() as db, pytest.raises(TargetEconomicError) as caught:
        LedgerEntryService(db).summary(LedgerEntrySummaryQuery())
    assert caught.value.code == "AGGREGATION_LIMIT" and caught.value.status_code == 503


def test_deadline_expiry_after_body_read_still_refuses_partial_result(monkeypatch):
    from backend.mapper import bounded_query_mapper as budget
    from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
    seed_contributions(1)
    clock = [0]
    monkeypatch.setattr(budget, "monotonic", lambda: clock[0])
    original = LedgerEntryMapper.summary

    def body(self, query):
        result = original(self, query)
        clock[0] = 2.001
        return result

    monkeypatch.setattr(LedgerEntryMapper, "summary", body)
    with target_database.SessionLocal() as db, pytest.raises(TargetEconomicError) as caught:
        LedgerEntryService(db).summary(LedgerEntrySummaryQuery())
    assert caught.value.code == "AGGREGATION_LIMIT" and caught.value.status_code == 503


def test_actual_summary_account_plan_uses_ref_currency_time_index():
    from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
    seed_contributions(1)
    with target_database.SessionLocal() as db:
        statement = LedgerEntryMapper(db).summary_statement(LedgerEntrySummaryQuery(party_id=1, cash_currency_code="CNY"))
        sql = str(statement.compile(db.get_bind(), compile_kwargs=dict(literal_binds=True)))
        plan = " ".join(str(row) for row in db.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "ledger_entry_account_ref_time" in plan
    assert "ledger_account_party_lookup" in plan
    assert "50001" in sql


def test_current_account_scope_distinguishes_unidentified_and_known_unassigned(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY"), ("IN", 200, "CNY"), ("IN", 300, "CNY"), ("IN", 400, "USD")])
    with sessions() as db:
        db.add(LedgerAccountParty(id=1, name="Mock owner", status="ACTIVE"))
        db.add(LedgerAccount(id=1, party_id=1, name="Mock group", status="ACTIVE"))
        db.add_all([LedgerAccountRef(id=i, account_id=1 if i == 1 else 0, name="Mock source", institution="", reference="",
            source_namespace="", source_identity="", identity_strength=0, status="ACTIVE") for i in (1, 2)])
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[0]).values(account_ref_id=1))
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[1]).values(account_ref_id=2))
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[3]).values(account_ref_id=1))
        db.commit()
    for scope, expected in [(dict(account_ref_id=0), 300), (dict(account_id=0), 200),
                            (dict(account_ref_id=1), 100), (dict(account_id=1), 100), (dict(party_id=1), 100)]:
        response = client.get("/paam/ledger/v1/flow/summary", params=scope | dict(cash_currency_code="CNY"))
        assert response.status_code == 200, response.text
        body = response.json()["body"]
        assert body["entry_count"] == 1 and body["totals"][0]["income_and_expense_in_amount"] == expected
    empty = client.get("/paam/ledger/v1/flow/summary", params=dict(account_ref_id=0, account_id=0)).json()["body"]
    assert empty["entry_count"] == 0 and empty["totals"] == []


def test_owner_move_between_guard_and_body_is_seen_only_on_next_snapshot(economic_api, monkeypatch):
    from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY")])
    with sessions() as db:
        db.execute(text("PRAGMA journal_mode=WAL"))
        db.add_all([LedgerAccountParty(id=i, name="Mock owner", status="ACTIVE") for i in (1, 2)])
        db.add_all([LedgerAccount(id=i, party_id=i, name="Mock group", status="ACTIVE") for i in (1, 2)])
        db.add(LedgerAccountRef(id=1, account_id=1, name="Mock source", institution="", reference="",
            source_namespace="", source_identity="", identity_strength=0, status="ACTIVE"))
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[0]).values(account_ref_id=1))
        db.commit()
    original, moved = LedgerEntryMapper.summary, []

    def after_guard(self, query):
        if not moved:
            with sessions() as writer:
                writer.execute(update(LedgerAccountRef).where(LedgerAccountRef.id == 1).values(account_id=2))
                writer.commit()
            moved.append(True)
        return original(self, query)

    monkeypatch.setattr(LedgerEntryMapper, "summary", after_guard)
    first = client.get("/paam/ledger/v1/flow/summary", params=dict(party_id=1))
    assert first.status_code == 200 and first.json()["body"]["entry_count"] == 1
    second = client.get("/paam/ledger/v1/flow/summary", params=dict(party_id=1))
    assert second.status_code == 200 and second.json()["body"]["entry_count"] == 0
    latest = client.get("/paam/ledger/v1/flow/summary", params=dict(party_id=2))
    assert latest.status_code == 200 and latest.json()["body"]["entry_count"] == 1


@pytest.mark.parametrize("params", [dict(account_ref_id=-1), dict(party_id=0), dict(account_id=2**63),
    dict(cash_currency_code="cny"), dict(cash_currency_code="PRIVATE"), dict(account_code="legacy")])
def test_summary_rejects_invalid_or_legacy_account_scope(economic_api, params):
    client, _ = economic_api
    assert client.get("/paam/ledger/v1/flow/summary", params=params).status_code == 422


def test_duplicate_and_inactive_evidence_do_not_contribute_to_summary(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY"), ("IN", 200, "CNY"), ("IN", 300, "CNY")])
    with sessions() as db:
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[1]).values(entry_type=3))
        db.execute(update(ReviewCase).where(ReviewCase.id == ids[2]).values(status=1))
        db.commit()
    body = client.get("/paam/ledger/v1/flow/summary").json()["body"]
    assert body["entry_count"] == 1 and body["totals"][0].get("income_and_expense_in_amount") == 100


def test_positive_orphan_fails_before_empty_scope_can_hide_it(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("IN", 100, "CNY")])
    with sessions() as db:
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[0]).values(account_ref_id=999))
        db.commit()
    response = client.get("/paam/ledger/v1/flow/summary", params=dict(party_id=999))
    assert response.status_code == 409 and response.json()["body"]["code"] == "ACCOUNT_RELATION_BROKEN"
