"""PIRC-24 DEV-004: 12 approved fragments and PRIV-01..16, all fictional.

The contract fixtures describe business fragments, not a wire DTO. Unknown
merchant names become safe categories; source parsing remains the Fact layer's
job. Tests feed only normalized fields, never raw rows to the model boundary.
"""
from __future__ import annotations

import json
import traceback
import unicodedata
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import monotonic

import pytest
from pydantic import ValidationError

from backend.error import LlmAdapterError
from backend.mapper.auto_tag_scan_mapper import ProtectedScanSource, ScanTarget
from backend.schema.llm_analysis import ProtectedLlmAnalysisInput
from backend.schema.setting import AutomationModelWrite
from backend.service.llm_adapter import LiteLlmAdapter, build_litellm_request, build_messages, parse_business_output
from backend.service.llm_privacy_service import LlmPrivacyService
from test_llm_adapter import _profile, _provider
from test_llm_privacy import _page

CONTRACT = json.loads((Path(__file__).parent / "fixtures/pirc24_privacy_cases.json").read_text(encoding="utf-8"))
POLICY = {
    "date_granularity": CONTRACT["date_granularity"],
    "amount_bands": {code: {"boundaries": value["boundaries"]} for code, value in CONTRACT["amount_bands"].items()},
}


def _input(text="午餐", *, merchant="餐馆", amount=2900, currency="CNY", direction="OUT", channel=""):
    return ProtectedScanSource(direction, amount, currency, merchant, text, datetime(2030, 1, 2, 12, 34, 56), channel)


def _payload(source=None, mode=1, policy=POLICY, page=None):
    return LlmPrivacyService(policy).build_payload(page or _page(mode), source or _input())


def _user(payload):
    return json.loads(build_messages(payload)[1]["content"])


def _business_json(user):
    # The independently generated correlation nonce may coincidentally contain
    # a few digits from a phone/account; inspect EVERY source-derived field.
    return json.dumps({key: value for key, value in user.items() if key != "item"}, ensure_ascii=False)


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("mode_name,mode", [("BAND", 1), ("EXACT", 2), ("NONE", 3)])
def test_twelve_approved_source_fragments(case, mode_name, mode):
    raw = case["input"]
    expected = case["expected_by_mode"][mode_name]
    money = case["normalized_money"]
    description = raw.get("商品") or raw.get("摘要", "")
    # Source-specific product/summary selection is explicit, not an all-row dump.
    if "商品" not in raw:
        description += "，" + raw.get("备注", raw.get("附言", ""))
    source = _input(
        description, merchant=raw.get("交易对方", ""), amount=money["amount"],
        direction="IN" if money["direction"] == "INCOME" else "OUT",
        channel=raw.get("支付方式", raw.get("收/付款方式", "")),
    )
    source = replace(source, occurred_time=datetime.fromisoformat(
        raw.get("交易时间", raw.get("交易日期", raw.get("记账日期"))),
    ))
    payload = _payload(source, mode)
    user = _user(payload)
    assert user["summary"] == unicodedata.normalize("NFKC", expected["description"])
    assert user["date"] == expected["date"]
    assert user["direction"] == source.direction
    assert user["currency_code"] == expected["currency_code"]
    if "merchant" in expected:
        assert user["merchant"] in expected["merchant"] and user["merchant"]
    else:
        assert user["merchant"] == ""
    assert user.get("payment_channel") == expected.get("payment_channel")
    safe = _business_json(user)
    assert all(value not in safe for value in case["forbidden_in_expected"])
    assert not {"ledger_id", "tag_id", "account", "balance", "source_shape"} & user.keys()
    if mode == 1:
        band = expected["amount_band"]
        high = band["upper_exclusive"]
        assert user["amount_band"] == f'[{band["lower_inclusive"]},{high if high is not None else "+∞"})'
        assert "amount_units" not in user
    elif mode == 2:
        assert user["amount_units"] == expected["amount"]
        assert "amount_band" not in user
        assert str(expected["amount"]) not in build_messages(payload)[0]["content"]
    else:
        assert not any(key.startswith("amount") for key in user)


@pytest.mark.parametrize("card", ["卡号6222 1234 5678 7788", "卡号6222-1234-5678-7788", "卡号6222****7788", "尾号7788"])
def test_priv01_card_category_only(card):
    payload = _payload(_input(f"午餐 银行卡 {card}", channel=f"银行卡({card})"))
    user = _user(payload)
    assert user["payment_channel"] == "银行卡"
    assert "7788" not in _business_json(user)


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_priv02_all_text_channels_and_unknown_extras(mode):
    text = "午餐；订单号 ABC-QZX；未标记身份虚构用户甲；原价39减10；二十九元"
    page = replace(_page(mode), prompt=text, targets=(ScanTarget(2, text),))
    payload = _payload(_input(text, merchant=text), mode, page=page)
    user = _user(payload)
    assert all(secret not in json.dumps(user, ensure_ascii=False) for secret in ("ABC", "QZX", "虚构用户甲", "二十九"))
    for key in ("merchant", "summary", "rule_prompt"):
        assert "午餐" in user[key]
    assert "午餐" in user["candidates"][0]["name"]
    for channel in ("description", "history", "examples", "raw_row", "extra"):
        value = payload.model_dump()
        value[channel] = text
        with pytest.raises(ValidationError):
            ProtectedLlmAnalysisInput.model_validate(value)


