import pytest

from backend.mapper.auto_tag_scan_mapper import (
    ProtectedScanSource,
    ScanPage,
    ScanTarget,
    ScanToken,
)
from backend.schema.llm_analysis import LlmResolvedSuggestion
from backend.service.llm_privacy_service import LlmPrivacyService


def _page(amount_mode=1):
    return ScanPage(
        token=ScanToken(1, 1, 1, 0),
        enabled=True,
        view_active=True,
        model_enabled=True,
        view_id=1,
        model_id=2,
        prompt="根据商户和摘要选择分类；订单号 ABC-12345678 不得外发",
        amount_mode=amount_mode,
        ledger_ids=(1,),
        active_ledger_ids=frozenset({1}),
        active_tag_states={1: ("unclassified",)},
        existing_request_ids=frozenset(),
        targets=(ScanTarget(2, "餐饮"), ScanTarget(3, "出行")),
    )


def test_protected_payload_removes_identifiers_and_uses_configured_band():
    privacy = LlmPrivacyService({
        "amount_bands": {"CNY": {"boundaries": [0, 3000, 10000]}},
    })
    payload = privacy.build_payload(_page(), ProtectedScanSource(
        direction="OUT",
        amount=2900,
        currency_code="CNY",
        merchant="示例咖啡馆 卡号 6222 1234 5678 9999",
        summary="早餐 实付29.00元 https://example.test/order?id=12345678",
    ))
    assert payload is not None
    serialized = payload.model_dump_json(exclude={"item"})
    assert payload.source == "PROTECTED_LEDGER"
    assert payload.amount.band_label == "[0,3000)"
    assert "咖啡馆" in payload.merchant
    assert "早餐" in payload.summary
    for secret in ("6222", "29.00", "12345678", "https://"):
        assert secret not in serialized


def test_band_without_currency_config_omits_amount_and_none_removes_hints():
    privacy = LlmPrivacyService({"amount_bands": {}})
    payload = privacy.build_payload(_page(amount_mode=1), ProtectedScanSource(
        direction="OUT",
        amount=3500,
        currency_code="USD",
        merchant="餐厅",
        summary="高额聚餐",
    ))
    assert payload is not None
    assert payload.amount.mode == "NONE"

    none_payload = privacy.build_payload(_page(amount_mode=3), ProtectedScanSource(
        direction="OUT",
        amount=3500,
        currency_code="CNY",
        merchant="餐厅",
        summary="高额聚餐",
    ))
    assert none_payload is not None
    assert "高额" not in none_payload.summary
    assert "聚餐" in none_payload.summary


def test_model_reason_with_identifier_is_rejected():
    privacy = LlmPrivacyService()
    suggestion = LlmResolvedSuggestion(
        tag_id=2,
        tag_name="餐饮",
        reason="订单号 ABC-12345678 对应餐饮",
    )
    try:
        privacy.validate_suggestions((suggestion,), amount_mode=1)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe model reason was accepted")


@pytest.mark.parametrize("length", [500, 501, 8000])
def test_protected_text_is_bounded_after_sanitizing_without_changing_source(length):
    source = ProtectedScanSource(
        direction="OUT", amount=1000, currency_code="CNY",
        merchant="店" * 195 + " 订单号 ABC-12345678 " + "铺" * length,
        summary="茶" * 495 + " https://example.test/private " + "点" * length,
    )
    payload = LlmPrivacyService().build_payload(_page(), source)
    assert payload is not None
    assert len(payload.merchant) == 200
    assert len(payload.summary) == 500
    assert "ABC" not in payload.merchant
    assert "http" not in payload.summary
    assert "订单号 ABC-12345678" in source.merchant
    assert "https://example.test/private" in source.summary


@pytest.mark.parametrize("amount_mode", [1, 2, 3])
@pytest.mark.parametrize(("text", "forbidden"), [
    ("午餐 银行卡尾号7788 卡号6222-1234-5678-9999", ("7788", "6222", "9999")),
    ("咖啡 账号138****8899 联系邮箱 somebody@example.test", ("138", "8899", "somebody")),
    ("午餐 实付￥29.00 / 2,900分 / 二十九元", ("29", "2,900", "二十九")),
    ("午餐 原价39减10，余额8765.43，手续费0.50", ("39", "10", "8765", "0.50")),
    ("午餐 商品29.00；验证码 ABX9；二维码 weixin://private-token", ("29.00", "ABX9", "private-token")),
    ("矿泉水500mL；设备序列号 ZXCV123456；订单号 20260926ABC", ("ZXCV", "20260926")),
    ("午餐；姓名张三；门牌示例路88号", ("张三", "示例路", "88")),
    ("午餐；卡号６２２２１２３４５６７８９９９９；实付２９．００", ("6222", "29.00", "６２２２")),
    ("午餐；卡号6222\u200b1234\u200b56789999", ("6222", "1234", "9999")),
])
def test_all_text_channels_remove_identity_codes_and_unstructured_money(amount_mode, text, forbidden):
    from dataclasses import replace

    page = replace(_page(amount_mode), prompt=text, targets=(ScanTarget(2, text),))
    payload = LlmPrivacyService().build_payload(page, ProtectedScanSource(
        "OUT", 2900, "CNY", text, text,
    ))
    assert payload is not None
    for value in (payload.merchant, payload.summary, payload.rule_prompt, payload.candidates[0].name):
        assert all(secret not in value for secret in forbidden), value
        if "500mL" in text:
            assert "500mL" in value


@pytest.mark.parametrize("person", ["张三", "李四", "Alice Smith", "张\u200b三", "个人收款人"])
def test_known_personal_counterparty_is_not_sent_in_any_text_channel(person):
    from dataclasses import replace

    page = replace(_page(), prompt=f"给{person}的午餐", targets=(ScanTarget(2, f"{person}餐饮"),))
    payload = LlmPrivacyService().build_payload(page, ProtectedScanSource(
        "OUT", 2900, "CNY", person, f"{person} 午餐",
    ))
    assert payload is not None and payload.merchant == ""
    normalized = LlmPrivacyService._normalize_text(person)
    assert normalized not in payload.model_dump_json(exclude={"item"})
    assert "午餐" in payload.summary


def test_identity_only_input_does_not_call_for_classification():
    assert LlmPrivacyService().build_payload(_page(), ProtectedScanSource(
        "OUT", 2900, "CNY", "张三", "张三",
    )) is None


@pytest.mark.parametrize("amount_mode", [1, 2, 3])
def test_reason_rejects_masked_identity_and_bare_amounts(amount_mode):
    privacy = LlmPrivacyService()
    for reason in ("支付29.00用于午餐", "账号138****8899消费", "姓名张三午餐", "餐费2,900分"):
        with pytest.raises(ValueError):
            privacy.validate_suggestions((LlmResolvedSuggestion(
                tag_id=2, tag_name="餐饮", reason=reason,
            ),), amount_mode=amount_mode)
