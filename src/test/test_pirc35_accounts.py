"""Fictional metadata, reliable identities and unchanged cash provenance."""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import base64
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update, insert

from backend.core import target_database
from backend.entity import LedgerAccountParty, LedgerAccountRef, LedgerAccount, LedgerEntry, TransactionFact, ReviewCase
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.error import TargetEconomicError
from backend.target_main import app
from backend.parser.statement_parser import parse_statement
from backend.schema.intake import IntakePreviewRequest, IntakeConfirmRequest
from backend.service.target_intake_service import TargetIntakeService
from backend.error import TargetIntakeError

BASE = "/paam/ledger/v1"


def post(client, path, payload):
    response = client.post(BASE + path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["body"]


def seed_cash(ref_ids):
    with target_database.SessionLocal() as db:
        for index, ref_id in enumerate(ref_ids, 1):
            db.add(TransactionFact(id=index, fact_key=f"fictional-{index}", account_code="ORIGINAL",
                amount=100 + index, currency_code="USD" if index % 2 else "KRW", cash_direction=2,
                occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
        db.flush()
        ReviewCommandMapper(db).create_initial_defaults(range(1, len(ref_ids) + 1),
            account_refs=dict(enumerate(ref_ids, 1)))
        db.commit()


def test_fifteen_cards_four_groups_moves_never_rewrite_cash_and_closed_not_removed():
    with TestClient(app) as client:
        party = post(client, "/account-party", dict(name="Mock person"))
        groups = [post(client, "/account", dict(name=f"Set {i}", party_id=party["id"])) for i in range(4)]
        assert all(item["statement_interval_months"] == item["snapshot_interval_months"] == 0 for item in groups)
        refs = [post(client, "/account-ref", dict(account_id=groups[i % 4]["id"],
             name="Same name", reference=f"990000{i:04d}1234")) for i in range(15)]
        assert len({row["id"] for row in refs}) == 15
        assert all(row["reference"] == "****1234" for row in refs)
        seed_cash([row["id"] for row in refs])
        with target_database.SessionLocal() as db:
            before = [dict(row) for row in db.execute(select(LedgerEntry.__table__)).mappings()]
        ref = refs[0]
        change = dict(account_id=groups[1]["id"], expected_updated_time=ref["updated_time"])
        direct = client.put(BASE + f"/account-ref/{ref['id']}/metadata", json=change | dict(status="ACTIVE"))
        assert direct.status_code == 409 and direct.json()["body"]["code"] == "ACCOUNT_MOVE_REQUIRES_PREVIEW"
        preview = post(client, f"/account-ref/{ref['id']}/move-preview", change)
        assert preview["affected_ledger_count"] == 1 and not preview["cross_party"]
        moved = post(client, f"/account-ref/{ref['id']}/move-command", change | dict(preview_digest=preview["preview_digest"]))
        assert moved["account_id"] == groups[1]["id"]
        closed = client.put(BASE + f"/account-ref/{ref['id']}/metadata", json=dict(account_id=moved["account_id"],
            expected_updated_time=moved["updated_time"], name="Cold card", status="CLOSED", reference=moved["reference"]))
        assert closed.status_code == 200, closed.text
        with target_database.SessionLocal() as db:
            assert [dict(row) for row in db.execute(select(LedgerEntry.__table__)).mappings()] == before
            assert db.get(LedgerAccountRef, ref["id"]).reference == "99000000001234"
            assert db.scalar(select(func.count()).select_from(ReviewCase)) == 15
        for group in groups:
            result = client.get(BASE + "/account-ref/list", params={"filter": __import__("json").dumps(
                dict(key="account_id", op="=", val=group["id"]))}).json()["body"]
            expected = sum(row["account_id"] == group["id"] for row in refs) + (1 if group == groups[1] else -1 if group == groups[0] else 0)
            assert result["total"] == expected
            assert set(result) == {"items", "total", "page_index", "page_size"}


def test_cross_person_move_requires_fresh_impact_preview_and_cas():
    with TestClient(app) as client:
        people = [post(client, "/account-party", dict(name="Same person label")) for _ in range(2)]
        groups = [post(client, "/account", dict(name="Same set", party_id=row["id"])) for row in people]
        ref = post(client, "/account-ref", dict(account_id=groups[0]["id"]))
        change = dict(account_id=groups[1]["id"], expected_updated_time=ref["updated_time"])
        preview = post(client, f"/account-ref/{ref['id']}/move-preview", change)
        assert preview["cross_party"] and preview["from_party_id"] != preview["to_party_id"]
        seed_cash([ref["id"]])
        stale = client.post(BASE + f"/account-ref/{ref['id']}/move-command", json=change | dict(preview_digest=preview["preview_digest"]))
        assert stale.status_code == 409 and stale.json()["body"]["code"] == "ENTITY_CHANGED"
        fresh = post(client, f"/account-ref/{ref['id']}/move-preview", change)
        moved = post(client, f"/account-ref/{ref['id']}/move-command", change | dict(preview_digest=fresh["preview_digest"]))
        assert moved["account_id"] == groups[1]["id"]
        assert client.post(BASE + f"/account-ref/{ref['id']}/move-command", json=change | dict(preview_digest=fresh["preview_digest"])).status_code == 409


def test_known_unassigned_distinct_from_unknown_and_reliable_namespace_exact():
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        mapper = AccountManagementMapper(db)
        identities = [("ccb:statement-v1", "990000000000001234"), ("abc:statement-v1", "990000000000001234"),
                      ("ccb:statement-v1", "991111111111111234")]
        refs = mapper.create_reliable_refs(identities)
        assert len({row["id"] for row in refs.values()}) == 3
        assert all(row["account_id"] == 0 for row in refs.values())
        again = mapper.create_reliable_refs(identities * 2)
        assert {key: row["id"] for key, row in refs.items()} == {key: row["id"] for key, row in again.items()}
        db.commit()
    with TestClient(app) as client:
        response = client.get(BASE + "/account-ref/list").json()["body"]
        assert response["total"] == 3
        assert all(row["identity_strength"] == "RELIABLE" and row["source_identity"] == "****1234" for row in response["items"])


@pytest.mark.parametrize("kind,values", [("account", dict(party_id=987, name="broken")),
                                        ("ref", dict(account_id=987))])
def test_unused_positive_orphans_report_relation_broken(kind, values):
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        entity = LedgerAccount if kind == "account" else LedgerAccountRef
        db.add(entity(**values))
        db.commit()
        with pytest.raises(TargetEconomicError, match="关系损坏") as error:
            TrustedRelationMapper(db).validate()
        assert error.value.code == "ACCOUNT_RELATION_BROKEN"


def test_metadata_strict_inputs_sorters_and_nonblank_names():
    with TestClient(app) as client:
        assert client.post(BASE + "/account-party", json={"name": " "}).status_code == 422
        assert client.post(BASE + "/account", json=dict(name="Mock", party_id=True)).status_code == 422
        assert client.post(BASE + "/account-ref", json=dict(source_identity="fake", identity_strength="RELIABLE")).status_code == 422
        result = client.get(BASE + "/account-ref/list", params={"sorter": '[{"key":"account_id","direction":"asc"}]'})
        assert result.status_code == 422
        assert client.get(BASE + "/account-ref/list", params={"page_size": 101}).status_code == 422


def upload(service, content):
    return service.preview(IntakePreviewRequest(files=[dict(filename="mock.csv",
        content_base64=base64.b64encode(content).decode())]), source_timezone=ZoneInfo("Asia/Hong_Kong"))

from import_batch_helpers import confirm_service_batch, prepare_service_batch


def test_reliable_import_reuses_ref_default_atomic_weak_stays_unknown():
    target_database.init_target_db()
    content = (Path(__file__).parent / "fixtures/pirc35/ccb-2.csv").read_bytes()
    with target_database.SessionLocal() as db:
        service = TargetIntakeService(db)
        first = upload(service, content)
        result = confirm_service_batch(service, first)
        ref = db.scalar(select(LedgerAccountRef))
        assert ref.account_id == 0 and ref.source_identity == "990000000000001234"
        assert set(db.scalars(select(LedgerEntry.account_ref_id))) == {ref.id}
        assert "_account_refs" not in result
        again = upload(service, content.replace(b"2024-", b"2025-"))
        assert again["issue_count"] == 0
        confirm_service_batch(service, again)
        assert db.scalar(select(func.count()).select_from(LedgerAccountRef)) == 1
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 48
        # A full card with the same suffix already exists, but weak evidence stays weak.
        weak_content = content.replace(b"990000000000001234", b"**************1234").replace(b"2024-", b"2026-")
        parsed = parse_statement(weak_content, "mock.csv", source_timezone=ZoneInfo("Asia/Hong_Kong"))
        assert all(row["source_account"]["identity_strength"] == "WEAK" for row in parsed["rows"])
        weak = upload(service, weak_content)
        assert weak["issue_count"] == 0
        confirm_service_batch(service, weak)
        assert db.scalar(select(func.count()).select_from(LedgerAccountRef)) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.account_ref_id == 0)) == 24


def test_initial_default_failure_rolls_back_new_identity_fact_and_sources(monkeypatch):
    target_database.init_target_db()
    content = (Path(__file__).parent / "fixtures/pirc35/ccb-2.csv").read_bytes()
    with target_database.SessionLocal() as db:
        service = TargetIntakeService(db)
        preview = upload(service, content)
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic default failure")
        monkeypatch.setattr(ReviewCommandMapper, "create_initial_defaults", fail)
        with pytest.raises(RuntimeError, match="synthetic default failure"):
            confirm_service_batch(service, preview)
        assert db.scalar(select(func.count()).select_from(TransactionFact)) == 0
        assert db.scalar(select(func.count()).select_from(LedgerAccountRef)) == 0
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 0


def test_import_preview_detects_changed_source_ref_and_closed_source_does_not_add_fact():
    target_database.init_target_db()
    content = (Path(__file__).parent / "fixtures/pirc35/ccb-2.csv").read_bytes()
    with target_database.SessionLocal() as db:
        service = TargetIntakeService(db)
        first = upload(service, content)
        confirm_service_batch(service, first)
        preview = upload(service, content.replace(b"2024-", b"2025-"))
        intent = prepare_service_batch(service, preview)
        db.execute(update(LedgerAccountRef).values(name="changed after preview", status="CLOSED",
            updated_time=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        db.commit()
        with pytest.raises(TargetIntakeError) as stale:
            service.confirm(preview["token"], intent)
        assert stale.value.status_code == 409
        fresh = upload(service, content.replace(b"2024-", b"2025-"))
        with pytest.raises(TargetIntakeError) as closed:
            confirm_service_batch(service, fresh)
        assert closed.value.code == "ACCOUNT_NOT_ACTIVE"
        assert db.scalar(select(func.count()).select_from(TransactionFact)) == 24
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 24


def test_edit_cas_and_commit_outcome_unknown_preserve_current_metadata(monkeypatch):
    from backend.service.account_management_service import AccountManagementService
    from backend.schema.account_management import PartyCreate, PartyUpdate
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        service = AccountManagementService(db)
        party = service.create("party", PartyCreate(name="Mock person"))
        patch = PartyUpdate(name="Changed", status="ACTIVE", expected_updated_time=party["updated_time"])
        changed = service.update("party", party["id"], patch)
        with pytest.raises(TargetEconomicError) as stale:
            service.update("party", party["id"], patch)
        assert stale.value.code == "ENTITY_CHANGED"
        original_commit = db.commit
        def commit_then_lose():
            original_commit()
            raise OperationalError("synthetic", {}, Exception("response lost"))
        from sqlalchemy.exc import OperationalError
        monkeypatch.setattr(db, "commit", commit_then_lose)
        with pytest.raises(TargetEconomicError) as unknown:
            service.update("party", party["id"], PartyUpdate(name="Committed but unknown", status="ACTIVE",
                expected_updated_time=changed["updated_time"]))
        assert unknown.value.code == "RESULT_UNKNOWN"
        monkeypatch.setattr(db, "commit", original_commit)
        assert service.get("party", party["id"])["name"] == "Committed but unknown"
        assert db.scalar(select(func.count()).select_from(LedgerAccountParty)) == 1


@pytest.mark.parametrize("size", [100, 1000, 10000])
def test_account_scope_uses_indexed_ref_set_without_literal_id_expansion(size):
    """Index/performance probe, not a published ledger fixture."""
    from time import perf_counter
    target_database.init_target_db()
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with target_database.SessionLocal() as db:
        db.add_all([LedgerAccountParty(id=i, name=f"Mock person {i}") for i in (1, 2)])
        db.add_all([LedgerAccount(id=i, party_id=i, name=f"Mock set {i}") for i in (1, 2)])
        db.flush()
        for offset in range(0, size, 400):
            ids = range(offset + 1, min(offset + 400, size) + 1)
            db.execute(insert(LedgerAccountRef.__table__), [dict(id=i, account_id=1 if i % 2 else 2,
                status="CLOSED" if i % 5 == 0 else "ACTIVE") for i in ids])
            db.execute(insert(LedgerEntry.__table__), [dict(id=i, account_ref_id=i, entry_type=0, entry_direction=2,
                cash_amount=100, cash_currency_code="USD" if i % 2 else "KRW", account_code="",
                occurred_time=now) for i in ids])
        db.commit()
        mapper = AccountManagementMapper(db)
        elapsed = []
        for dimension in ("account_id", "party_id"):
            predicate = LedgerEntry.account_ref_id.in_(mapper.ref_scope(dimension, 1))
            count = select(func.count()).select_from(LedgerEntry).where(predicate)
            page = select(LedgerEntry.id).where(predicate).order_by(LedgerEntry.occurred_time, LedgerEntry.id).limit(100)
            sql = str(page.compile(db.bind, compile_kwargs={"literal_binds": True}))
            plan = "\n".join(str(row) for row in db.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + sql))
            assert "ledger_entry_account_ref_time" in plan
            assert "ledger_account_ref_account" in plan
            if dimension == "party_id":
                assert "ledger_account_party_lookup" in plan
            for _ in range(5):
                start = perf_counter()
                assert db.scalar(count) == size // 2
                assert len(db.scalars(page).all()) == min(size // 2, 100)
                elapsed.append(perf_counter() - start)
        assert max(elapsed) < 2, f"indexed local probe exceeded two seconds at {size} refs"
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.account_ref_id.in_(mapper.ref_scope("party_id", 999)))) == 0
