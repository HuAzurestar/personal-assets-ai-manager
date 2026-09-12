import base64
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import xlwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import database, main, target_database
from app.core.intake_preview_store import target_intake_preview_store
from app.database import Bill, ImportEvidence, ImportPreview, Account
from app.models.target import (
    BillFact,
    BillRaw,
    ImportFile,
    LedgerEntry,
    LedgerEntrySource,
    ReviewCase,
    ReviewHistory,
)
from app.statement_parser import parse_statement, normalise_statement_row


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'smart.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    target_intake_preview_store.clear()
    with TestClient(main.app) as client:
        yield client, sessions
    target_intake_preview_store.clear()
    engine.dispose()


def csv_bytes(
    reference="ref-1",
    amount="10.00",
    note="商品",
    merchant="商户",
    extra=True,
    status="支付成功",
    direction="支出",
):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["微信支付账单明细"])
    header = [
        "交易时间",
        "交易对方",
        "金额(元)",
        "收/支",
        "交易单号",
        "当前状态",
        "支付方式",
    ]
    values = [
        "2026-08-01 12:00:00",
        merchant,
        amount,
        direction,
        reference,
        status,
        "零钱",
    ]
    if extra:
        header.append("商品")
        values.append(note)
    writer.writerow(header)
    writer.writerow(values)
    return stream.getvalue().encode()


def csv_many(count: int, reference_prefix: str) -> bytes:
    lines = csv_bytes().decode().splitlines()
    records = [
        lines[2].replace("ref-1", f"{reference_prefix}-{index}")
        for index in range(count)
    ]
    return ("\n".join(lines[:2] + records) + "\n").encode()