def test_priv03_unlabelled_name_address_and_account(monkeypatch):
    # Make the old flaky substring assertion fail deterministically if it
    # accidentally inspects the opaque nonce again.
    monkeypatch.setattr("backend.service.llm_privacy_service.secrets.token_hex", lambda _: "1387788" + "a" * 25)
    payload = _payload(_input("个人转账，退还垫款；虚构用户乙；姓名张三；门牌示例路88号；13812345678；abc@example.test", merchant="张三"))
    user = _user(payload)
    assert user["summary"] == "个人转账,退还垫款"
    assert user["merchant"] == ""
    assert user["item"] == "item_1387788" + "a" * 25
    assert all(value not in _business_json(user) for value in ("用户乙", "张三", "示例路", "138", "example"))


@pytest.mark.parametrize("mode", [1, 2, 3], ids=["PRIV-04-BAND", "PRIV-05-EXACT", "PRIV-06-NONE"])
def test_priv04_05_06_money_has_one_authoritative_representation(mode):
    payload = _payload(_input("小额午餐 实付￥29.00 / 2,900分 / 二十九元；余额8765.43"), mode)
    user = _user(payload)
    assert user["summary"] == "午餐"
    assert all(secret not in _business_json(user) for secret in ("29.00", "2,900", "8765"))
    assert user.get("amount_units") == (2900 if mode == 2 else None)
    assert ("amount_band" in user) is (mode == 1)


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_priv07_arithmetic_and_auxiliary_money(mode):
    payload = _payload(_input("午餐 原价39元减10元；余额8765.43；手续费0.50；另一笔300元"), mode)
    assert _user(payload)["summary"] == "午餐"


def test_priv08_code_url_and_attachment_channels():
    payload = _payload(_input("午餐；https://example.test/?code=QZX；weixin://private/QZX；验证码QZX；兑换码QZX；二维码data:image/png;base64,QZX"))
    assert _user(payload)["summary"] == "午餐"


@pytest.mark.parametrize("amount,band", [(3000, "[3000,10000)"), (300000, "[300000,+∞)")])
def test_priv09_left_closed_boundaries(amount, band):
    assert _user(_payload(_input(amount=amount)))["amount_band"] == band


@pytest.mark.parametrize("currency,amount", [("USD", 2900), ("CNY_4", 290000)])
def test_priv10_unconfigured_currency_does_not_inherit_cny(currency, amount):
    source = _input(currency=currency, amount=amount)
    band = _payload(source)
    assert band.amount.mode == "NONE"
    assert band._privacy_warnings == ("AMOUNT_BAND_UNCONFIGURED",)
    assert _user(_payload(source, 2))["amount_units"] == amount
    with pytest.raises(ValueError):
        _payload(replace(source, currency_code="UNKNOWN"))


@pytest.mark.parametrize("amount,band", [(0, "[0,3000)"), (-3000, "[3000,10000)")])
def test_priv11_signed_amount_and_independent_direction(amount, band):
    source = _input("退款", amount=amount, direction="IN")
    assert _user(_payload(source))["amount_band"] == band
    exact = _user(_payload(source, 2))
    assert (exact["amount_units"], exact["direction"]) == (amount, "IN")


def test_priv12_specification_not_arbitrary_numeric_code():
    payload = _payload(_input("矿泉水500mL；设备序列号ZXCV123456；未知XYZ500mL；SKU-A500mL"))
    user = _user(payload)
    assert user["summary"] == "矿泉水500mL"
    assert user["currency_code"] == "CNY"


@pytest.mark.parametrize("unknown", ["未标記用戶甲", "QZX-ABCD", "虚构用户乙", "xyz500mL", "地址未知", "店铺未知名字"])
def test_priv13_unknown_fragments_never_become_safe_by_regex_absence(unknown):
    assert _payload(_input(unknown, merchant=unknown)) is None
    mixed = _payload(_input(unknown + "；午餐", merchant=unknown))
    assert unknown not in mixed.model_dump_json()


@pytest.mark.parametrize("reason", ["账号6222****7788对应餐饮", "张三午餐", "午餐小额消费", "交易金额二十九元", "午餐；未标記用戶乙"])
def test_priv14_one_unsafe_reason_rejects_entire_response(reason):
    payload = _payload(mode=3)
    value = {"item": payload.item, "decision": "suggestion", "suggestions": [
        {"tag": "t1", "reason": "午餐语义支持餐饮。"}, {"tag": "t2", "reason": reason},
    ]}
    with pytest.raises(LlmAdapterError) as caught:
        parse_business_output(json.dumps(value), payload)
    assert caught.value.code == "OUTPUT_SEMANTIC_INVALID"
    assert reason not in str(caught.value.details)


