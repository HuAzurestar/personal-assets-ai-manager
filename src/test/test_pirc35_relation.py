from datetime import datetime, timezone

import pytest

from backend.core import target_database
from backend.entity import LedgerEntry, ReviewAllocation, ReviewCase, TransactionFact
from backend.error import TargetEconomicError
from backend.service.ledger_entry_service import LedgerEntryService


def seed(db):
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db.add(TransactionFact(id=1, fact_key="mock", occurred_time=now, cash_direction=1,
                           amount=100, currency_code="CNY", account_code=""))
    db.add(ReviewCase(id=1, behavior_type=0, status=0, title="mock"))
    db.add(LedgerEntry(id=1, entry_type=0, entry_direction=1, amount=100, currency_code="CNY",
                       account_code="", occurred_time=now))
    db.add(ReviewAllocation(id=1, review_case_id=1, transaction_fact_id=1, ledger_entry_id=1,
                            amount=100, currency_code="CNY"))
    db.commit()


@pytest.mark.parametrize("corrupt", ["allocation", "account", "source"])
def test_positive_broken_refs_fail_instead_of_disappearing_from_join(corrupt):
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        seed(db)
        if corrupt == "allocation":
            db.get(ReviewAllocation, 1).amount = 99
        elif corrupt == "account":
            db.get(LedgerEntry, 1).account_ref_id = 999
        else:
            db.get(ReviewAllocation, 1).transaction_fact_id = 999
        db.commit()
        with pytest.raises(TargetEconomicError) as error:
            LedgerEntryService(db).detail(1)
        assert error.value.code == ("ACCOUNT_RELATION_BROKEN" if corrupt == "account" else "RELATION_BROKEN")
