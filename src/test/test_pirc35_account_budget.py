"""D03: account preview/read and metadata writes retain the shared deadlines.

All data lives in the autouse disposable SQLite fixture. Deterministic clock
advancement tests expiry, not actual thirty-second capacity or production SLA.
The SQL expiry case executes a real SQLite statement, not a fabricated 503.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from backend.core import target_database
from backend.entity import TransactionFact
from backend.mapper import bounded_query_mapper as bounded
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service import account_management_service as account_module
from backend.service.account_management_service import AccountManagementService
from backend.target_main import app
from test_pirc35_import_duplicate import manifest

BASE = "/paam/ledger/v1"
SQL_PROBE = text("WITH RECURSIVE counter(n) AS (SELECT 1 UNION ALL "
    "SELECT n + 1 FROM counter WHERE n < 100000) SELECT sum(n) FROM counter")


def post(client, path, payload):
    response = client.post(BASE + path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["body"]


@pytest.fixture
def account_api():
    with TestClient(app) as client:
        people = [post(client, "/account-party", dict(name=f"Mock budget owner {i}")) for i in range(2)]
        groups = [post(client, "/account", dict(name=f"Mock budget group {i}", party_id=person["id"]))
                  for i, person in enumerate(people)]
        ref = post(client, "/account-ref", dict(account_id=groups[0]["id"], name="Mock budget source",
            institution="Mock bank", reference="99000000001234"))
        with target_database.SessionLocal() as db:
            db.add(TransactionFact(id=1, fact_key="mock-account-budget", account_code="ORIGINAL",
                amount=40000, currency_code="CNY", cash_direction=2,
                occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([1], account_refs={1: ref["id"]})
            db.commit()
        yield client, people, groups, ref


def state():
    with target_database.SessionLocal() as db:
        result = manifest(AccountManagementMapper(db))
    assert len(result) == 20
    return result


def move_input(groups, ref):
    return dict(account_id=groups[1]["id"], expected_updated_time=ref["updated_time"])


@pytest.mark.parametrize("stage", ["relations", "sql", "assembly"])
def test_move_preview_expired_read_returns_query_busy_without_changes(account_api, monkeypatch, stage):
    client, _, groups, ref = account_api
    before = state()
    clock = [0.0]
    writes, interrupted = [], []

    def record_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)

    event.listen(target_database.engine, "before_cursor_execute", record_write)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(bounded, "monotonic", lambda: clock[0])
            if stage == "relations":
                original = TrustedRelationMapper.validate
                def expired_relations(self):
                    result = original(self)
                    clock[0] = 31.0
                    return result
                patch.setattr(TrustedRelationMapper, "validate", expired_relations)
            elif stage == "assembly":
                original = AccountManagementService._move
                def expired_assembly(self, *args):
                    result = original(self, *args)
                    clock[0] = 31.0
                    return result
                patch.setattr(AccountManagementService, "_move", expired_assembly)
            else:
                original = AccountManagementMapper.ledger_effect
                def expired_sql(self, ref_id):
                    result = original(self, ref_id)
                    clock[0] = 31.0
                    try:
                        assert self.db.scalar(SQL_PROBE) == 5000050000
                    except Exception as error:
                        interrupted.append(str(error))
                        raise
                    return result
                patch.setattr(AccountManagementMapper, "ledger_effect", expired_sql)
            response = client.post(BASE + f"/account-ref/{ref['id']}/move-preview", json=move_input(groups, ref))
            assert response.status_code == 503, response.text
            assert response.json()["status"] == 503 and response.json()["body"]["code"] == "QUERY_BUSY"
            if stage == "sql":
                assert len(interrupted) == 1 and "interrupted" in interrupted[0]
            # The expired handler must not poison the connection after failure.
            with target_database.SessionLocal() as db:
                assert db.scalar(SQL_PROBE) == 5000050000
        assert writes == [] and state() == before
        fresh = post(client, f"/account-ref/{ref['id']}/move-preview", move_input(groups, ref))
        assert fresh["cross_party"] and fresh["affected_ledger_count"] == 1
        assert state() == before
    finally:
        event.remove(target_database.engine, "before_cursor_execute", record_write)


def test_move_preview_is_readonly_even_with_another_write_slot_owner(account_api):
    client, _, groups, ref = account_api
    before = state()
    with target_database.engine.connect() as holder:
        holder.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            result = post(client, f"/account-ref/{ref['id']}/move-preview", move_input(groups, ref))
        finally:
            holder.rollback()
    assert result["cross_party"] and result["affected_ledger_count"] == 1
    assert result["from_account_id"] == groups[0]["id"] and result["to_account_id"] == groups[1]["id"]
    assert state() == before


@pytest.mark.parametrize("failure,code", [("stale", "ENTITY_CHANGED"),
    ("closed", "ACCOUNT_NOT_ACTIVE"), ("broken", "ACCOUNT_RELATION_BROKEN")])
def test_move_preview_budget_preserves_domain_checks(account_api, failure, code):
    client, _, groups, ref = account_api
    payload = move_input(groups, ref)
    if failure == "stale":
        current = datetime.fromisoformat(ref["updated_time"].replace("Z", "+00:00"))
        payload["expected_updated_time"] = (current - timedelta(microseconds=1)).isoformat()
    elif failure == "closed":
        response = client.put(BASE + f"/account/{groups[1]['id']}/metadata", json=dict(
            name=groups[1]["name"], status="CLOSED", expected_updated_time=groups[1]["updated_time"]))
        assert response.status_code == 200, response.text
    else:
        # A deliberate invalid relation in this disposable fixture, never a
        # repair of an accepted or user-owned database.
        from backend.entity import LedgerAccountRef
        with target_database.SessionLocal() as db:
            db.add(LedgerAccountRef(account_id=987, name="Mock orphan outside scope"))
            db.commit()
    before = state()
    response = client.post(BASE + f"/account-ref/{ref['id']}/move-preview", json=payload)
    assert response.status_code == 409 and response.json()["body"]["code"] == code, response.text
    assert state() == before


def mutation(client, people, groups, ref, kind):
    commands = {
        "create_party": ("POST", "/account-party", dict(name="Mock budget new owner")),
        "create_account": ("POST", "/account", dict(name="Mock budget new group", party_id=people[0]["id"])),
        "create_ref": ("POST", "/account-ref", dict(name="Mock budget new source", account_id=groups[0]["id"])),
        "edit_party": ("PUT", f"/account-party/{people[0]['id']}/metadata", dict(name="Mock budget changed owner",
            status="ACTIVE", expected_updated_time=people[0]["updated_time"])),
        "edit_account": ("PUT", f"/account/{groups[0]['id']}/metadata", dict(name="Mock budget changed group",
            status="ACTIVE", expected_updated_time=groups[0]["updated_time"])),
        "edit_ref": ("PUT", f"/account-ref/{ref['id']}/metadata", dict(name="Mock budget changed source",
            account_id=groups[0]["id"], status="ACTIVE", reference=ref["reference"],
            expected_updated_time=ref["updated_time"])),
    }
    if kind == "move":
        change = move_input(groups, ref)
        plan = post(client, f"/account-ref/{ref['id']}/move-preview", change)
        return "POST", f"/account-ref/{ref['id']}/move-command", change | dict(preview_digest=plan["preview_digest"])
    return commands[kind]


@pytest.mark.parametrize("kind", ["create_party", "create_account", "create_ref",
    "edit_party", "edit_account", "edit_ref", "move"])
def test_account_expired_python_write_rolls_back_every_metadata_writer(account_api, monkeypatch, kind):
    client, people, groups, ref = account_api
    method, path, payload = mutation(client, people, groups, ref, kind)
    before = state()
    original = AccountManagementService._po
    commits, dml = [], []
    def committed(connection):
        commits.append(True)
    def wrote(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            dml.append(True)
    event.listen(target_database.engine, "commit", committed)
    event.listen(target_database.engine, "before_cursor_execute", wrote)
    try:
        with monkeypatch.context() as patch:
            def expired(self, *args):
                result = original(self, *args)
                # Only the Service's final Python calculation is expired; the
                # SQLite progress handler retains its real clock and 2s limit.
                patch.setattr(account_module, "monotonic", lambda: self.mapper.write_started + 2.001, raising=False)
                return result
            patch.setattr(AccountManagementService, "_po", expired)
            response = client.request(method, BASE + path, json=payload)
        assert response.status_code == 503, response.text
        assert response.json()["body"]["code"] == "WRITE_BUSY"
        assert dml and commits == [], "real SQL mutation occurred but no commit was permitted"
    finally:
        event.remove(target_database.engine, "commit", committed)
        event.remove(target_database.engine, "before_cursor_execute", wrote)
    assert state() == before, "all twenty tables, original timestamps and financial outputs must remain exact"
    # Explicit new action after the confirmed rollback, never automatic retry.
    recovered = client.request(method, BASE + path, json=payload)
    assert recovered.status_code == 200, recovered.text
    after = state()
    for name in before.keys() - {"ledger_account_party", "ledger_account", "ledger_account_ref"}:
        assert after[name] == before[name], name
