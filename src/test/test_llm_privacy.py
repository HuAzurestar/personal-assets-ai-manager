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
    serialized = payload.model_dump_json()
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
