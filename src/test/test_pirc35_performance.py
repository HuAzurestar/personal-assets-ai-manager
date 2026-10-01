"""Local service measurements, not a claim about production/network latency."""
import json
from time import monotonic
from sqlalchemy import text
from backend.core import target_database
from backend.schema.flow_read import FlowListRequest
from backend.schema.ledger_entry import LedgerEntrySummaryQuery
from backend.service.flow_read_service import FlowReadService
from backend.service.ledger_entry_service import LedgerEntryService
from test_pirc35_aggregate import seed_contributions
import pytest


@pytest.mark.parametrize('dimension',['account_ref_id','account_id','party_id'])
def test_current_ownership_service_p95_at_ten_thousand_cash_rows(dimension):
    seed_contributions(10000)
    with target_database.SessionLocal() as db:
        db.execute(text("INSERT INTO ledger_account_party(id,name) VALUES(1,'Mock person'),(2,'Mock other')"))
        db.execute(text("INSERT INTO ledger_account(id,party_id,name) VALUES(1,1,'Mock A'),(2,1,'Mock B'),(3,2,'Mock C'),(4,2,'Mock D')"))
        db.execute(text("""WITH RECURSIVE seq(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM seq WHERE i<15)
            INSERT INTO ledger_account_ref(id,account_id,name) SELECT i,1+((i-1)%4),'Mock source' FROM seq"""))
        db.execute(text('UPDATE ledger_entry SET account_ref_id=1+((id-1)%15)'))
        db.commit()
        expected = db.execute(text(f'''SELECT COUNT(*) FROM ledger_entry l JOIN ledger_account_ref ref ON ref.id=l.account_ref_id
            JOIN ledger_account a ON a.id=ref.account_id WHERE {dict(account_ref_id='ref.id',account_id='a.id',party_id='a.party_id')[dimension]}=1''')).scalar_one()
    request = FlowListRequest.model_validate(dict(page_size=100,filter=dict(key=dimension,op='=',val=1),
        sorter=[dict(key='id',direction='asc')]))
    readings = dict(list_ms=[],summary_ms=[])
    for sample in range(22):
        with target_database.SessionLocal() as db:
            started = monotonic()
            page = FlowReadService(db).page(request)
            elapsed = (monotonic()-started)*1000
            assert page.total == expected and len(page.items) == 100
            if sample >= 2:
                readings['list_ms'].append(elapsed)
        with target_database.SessionLocal() as db:
            started = monotonic()
            summary = LedgerEntryService(db).summary(LedgerEntrySummaryQuery(**{dimension:1},cash_currency_code='CNY'))
            elapsed = (monotonic()-started)*1000
            assert summary.entry_count == expected and summary.totals[0].income_and_expense_in_amount == expected
            if sample >= 2:
                readings['summary_ms'].append(elapsed)
    p95 = {name:round(sorted(values)[18],2) for name,values in readings.items()}
    assert p95['summary_ms'] < 2000 and p95['list_ms'] < 30000
    print('PIRC35_LOCAL_PERFORMANCE '+json.dumps(dict(scope=dimension,rows=10000,cards=15,groups=4,
        samples=20,warmups=2,measurement='service including guards/count/page/projection, excluding network',p95=p95)))
