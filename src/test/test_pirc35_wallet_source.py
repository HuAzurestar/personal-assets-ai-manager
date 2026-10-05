"""Fictional wallet identities: own full header only, never funding tails."""
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend.core.source_account_identity import reliable_source
from backend.entity import LedgerAccountRef, LedgerAccount, LedgerAccountParty, LedgerEntry, ReviewCase, TransactionFact
from backend.parser.statement_parser import parse_statement
from test_pirc35_import_service import service, preview, choose, confirm


FIXTURES = Path(__file__).parent / "fixtures" / "pirc35"


def statement(provider, header):
    original = (FIXTURES / ("alipay-6.csv" if provider == "alipay" else "wechat-3.csv")).read_text(encoding="utf-8")
    lines = original.splitlines()
    return (lines[0] + "\n" + header + "\n" + "\n".join(lines[2:]) + "\n").encode()


@pytest.mark.parametrize("provider,header,identity", [
    ("alipay", "支付宝账户：mock@example.invalid", "mock@example.invalid"),
    ("alipay", "支付宝账户：13900000001", "13900000001"),
    ("wechat", "微信昵称：[Mock昵称]\n微信号：MockWallet001", "MockWallet001"),
])
def test_parser_full_own_identity_not_funding_card(provider, header, identity):
    content = statement(provider, header).replace("余额".encode(), "建设银行(3456)".encode())
    document = parse_statement(content, "mock-wallet.csv", source_timezone=ZoneInfo("Asia/Hong_Kong"))
    assert len(document["rows"]) == 24
    assert {reliable_source(row) for row in document["rows"]} == {(f"{provider}:statement-v1", identity)}


@pytest.mark.parametrize("provider,header", [
    ("alipay", "支付宝账户：Mock昵称"),
    ("alipay", "支付宝账户：139****0001"),
    ("alipay", "支付宝账户：m***@example.invalid"),
    ("wechat", "微信昵称：[MockWallet001]"),
    ("wechat", "微信昵称：[Mock昵称]\n微信号：Mock***001"),
    ("wechat", "微信昵称：[Mock昵称]\n微信号："),
])
def test_weak_header_never_becomes_reliable(provider, header):
    document = parse_statement(statement(provider, header), "mock-wallet.csv", source_timezone=ZoneInfo("Asia/Hong_Kong"))
    assert all(reliable_source(row) is None for row in document["rows"])
    assert all(row["source_account"]["identity_strength"] != "RELIABLE" for row in document["rows"])


@pytest.mark.parametrize("provider,header", [
    ("alipay", "支付宝账户：one@example.invalid\n支付宝账户：two@example.invalid"),
    ("wechat", "微信号：MockWallet001\n微信号：MockWallet002"),
])
def test_conflicting_own_headers_reject_whole_document(provider, header):
    with pytest.raises(ValueError, match="多个本方"):
        parse_statement(statement(provider, header), "mock-wallet.csv", source_timezone=ZoneInfo("Asia/Hong_Kong"))


@pytest.mark.parametrize("provider,header,identity", [
    ("alipay", "支付宝账户：mock@example.invalid", "mock@example.invalid"),
    ("wechat", "微信昵称：[Mock昵称]\n微信号：MockWallet001", "MockWallet001"),
])
def test_real_import_auto_creates_unassigned_ref_reuses_and_rolls_back(service, provider, header, identity):
    content = statement(provider, header)
    current = preview(service, "mock-wallet.csv", content=content)
    keys = sorted(service.store.get(current["token"]).rows)
    current = choose(service, current, keys[:1])

    def fault(stage):
        if stage == "before_commit":
            raise RuntimeError("fictional rollback")

    with pytest.raises(RuntimeError, match="fictional rollback"):
        confirm(service, current, keys[:1], fault=fault)
    for entity in (LedgerAccountRef, LedgerEntry, ReviewCase, TransactionFact):
        assert service.db.scalar(select(func.count()).select_from(entity)) == 0
    confirm(service, current, keys[:1])
    current = choose(service, service.current(current["token"]), keys[1:])
    confirm(service, current, keys[1:])
    ref = service.db.execute(select(LedgerAccountRef)).scalar_one()
    assert (ref.source_namespace, ref.source_identity, ref.identity_strength, ref.account_id) == (
        f"{provider}:statement-v1", identity, 1, 0)
    assert set(service.db.scalars(select(LedgerEntry.account_ref_id))) == {ref.id}
    for entity in (LedgerAccount, LedgerAccountParty):
        assert service.db.scalar(select(func.count()).select_from(entity)) == 0
    again = preview(service, "mock-wallet.csv", content=content)
    assert again["counts"]["processed"] == 24
    assert service.db.scalar(select(func.count()).select_from(LedgerAccountRef)) == 1


def test_reliable_source_rejects_wrong_provider_namespace_or_strength():
    for source_type, namespace, identity, strength in [
        ("wechat", "alipay:statement-v1", "one@example.invalid", "RELIABLE"),
        ("alipay", "alipay:statement-v1", "one@example.invalid", "WEAK"),
        ("wechat", "wechat:statement-v1", "Mock***001", "RELIABLE"),
        ("alipay", "alipay:statement-v1", "MockNickname", "RELIABLE"),
    ]:
        assert reliable_source(dict(source_type=source_type, source_account=dict(
            source_namespace=namespace, source_identity=identity, identity_strength=strength))) is None


def test_wallet_same_tail_different_full_identity_creates_distinct_refs(service):
    for index, identity in enumerate(("13900000001", "13910000001")):
        content = statement("alipay", f"支付宝账户：{identity}").replace(b"2024-", f"{2040 + index}-".encode())
        current = preview(service, "Mock wallet.csv", content=content)
        keys = sorted(service.store.get(current["token"]).rows)
        current = choose(service, current, keys)
        confirm(service, current, keys)
    refs = list(service.db.scalars(select(LedgerAccountRef)))
    assert len(refs) == 2 and {ref.source_identity for ref in refs} == {"13900000001", "13910000001"}
    assert service.db.scalar(select(func.count()).select_from(LedgerEntry)) == 48
    assert set(service.db.scalars(select(LedgerEntry.account_ref_id))) == {ref.id for ref in refs}