def upload(client, items):
    response = client.post(
        "/api/intake/preview",
        json={
            "files": [
                {"filename": name, "content_base64": base64.b64encode(content).decode()}
                for name, content in items
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, preview):
    return client.post(
        f"/api/intake/{preview['token']}/confirm", json={"version": preview["version"]}
    )


def target_upload(client, items):
    response = client.post(
        "/paam/import/v1/preview",
        json={
            "files": [
                {"filename": name, "content_base64": base64.b64encode(content).decode()}
                for name, content in items
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["body"]


def target_confirm(client, preview):
    return client.post(
        f"/paam/import/v1/preview/confirm/{preview['token']}",
        json={"version": preview["version"]},
    )


def test_versioned_intake_api_uses_envelope_and_bounded_preview_queries(ledger):
    client, sessions = ledger
    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.post(
            "/paam/import/v1/preview",
            json={
                "files": [{
                    "filename": "wechat.csv",
                    "content_base64": base64.b64encode(csv_bytes()).decode(),
                }]
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["body"]["counts"]["new"] == 1
    assert len(statements) <= 8
    assert all("SELECT *" not in statement.upper() for statement in statements)


def test_versioned_intake_writes_only_pirc9_fact_tables(ledger):
    client, sessions = ledger
    preview_response = client.post(
        "/paam/import/v1/preview",
        json={
            "files": [{
                "filename": "wechat.csv",
                "content_base64": base64.b64encode(csv_bytes()).decode(),
            }]
        },
    )
    preview = preview_response.json()["body"]
    confirmed = client.post(
        f"/paam/import/v1/preview/confirm/{preview['token']}",
        json={"version": preview["version"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["body"]
    assert result["counts"]["new"] == 1

    with sessions() as db:
        assert db.query(ImportFile).count() == 1
        assert db.query(BillRaw).count() == 1
        assert db.query(BillFact).count() == 1
        assert db.query(LedgerEntry).count() == 1
        assert db.query(LedgerEntrySource).count() == 1
        assert db.query(Bill).count() == 0
        assert db.query(ImportEvidence).count() == 0
        fact = db.scalar(select(BillFact))
        assert fact.amount_value == 1000
        assert fact.amount_scale == 2
        assert fact.cash_direction == "OUT"

    ledger_page = client.get("/api/shadow/v1/ledger/entries").json()
    assert ledger_page["total"] == 1
    assert ledger_page["items"][0]["ledger_type"] == "EXPENSE"
    assert ledger_page["items"][0]["outgoing"]["amount_value"] == 1000

    history = client.get("/paam/import/v1/batch/list").json()["body"]
    assert history[0]["id"] == result["import_file_ids"][0]
    raw_rows = client.get(
        "/paam/import/v1/batch/row/list",
        params={"batch_id": result["import_file_ids"][0]},
    ).json()["body"]
    assert len(raw_rows) == 1
    assert raw_rows[0]["bill_id"] == result["bill_fact_ids"][0]

    replay = client.post(
        "/paam/import/v1/preview",
        json={
            "files": [{
                "filename": "wechat-again.csv",
                "content_base64": base64.b64encode(csv_bytes()).decode(),
            }]
        },
    ).json()["body"]
    assert replay["counts"]["duplicate_file"] == 1
    repeated = client.post(
        f"/paam/import/v1/preview/confirm/{replay['token']}",
        json={"version": replay["version"]},
    )
    assert repeated.status_code == 200
    with sessions() as db:
        assert db.query(ImportFile).count() == 1
        assert db.query(BillRaw).count() == 1
        assert db.query(BillFact).count() == 1
        assert db.query(LedgerEntry).count() == 1
        assert db.query(LedgerEntrySource).count() == 1


def test_target_default_projection_does_not_reuse_fact_primary_key(ledger):
    client, sessions = ledger
    occurred = datetime(2026, 7, 1, 12)
    with sessions() as db:
        db.add(LedgerEntry(
            id=1,
            ledger_type="LOAN_BORROW",
            allocation_status="CONFIRMED",
            title="existing review projection",
            start_time=occurred,
            end_time=occurred,
            in_amount_value=100,
            in_amount_scale=2,
            in_currency_code="CNY",
            out_amount_value=0,
            out_amount_scale=2,
            out_currency_code="CNY",
            in_account_code="wallet",
            out_account_code="UNKNOWN",
            input_hash="review-hash",
            projection_version=1,
        ))
        db.add(LedgerEntrySource(
            ledger_id=1,
            source_kind="REVIEW_CASE",
            source_id=99,
        ))
        db.commit()

    preview = target_upload(client, [("wechat.csv", csv_bytes())])
    response = target_confirm(client, preview)
    assert response.status_code == 200, response.text
    with sessions() as db:
        fact = db.scalar(select(BillFact))
        fact_source = db.scalar(select(LedgerEntrySource).where(
            LedgerEntrySource.source_kind == "BILL_FACT"
        ))
        assert fact.id == 1
        assert fact_source.ledger_id != fact.id
        assert db.query(LedgerEntry).count() == 2


def test_target_intake_supplements_raw_evidence_and_blocks_fact_conflicts(ledger):
    client, sessions = ledger

    def target_preview(content: bytes):
        response = client.post(
            "/paam/import/v1/preview",
            json={
                "files": [{
                    "filename": "wechat.csv",
                    "content_base64": base64.b64encode(content).decode(),
                }]
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["body"]

    first = target_preview(csv_bytes(note="first evidence"))
    assert client.post(
        f"/paam/import/v1/preview/confirm/{first['token']}",
        json={"version": first["version"]},
    ).status_code == 200

    richer = target_preview(csv_bytes(note="richer evidence", extra=True))
    assert richer["counts"]["supplement"] == 1
    assert client.post(
        f"/paam/import/v1/preview/confirm/{richer['token']}",
        json={"version": richer["version"]},
    ).status_code == 200
    with sessions() as db:
        assert db.query(BillFact).count() == 1
        assert db.query(BillRaw).count() == 2
        assert db.query(ImportFile).count() == 2

    conflict = target_preview(csv_bytes(amount="20.00", note="conflict"))
    assert conflict["can_confirm"]
    assert conflict["counts"]["error"] == 1
    recorded = client.post(
        f"/paam/import/v1/preview/confirm/{conflict['token']}",
        json={"version": conflict["version"]},
    )
    assert recorded.status_code == 200, recorded.text
    with sessions() as db:
        assert db.query(BillFact).count() == 1
        assert db.query(BillRaw).count() == 3
        assert db.query(ImportFile).count() == 3
        issue = db.scalar(select(BillRaw).where(BillRaw.parse_status == "INVALID"))
        assert issue.bill_id == 0
        assert issue.issue_code == "FACT_CONFLICT"
        assert issue.issue_message
        conflict_case = db.scalar(select(ReviewCase).where(
            ReviewCase.review_type == "FACT_CONFLICT"
        ))
        assert conflict_case.status == "PENDING"
        assert json.loads(conflict_case.result_json)["bill_raw_id"] == issue.id
        assert db.query(ReviewHistory).filter_by(case_id=conflict_case.id).count() == 1
        case_id = conflict_case.id
        existing_fact_id = db.scalar(select(BillFact.id))

    dismissed = client.post(f"/paam/review/v1/fact-conflict/dismiss/{case_id}", json={
        "expected_version": 1,
        "reason": "not a new transaction",
        "idempotency_key": "conflict-dismiss",
    })
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["body"]["status"] == "REJECTED"
    reopened = client.post(f"/paam/review/v1/fact-conflict/reopen/{case_id}", json={
        "expected_version": 2,
        "reason": "needs another check",
        "idempotency_key": "conflict-reopen",
    })
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["body"]["history"][-1]["reverses_history_id"] > 0
    resolved = client.post(f"/paam/review/v1/fact-conflict/resolve/{case_id}", json={
        "resolution_type": "LINK_EXISTING",
        "existing_bill_id": existing_fact_id,
        "expected_version": 3,
        "reason": "same immutable transaction; retain extra evidence",
        "idempotency_key": "conflict-resolve",
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["body"]["status"] == "CONFIRMED"
    assert resolved.json()["body"]["lines"][0]["role"] == "FACT_ACCEPTED"
    with sessions() as db:
        issue = db.scalar(select(BillRaw).where(BillRaw.issue_code == "FACT_CONFLICT"))
        assert issue.parse_status == "SUCCESS"
        assert issue.bill_id == existing_fact_id
        assert db.query(BillFact).count() == 1


def test_target_fact_conflict_can_be_accepted_as_a_distinct_fact(ledger):
    client, sessions = ledger
    first = target_upload(client, [("wechat.csv", csv_bytes(note="first"))])
    assert target_confirm(client, first).status_code == 200
    conflict = target_upload(client, [(
        "wechat.csv",
        csv_bytes(amount="20.00", note="actually separate"),
    )])
    assert target_confirm(client, conflict).status_code == 200
    with sessions() as db:
        case_id = db.scalar(select(ReviewCase.id).where(
            ReviewCase.review_type == "FACT_CONFLICT"
        ))
    resolved = client.post(f"/paam/review/v1/fact-conflict/resolve/{case_id}", json={
        "resolution_type": "CREATE_NEW",
        "expected_version": 1,
        "reason": "verified as a separate transaction",
        "idempotency_key": "conflict-create-new",
    })
    assert resolved.status_code == 200, resolved.text
    new_fact_id = resolved.json()["body"]["lines"][0]["bill_id"]
    with sessions() as db:
        facts = db.scalars(select(BillFact).order_by(BillFact.id)).all()
        assert [fact.amount_value for fact in facts] == [1000, 2000]
        assert new_fact_id == facts[1].id
        assert db.query(LedgerEntry).count() == 2
        conflict_raw = db.scalar(select(BillRaw).where(
            BillRaw.issue_code == "FACT_CONFLICT"
        ))
        assert conflict_raw.bill_id == new_fact_id
        assert conflict_raw.parse_status == "SUCCESS"


def test_confirm_select_count_does_not_grow_per_import_row(ledger):
    client, sessions = ledger
    engine = sessions.kw["bind"]

    def confirm_select_count(content: bytes) -> int:
        preview = upload(client, [("wechat.csv", content)])
        statements = []

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            response = confirm(client, preview)
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        assert response.status_code == 200, response.text
        assert all("SELECT *" not in statement.upper() for statement in statements)
        return len(statements)

    one_row = confirm_select_count(csv_many(1, "one"))
    twenty_rows = confirm_select_count(csv_many(20, "many"))
    assert twenty_rows <= one_row + 2


def test_target_confirm_select_count_does_not_grow_per_import_row(ledger):
    client, sessions = ledger
    engine = sessions.kw["bind"]

    def target_confirm_select_count(content: bytes) -> int:
        preview_response = client.post(
            "/paam/import/v1/preview",
            json={
                "files": [{
                    "filename": "wechat.csv",
                    "content_base64": base64.b64encode(content).decode(),
                }]
            },
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["body"]
        statements = []

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            response = client.post(
                f"/paam/import/v1/preview/confirm/{preview['token']}",
                json={"version": preview["version"]},
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        assert response.status_code == 200, response.text
        assert all("SELECT *" not in statement.upper() for statement in statements)
        return len(statements)

    one_row = target_confirm_select_count(csv_many(1, "target-one"))
    twenty_rows = target_confirm_select_count(csv_many(20, "target-many"))
    assert twenty_rows <= one_row + 2


def test_target_projection_classifies_refund_without_counting_it_as_income(ledger):
    client, sessions = ledger
    preview = target_upload(client, [(
        "refund.csv",
        csv_bytes(reference="refund", status="退款成功", direction="收入"),
    )])
    assert preview["can_confirm"]
    response = target_confirm(client, preview)
    assert response.status_code == 200, response.text

    page = client.get("/api/shadow/v1/ledger/entries").json()
    assert page["total"] == 1
    assert page["items"][0]["ledger_type"] == "REFUND"
    assert page["items"][0]["incoming"]["amount_value"] == 1000


def test_target_projection_keeps_neutral_bank_flow_unresolved(ledger):
    client, sessions = ledger
    preview = target_upload(client, [(
        "ccb.xls",
        ccb_file(rows=[
            ["1", "基金申购", "20260801", "-10.00", "90.00", "基金账户"],
        ]),
    )])
    assert preview["can_confirm"]
    response = target_confirm(client, preview)
    assert response.status_code == 200, response.text

    page = client.get("/api/shadow/v1/ledger/entries").json()
    assert page["total"] == 1
    assert page["items"][0]["ledger_type"] == "UNRESOLVED"
    assert page["items"][0]["allocation_status"] == "PARTIAL"


def test_auto_source_optional_fields_and_complementary_evidence(ledger):
    client, sessions = ledger
    first = upload(client, [("unknown.csv", csv_bytes())])
    assert first["documents"][0]["source_type"] == "wechat"
    assert first["counts"]["new"] == 1
    assert confirm(client, first).status_code == 200
    second = upload(
        client,
        [
            ("fewer.csv", csv_bytes(extra=False)),
            ("more.csv", csv_bytes(merchant="商户的完整名称")),
        ],
    )
    assert second["counts"]["supplement"] == 2
    assert confirm(client, second).status_code == 200
    with sessions() as db:
        assert db.query(Bill).count() == 1
        assert db.query(ImportEvidence).count() == 3
        assert db.scalar(select(Bill)).amount == -10
        assert db.scalar(select(Bill)).account_id
    source = client.get("/api/transactions/1/source").json()
    assert len(source["evidence"]) == 3


def test_identity_conflict_blocks_entire_batch(ledger):
    client, sessions = ledger
    initial = upload(client, [("one.csv", csv_bytes())])
    assert confirm(client, initial).status_code == 200
    bad = upload(
        client,
        [
            ("new.csv", csv_bytes(reference="ref-new")),
            ("conflict.csv", csv_bytes(amount="11")),
        ],
    )
    assert not bad["can_confirm"]
    assert confirm(client, bad).status_code == 422
    with sessions() as db:
        assert db.query(Bill).count() == 1


def test_closed_and_refund_preserve_semantics(ledger):
    client, sessions = ledger
    preview = upload(
        client,
        [
            ("closed.csv", csv_bytes(status="交易关闭")),
            ("paid.csv", csv_bytes(reference="paid", status="已全额退款")),
            (
                "refund.csv",
                csv_bytes(reference="refund", status="退款成功", direction="收入"),
            ),
        ],
    )
    assert preview["can_confirm"]
    assert preview["counts"]["record"] == 1
    assert confirm(client, preview).status_code == 200
    with sessions() as db:
        assert db.query(ImportEvidence).count() == 3
        assert db.query(Bill).count() == 2
    summary = client.get("/api/dashboard").json()
    assert summary["income"] == 0
    assert summary["spending"] == -10


def test_idempotent_confirm_and_stale_preview(ledger):
    client, sessions = ledger
    preview = upload(client, [("one.csv", csv_bytes())])
    stale = upload(client, [("one.csv", csv_bytes())])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(client, preview), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()
    assert confirm(client, stale).status_code == 409
    with sessions() as db:
        assert db.query(Bill).count() == 1


def ccb_file(number="6227000012345678", extra=False, rows=None):
    book = xlwt.Workbook()
    sheet = book.add_sheet("明细")
    sheet.write(0, 0, "中国建设银行个人活期账户全部交易明细")
    sheet.write(1, 0, f"卡号/账号:{number}")
    headers = ["序号", "摘要", "交易日期", "交易金额", "账户余额", "对方账号与户名"]
    if extra:
        headers.append("交易地点/附言")
    for c, value in enumerate(headers):
        sheet.write(2, c, value)
    for i, row in enumerate(
        rows
        or [
            ["1", "消费", "20260801", "-10.00", "90.00", "对方"],
            ["2", "消费", "20260801", "-10.00", "80.00", "对方"],
        ]
    ):
        if extra:
            row = [*row, extra if isinstance(extra, str) else "补充附言"]
        for c, value in enumerate(row):
            sheet.write(i + 3, c, value)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def test_bank_multiset_and_different_accounts(ledger):
    client, sessions = ledger
    preview = upload(
        client,
        [
            ("hqmx.xls", ccb_file()),
            ("again.xls", ccb_file(extra=True)),
            ("card2.xls", ccb_file(number="6227000012349000")),
        ],
    )
    assert preview["can_confirm"]
    assert preview["counts"]["new"] == 4
    assert preview["counts"]["supplement"] == 2
    assert confirm(client, preview).status_code == 200
    with sessions() as db:
        assert db.query(Account).count() == 2
        assert db.query(Bill).count() == 4
        assert db.query(ImportEvidence).count() == 6
    evidence = client.get("/api/transactions/1/source").json()
    assert evidence["raw_fields"]["交易地点/附言"] == "补充附言"


def test_source_mismatch_is_not_silently_accepted():
    with pytest.raises(ValueError, match="来源不符"):
        parse_statement(csv_bytes(), "支付宝.csv", source="alipay")
    parsed = parse_statement(csv_bytes(), "支付宝.csv")
    assert parsed["source_type"] == "wechat"


def test_missing_identifier_requires_inline_match_decision(ledger):
    client, sessions = ledger
    initial = upload(client, [("one.csv", csv_bytes(reference=""))])
    assert confirm(client, initial).status_code == 200
    preview = upload(client, [("two.csv", csv_bytes(reference="", extra=False))])
    assert not preview["can_confirm"]
    changed = client.put(
        f"/api/intake/{preview['token']}/preview",
        json={"decisions": {"0:0": "match:1"}},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["can_confirm"]
    assert confirm(client, changed.json()).status_code == 200
    with sessions() as db:
        assert db.query(Bill).count() == 1


def test_exact_reupload_in_any_filename_is_noop(ledger):
    client, sessions = ledger
    initial = upload(client, [("one.csv", csv_bytes())])
    assert confirm(client, initial).status_code == 200
    preview = upload(client, [("renamed.csv", csv_bytes())])
    assert preview["counts"]["duplicate_file"] == 1
    result = confirm(client, preview)
    assert result.status_code == 200
    assert result.json()["counts"]["duplicate_file"] == 1
    assert result.json()["counts"].get("new", 0) == 0
    with sessions() as db:
        assert db.query(ImportEvidence).count() == 1


def test_wallet_withdrawal_direction_is_relative_to_funding_account():
    row = normalise_statement_row(
        "wechat",
        {
            "交易时间": "2026-08-01 12:00:00",
            "交易类型": "零钱通转出-到建设银行(5678)",
            "交易对方": "银行",
            "商品": "",
            "收/支": "/",
            "金额(元)": "100",
            "支付方式": "零钱通",
            "当前状态": "资金已到账",
            "交易单号": "out",
        },
        "profile",
        {},
    )
    assert row["amount_minor"] == -10000
    assert row["nature"] == "neutral"
    assert row["account"]["provider"] == "wechat"


@pytest.mark.parametrize("bank_first", [True, False])
@pytest.mark.parametrize("wallet_neutral", [True, False])
def test_cross_channel_same_card_separate_upload_order(
    ledger, bank_first, wallet_neutral
):
    client, sessions = ledger
    wallet = (
        csv_bytes(
            direction="/" if wallet_neutral else "支出",
            note="转入零钱通" if wallet_neutral else "商品",
        )
        .decode()
        .replace(",零钱\r\n", ",建设银行储蓄卡(5678)\r\n")
        .replace(",零钱,", ",建设银行储蓄卡(5678),")
        .encode()
    )
    bank = ccb_file(
        rows=[["1", "财付通-微信支付", "20260801", "-10.00", "90.00", "财付通"]]
    )
    files = [("bank.xls", bank), ("wallet.csv", wallet)]
    if not bank_first:
        files.reverse()
    for item in files:
        preview = upload(client, [item])
        assert preview["can_confirm"]
        assert confirm(client, preview).status_code == 200
    with sessions() as db:
        assert db.query(Bill).count() == 1
        assert db.query(ImportEvidence).count() == 2
        bill = db.scalar(select(Bill))
        assert db.get(Account, bill.account_id).number == "6227000012345678"
        assert bill.import_nature == ("neutral" if wallet_neutral else "ordinary")
    assert client.get("/api/transactions?scope=all&source=ccb").json()["total"] == 1
    assert client.get("/api/transactions?scope=all&source=wechat").json()["total"] == 1
    assert client.get("/api/dashboard").json()["spending"] == (
        0 if wallet_neutral else -10
    )


@pytest.mark.parametrize("bank_first", [True, False])
@pytest.mark.parametrize("wallet_neutral", [True, False])
def test_target_cross_channel_projection_is_order_independent(
    ledger, bank_first, wallet_neutral
):
    client, sessions = ledger
    wallet = (
        csv_bytes(
            direction="/" if wallet_neutral else "支出",
            note="转入零钱通" if wallet_neutral else "商品",
        )
        .decode()
        .replace(",零钱\r\n", ",建设银行储蓄卡(5678)\r\n")
        .replace(",零钱,", ",建设银行储蓄卡(5678),")
        .encode()
    )
    bank = ccb_file(
        rows=[["1", "财付通-微信支付", "20260801", "-10.00", "90.00", "财付通"]]
    )
    files = [("bank.xls", bank), ("wallet.csv", wallet)]
    if not bank_first:
        files.reverse()
    for item in files:
        preview = target_upload(client, [item])
        assert preview["can_confirm"]
        response = target_confirm(client, preview)
        assert response.status_code == 200, response.text

    with sessions() as db:
        assert db.query(BillFact).count() == 1
        assert db.query(BillRaw).count() == 2
        assert db.query(LedgerEntry).count() == 1
        entry = db.scalar(select(LedgerEntry))
        assert entry.ledger_type == ("UNRESOLVED" if wallet_neutral else "EXPENSE")
        assert entry.allocation_status == ("PARTIAL" if wallet_neutral else "DEFAULT")


def test_legacy_endpoint_cannot_duplicate_smart_import(ledger):
    client, sessions = ledger
    initial = upload(client, [("one.csv", csv_bytes())])
    assert confirm(client, initial).status_code == 200
    response = client.post(
        "/api/imports/wechat?filename=two.csv", content=csv_bytes(extra=False)
    )
    assert response.status_code == 201, response.text
    assert response.json()["supplemented_count"] == 1
    with sessions() as db:
        assert db.query(Bill).count() == 1


def test_smart_import_recognises_legacy_references_with_new_profile(ledger):
    client, sessions = ledger
    response = client.post("/api/imports/wechat?filename=one.csv", content=csv_bytes())
    assert response.status_code == 201, response.text
    content = csv_bytes(extra=False).replace(
        "微信支付账单明细".encode(), "微信支付账单明细\n微信昵称：[测试用户]".encode()
    )
    preview = upload(client, [("two.csv", content)])
    assert preview["counts"]["supplement"] == 1
    assert confirm(client, preview).status_code == 200
    with sessions() as db:
        assert db.query(Bill).count() == 1


def test_pdf_columns_keep_optional_customer_note_and_date_precision(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    def word(x, y, text):
        return {"x0": x, "x1": x + 25, "top": y, "bottom": y + 9, "text": text}

    headers = [
        "记账日期",
        "货币",
        "交易金额",
        "联机余额",
        "交易摘要",
        "对手信息",
        "客户摘要",
    ]
    xs = [35, 100, 155, 235, 310, 420, 480]
    words = [word(35, 30, "招商银行交易流水"), word(260, 60, "账号：6227000012345678")]
    words += [word(x, 100, h) for x, h in zip(xs, headers)]
    words += [word(155, 115, "Amount")]
    words += [
        word(x, 140, v)
        for x, v in zip(
            xs, ["2026-08-01", "CNY", "-10.00", "90.00", "消费", "商户", "补充摘要"]
        )
    ]
    page = SimpleNamespace(
        width=595,
        height=842,
        extract_text=lambda **kw: "招商银行交易流水",
        extract_words=lambda **kw: words,
    )
    monkeypatch.setattr(
        "app.statement_parser.pdfplumber.open",
        lambda stream: nullcontext(SimpleNamespace(pages=[page])),
    )
    parsed = parse_statement(b"%PDF-test", "statement.pdf")
    row = parsed["rows"][0]
    assert not row["error"]
    assert row["amount_minor"] == -1000
    assert row["balance_minor"] == 9000
    assert row["time_precision"] == "day"
    assert row["raw"]["客户摘要"] == "补充摘要"


def test_confirmed_account_mapping_is_reused(ledger):
    client, sessions = ledger
    bank = upload(client, [("bank.xls", ccb_file())])
    assert confirm(client, bank).status_code == 200
    preview = upload(client, [("wallet.csv", csv_bytes(amount="7"))])
    row = preview["documents"][0]["rows"][0]
    target = next(a for a in preview["accounts"] if a["provider"] == "ccb")
    changed = client.put(
        f"/api/intake/{preview['token']}/preview",
        json={"accounts": {row["detected_account_identity"]: target["identity"]}},
    ).json()
    assert changed["can_confirm"]
    assert confirm(client, changed).status_code == 200
    again = upload(client, [("next.csv", csv_bytes(reference="next", amount="8"))])
    assert again["documents"][0]["rows"][0]["account"]["identity"] == target["identity"]
    assert again["documents"][0]["rows"][0]["account_basis"] == "使用之前确认的账户匹配"


def test_old_sqlite_schema_migrates_without_rewriting_money(tmp_path, monkeypatch):
    from sqlalchemy import text, inspect

    engine = create_engine(
        f"sqlite:///{tmp_path / 'old.db'}", connect_args={"check_same_thread": False}
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE bills (id INTEGER PRIMARY KEY, occurred_at DATETIME, merchant TEXT, note TEXT, amount FLOAT, category TEXT, tags TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bills VALUES (1, '2026-08-01 12:00:00', '原始商户', '原始备注', -12.34, '未分类', '')"
            )
        )
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    with TestClient(main.app) as client:
        bill = client.get("/api/bills").json()[0]
        assert bill["amount"] == -12.34
        assert bill["note"] == "原始备注"
        assert {"account_id", "time_precision", "import_nature"} <= {
            column["name"] for column in inspect(engine).get_columns("bills")
        }
    engine.dispose()


def test_zip_password_is_not_needed_again_or_persisted(ledger):
    import pyzipper

    client, sessions = ledger
    stream = io.BytesIO()
    with pyzipper.AESZipFile(
        stream, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as archive:
        archive.setpassword(b"sample-password")
        archive.writestr("wechat.csv", csv_bytes())
    response = client.post(
        "/api/intake/preview",
        json={
            "files": [
                {
                    "filename": "sample.zip",
                    "content_base64": base64.b64encode(stream.getvalue()).decode(),
                    "password": "sample-password",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["can_confirm"]
    with sessions() as db:
        stage = db.scalar(select(ImportPreview).where(ImportPreview.token == preview["token"]))
        assert "sample-password" not in stage.payload_json + stage.plan_json
    assert confirm(client, preview).status_code == 200
    with sessions() as db:
        stage = db.scalar(select(ImportPreview).where(ImportPreview.token == preview["token"]))
        assert stage.payload_json == "[]"
        assert set(json.loads(stage.plan_json)) == {"version", "counts"}


@pytest.mark.parametrize("extension", ["xls", "xlsx"])
def test_corrupt_workbook_is_a_preview_error_not_server_failure(ledger, extension):
    client, sessions = ledger
    preview = upload(client, [(f"bad.{extension}", b"not a workbook")])
    assert not preview["can_confirm"]
    assert preview["documents"][0]["error"]
    with sessions() as db:
        assert db.query(Bill).count() == 0


def test_expired_preview_cannot_be_confirmed(ledger):
    from datetime import datetime, timedelta

    client, sessions = ledger
    preview = upload(client, [("one.csv", csv_bytes())])
    with sessions() as db:
        stage = db.scalar(select(ImportPreview).where(ImportPreview.token == preview["token"]))
        stage.created_time = datetime.now() - timedelta(days=2)
        db.commit()
    assert confirm(client, preview).status_code == 409
    with sessions() as db:
        assert db.query(Bill).count() == 0


@pytest.mark.parametrize("reverse", [True, False])
def test_optional_bank_detail_enriches_nature_independent_of_order(ledger, reverse):
    client, sessions = ledger
    files = [("simple.xls", ccb_file()), ("detailed.xls", ccb_file(extra="基金申购"))]
    if reverse:
        files.reverse()
    for file in files:
        preview = upload(client, [file])
        assert confirm(client, preview).status_code == 200
    with sessions() as db:
        assert db.query(Bill).count() == 2
        assert all(
            b.import_nature == "neutral" and b.aggregate_excluded
            for b in db.scalars(select(Bill)).all()
        )


def test_review_rebuild_preserves_import_neutrality(ledger):
    client, sessions = ledger
    preview = upload(client, [("bank.xls", ccb_file(extra="基金申购"))])
    assert confirm(client, preview).status_code == 200
    with sessions() as db:
        ids = set(db.scalars(select(Bill.id)).all())
        main._rebuild_review_effects(db, ids)
        db.commit()
        assert all(b.aggregate_excluded for b in db.scalars(select(Bill)).all())
