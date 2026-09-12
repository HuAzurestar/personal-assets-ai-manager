from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill
from app.target_database import TargetBase
from app.models.target import BillFact, LedgerEntry
from app.services.target_shadow_migration_service import TargetShadowMigrationService


def test_complete_target_shadow_pipeline_is_one_call_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-all.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        db.add(Bill(
            occurred_at=datetime(2026, 9, 5, 12),
            merchant="lunch",
            note="",
            amount=-12.34,
            currency="CNY",
            account_name="wallet",
        ))
        db.commit()

        first = TargetShadowMigrationService(db).backfill_and_compare()
        assert first.matched, first.model_dump()
        assert first.status.ready
        fact = db.scalar(select(BillFact))
        entry = db.scalar(select(LedgerEntry))
        assert fact.account_code == entry.out_account_code == "wallet"
        assert entry.in_account_code == "UNKNOWN"

        second = TargetShadowMigrationService(db).backfill_and_compare()
        assert second.matched
        assert second.facts.bill_fact.inserted_count == 0
        assert second.projection.ledger_entry.inserted_count == 0