def test_priv15_purpose_not_a_hard_coded_amount_category():
    meal = _user(_payload(_input("高额聚餐3500元", amount=350000)))
    stationery = _user(_payload(_input("小额文具29元", merchant="文具店")))
    assert meal["summary"] == "聚餐"
    assert stationery["summary"] == "文具"
    assert meal["candidates"] == stationery["candidates"]


def test_priv16_immutable_configuration_snapshot_and_date_policy():
    policy = deepcopy(POLICY)
    first = LlmPrivacyService(policy)
    policy["amount_bands"]["CNY"]["boundaries"] = [0, 2000, 10000]
    policy["date_granularity"] = "MONTH"
    old = first.build_payload(_page(), _input())
    new = _payload(policy=policy)
    assert _user(old)["amount_band"] == "[0,3000)"
    assert _user(new)["amount_band"] == "[2000,10000)"
    assert _user(old)["date"] == "2030-01-02"
    assert _user(new)["date"] == "2030-01"
    policy["date_granularity"] = "NONE"
    assert "date" not in _user(_payload(policy=policy))


def test_payload_integrity_and_nonce_prevent_raw_or_post_transform_bypass():
    first, second = _payload(), _payload()
    assert first.item != second.item
    assert len(first.item.removeprefix("item_")) == 32
    for invalid in (
        ProtectedLlmAnalysisInput.model_validate(first.model_dump()),
        first.model_copy(update={"summary": "SECRET_PRIVATE_SOURCE"}),
        first.model_copy(update={"amount": first.amount.model_copy(update={"currency_code": "PRIVATE"})}),
    ):
        with pytest.raises(LlmAdapterError):
            build_messages(invalid)
    with pytest.raises(LlmAdapterError):
        LiteLlmAdapter(lambda **_: pytest.fail("provider called")).analyze_synthetic(first, _profile(), api_key="test")
    assert "privacy_seal" not in first.model_dump_json()


@pytest.mark.parametrize("extra", [
    {"metadata": {"identity": "PRIVATE"}}, {"extra_body": {"prompt": "PRIVATE"}},
    {"extra_body": {"notes": "PRIVATE"}}, {"success_callback": []},
    {"api_base": "https://example.test/v1?account=PRIVATE"},
    {"extra_body": {"model": "openai/paid-fallback"}}, {"log_raw_request_response": True},
])
def test_provider_parameters_have_no_text_or_logging_bypass(extra):
    value = _profile().model_dump()
    value["litellm_params"].update(extra)
    with pytest.raises(ValidationError):
        AutomationModelWrite.model_validate(value)
    forged = _profile().model_copy(update={"litellm_params": _profile().litellm_params.model_copy(update=extra)})
    with pytest.raises(LlmAdapterError):
        build_litellm_request(_payload(), forged)


def test_whole_response_errors_and_exception_traces_never_echo_values(caplog):
    payload = _payload()
    secret = "PRIVATE_API_KEY"
    adapter = LiteLlmAdapter(lambda **_: _provider(json.dumps({
        "item": payload.item, "decision": "insufficient", "suggestions": [], "PRIVATE_EXTRA_KEY": "PRIVATE_VALUE",
    })))
    with pytest.raises(LlmAdapterError) as caught:
        adapter.analyze_protected(payload, _profile(), api_key=secret)
    diagnostic = "".join(traceback.format_exception(caught.value))
    assert caught.value.details["field_path"] == "output"
    assert not any(secret in diagnostic + caplog.text for secret in ("PRIVATE_EXTRA_KEY", "PRIVATE_VALUE", "PRIVATE_API_KEY"))
    def fail(**_):
        raise RuntimeError("PRIVATE_PROVIDER_BODY")
    with pytest.raises(LlmAdapterError) as provider_error:
        LiteLlmAdapter(fail).analyze_protected(payload, _profile(), api_key=secret)
    assert "PRIVATE_PROVIDER_BODY" not in "".join(traceback.format_exception(provider_error.value))


def test_long_unknown_text_is_bounded_without_partial_identifier_leaks():
    started = monotonic()
    source = _input("午餐" * 12000 + "；订单号 ABC12345678")
    payload = _payload(source)
    assert len(payload.summary) == 500
    assert "ABC" not in payload.summary
    # Wide margin: old unanchored email/address regex took tens of seconds.
    assert monotonic() - started < 5
    assert _payload(_input("未知" * 20000, merchant="未知" * 20000)) is None


@pytest.mark.parametrize("boundaries", [[True, 100], [0, 100, 50], [100], [0, "100"], [0, 0]])
def test_corrupted_disclosure_is_not_silently_reinterpreted(boundaries):
    with pytest.raises(ValidationError):
        LlmPrivacyService({"amount_bands": {"CNY": {"boundaries": boundaries}}})
