from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    CASH_DIRECTION_OUT,
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_SOURCE_ALIPAY,
    IMPORT_SOURCE_WECHAT,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.router.dependency import get_db
from backend.router.import_file import router


@pytest.fixture
def import_file_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'import-file-api.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        yield client, sessions
    engine.dispose()


def _seed(sessions):
    now = datetime(2026, 9, 16, 8)
    with sessions() as db:
        files = [
            TransactionImportFile(
                batch_code=f"batch-{index}",
                source_type=source,
                filename=filename,
                file_format=IMPORT_FILE_FORMAT_CSV,
                sha256=str(index) * 64,
                period_start="2026-09-01",
                period_end="2026-09-30",
                total_count=1,
                success_count=1,
                skip_count=0,
                issue_count=0,
                status=IMPORT_FILE_STATUS_IMPORTED,
                created_time=now + timedelta(minutes=index),
                updated_time=now + timedelta(minutes=index),
            )
            for index, source, filename in (
                (1, IMPORT_SOURCE_WECHAT, "wechat-september.csv"),
                (2, IMPORT_SOURCE_ALIPAY, "alipay-september.csv"),
            )
        ]
        fact = TransactionFact(
            fact_key="shared-import-fact",
            occurred_time=now,
            cash_direction=CASH_DIRECTION_OUT,
            amount=880,
            currency_code="CNY",
            account_code="wallet",
            counterparty_name="Merchant",
            counterparty_account_ref="",
            summary="Lunch",
            created_time=now,
            updated_time=now,
        )
        db.add_all([*files, fact])
        db.flush()
        db.add_all([
            TransactionImportRow(
                transaction_fact_id=fact.id,
                transaction_import_file_id=file.id,
                source_row_number=1,
                source_reference=f"ref-{file.id}",
                raw_payload="{}",
                raw_hash=str(file.id) * 64,
                row_status=IMPORT_ROW_STATUS_ACCEPTED,
                issue_code="",
                issue_message="",
                created_time=now,
                updated_time=now,
            )
            for file in files
        ])
        db.commit()
        return [file.id for file in files], fact.id


def test_import_file_list_is_a_pure_filterable_po_list(import_file_api):
    client, sessions = import_file_api
    _seed(sessions)

    response = client.get(
        "/paam/import/v1/import_file/list",
        params={
            "q": "september",
            "filter": '{"source_type":102,"status":1}',
            "sorter": '{"field":"filename","order":"asc"}',
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "wechat-september.csv"
    assert "account_codes" not in body["items"][0]
    assert set(body["items"][0]) >= {
        "batch_code", "sha256", "period_start", "period_end", "issue_count"
    }


def test_import_file_summary_uses_the_same_search_and_filter_contract(
    import_file_api,
):
    client, sessions = import_file_api
    _seed(sessions)

    response = client.get(
        "/paam/import/v1/import_file/summary",
        params={
            "q": "september",
            "filter": '{"source_type":102,"status":1}',
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == 200
    assert response.json()["body"] == {
        "import_file_count": 1,
        "imported_file_count": 1,
        "row_count": 1,
        "success_count": 1,
        "skip_count": 0,
        "issue_count": 0,
        "q": "september",
        "filter": {
            "source_type": 102,
            "file_format": None,
            "status": 1,
        },
    }


def test_import_file_detail_has_a_paged_transaction_fact_subresource(
    import_file_api,
):
    client, sessions = import_file_api
    file_ids, fact_id = _seed(sessions)

    detail = client.get(f"/paam/import/v1/import_file/{file_ids[0]}")
    facts = client.get(
        f"/paam/import/v1/import_file/{file_ids[0]}/transaction_fact/list",
        params={"page": 1, "page_size": 10},
    )

    assert detail.status_code == 200
    assert detail.json()["body"]["import_file"]["id"] == file_ids[0]
    assert facts.status_code == 200
    assert facts.json()["body"]["total"] == 1
    assert facts.json()["body"]["items"][0]["id"] == fact_id


def test_import_file_query_validates_generic_objects(import_file_api):
    client, _sessions = import_file_api

    response = client.get(
        "/paam/import/v1/import_file/list",
        params={"filter": '{"account_code":"not-owned-by-import-file"}'},
    )

    assert response.status_code == 422
    assert response.json()["body"]["code"] == "LIST_QUERY_ERROR"


def test_import_file_detail_returns_not_found(import_file_api):
    client, _sessions = import_file_api

    response = client.get("/paam/import/v1/import_file/999")

    assert response.status_code == 404
    assert response.json()["status"] == 404
    assert response.json()["body"]["code"] == "INTAKE_ERROR"
