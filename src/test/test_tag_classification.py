"""Fictional scenario contract tests, NOT an LLM accuracy benchmark."""

import json
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from backend.error import LlmAdapterError
from backend.mapper.auto_tag_scan_mapper import ScanTarget
from backend.schema.auto_tag_rule import AutoTagRuleCreateRequest
from backend.schema.target_tag import TargetTagCreateRequest, TargetTagViewCreateRequest
from backend.service.llm_adapter import build_litellm_request, parse_business_output
from backend.service.llm_privacy_service import LlmPrivacyService
from test_llm_adapter import _profile
from test_llm_privacy import _page
from test_privacy_contract import POLICY, _input

DEFINITION = json.loads((Path(__file__).parents[1] / "asset/tag/classification.json").read_text(encoding="utf-8"))
VIEWS = DEFINITION["views"]
# merchant, summary, required evidence, proposed purpose, proposed channel.
# Expected labels document business intent. The parser assertions below use
# authored responses and do not pretend to measure a provider's decisions.
CASES = [
    ("虚构医院", "门店挂号", "挂号", "medical", "offline"),
    ("京东", "药品网购", "药品", "medical", "online"),
    ("虚构地铁公司", "地铁", "地铁", "transport", None),
    ("虚构出行公司", "高铁机票", "高铁", "transport", None),
    ("虚构餐厅", "到店聚餐", "聚餐", "food", "offline"),
    ("美团", "午餐外卖", "外卖", "food", "online"),
    ("淘宝", "连衣裙网购", "连衣裙", "clothing", "online"),
    ("虚构服装店", "门店衣服", "衣服", "clothing", "offline"),
    ("拼多多", "纸巾洗衣液网购", "洗衣液", "consumable", "online"),
    ("虚构便利店", "到店牙膏", "牙膏", "consumable", "offline"),
    ("虚构超市", "蔬菜水果生鲜", "生鲜", "grocery", None),
    ("京东商城", "电脑网购", "电脑", "electronics", "online"),
    ("虚构物业公司", "水费电费", "电费", "housing", None),
    ("虚构学校", "学费", "学费", "education", None),
    ("虚构电影院", "电影", "电影", "leisure", None),
    ("虚构酒店", "住宿", "住宿", "lodging", None),
    ("虚构通信公司", "话费", "话费", "communication", None),
    ("张三", "个人转账", "个人转账", None, None),
    ("支付宝", "付款", "付款", None, None),
    ("淘宝", "购物", "购物", None, "online"),
    ("虚构超市", "混合商品", "商品", None, None),
    ("京东", "退款", "退款", None, None),
]


def payload_for(view, source, mode):
    page = replace(
        _page(mode), prompt=view["rule"]["prompt"],
        targets=tuple(ScanTarget(index, tag["name"]) for index, tag in enumerate(view["tags"], 1)),
    )
    return LlmPrivacyService(POLICY).build_payload(page, source)


@pytest.mark.parametrize("view", VIEWS, ids=lambda view: view["system_name"])
@pytest.mark.parametrize("mode", [1, 2, 3])
def test_preset_names_and_complete_prompt_survive_privacy(view, mode):
    TargetTagViewCreateRequest.model_validate({key: view[key] for key in ("name", "system_name")})
    for tag in view["tags"]:
        TargetTagCreateRequest.model_validate(tag)
    rule = view["rule"]
    AutoTagRuleCreateRequest.model_validate({
        "view_id": 1, "name": rule["name"], "enabled": False,
        "cron": rule["cron"], "amount_mode": rule["amount_mode"],
        "method_config": {"schema_version": 1, "model_id": 2, "prompt": rule["prompt"]},
    })
    payload = payload_for(view, _input(), mode)
    assert payload.rule_prompt == unicodedata.normalize("NFKC", rule["prompt"]).rstrip("。")
    assert [tag.name for tag in payload.candidates] == [tag["name"] for tag in view["tags"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[1])
@pytest.mark.parametrize("mode", [1, 2, 3])
def test_scenario_evidence_and_authored_json_contract(case, mode):
    merchant, summary, evidence, purpose, channel = case
    source = _input(summary, merchant=merchant, amount=350000 if "聚餐" in summary else 900)
    for view, expected in zip(VIEWS, (purpose, channel), strict=True):
        payload = payload_for(view, source, mode)
        request = build_litellm_request(payload, _profile())
        data = json.loads(request["messages"][1]["content"])
        # Unknown-only fragments are deliberately discarded; no source value
        # from a real bill is used as a privacy vocabulary extension.
        if evidence != "商品":
            assert evidence in data["summary"]
        if merchant in {"淘宝", "京东", "京东商城", "拼多多", "美团"}:
            assert data["merchant"] == merchant
        if merchant == "张三":
            assert data["merchant"] == ""
        if expected is None:
            result = parse_business_output(json.dumps({
                "item": payload.item, "decision": "insufficient", "suggestions": [],
            }), payload)
            assert result.kind == "NO_SUGGESTION"
        else:
            index = next(i for i, tag in enumerate(view["tags"], 1) if tag["system_name"] == expected)
            for reason in data["reason_options"]:
                result = parse_business_output(json.dumps({
                    "item": payload.item, "decision": "suggestion",
                    "suggestions": [{"tag": f"t{index}", "reason": reason}],
                }, ensure_ascii=False), payload)
                assert result.suggestions[0].tag_name == view["tags"][index - 1]["name"]


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_new_vocabulary_does_not_admit_private_fragments_or_money(mode):
    source = _input(
        "网购药品；姓名虚构甲；账号6222123456789012；订单号 RX99887766；"
        "电话13812345678；地址示例路88号；未知诊断名甲；实付123.45元",
        merchant="杨京东", amount=12345,
    )
    payload = payload_for(VIEWS[0], source, mode)
    data = json.loads(build_litellm_request(payload, _profile())["messages"][1]["content"])
    text = json.dumps({key: value for key, value in data.items() if key != "item"}, ensure_ascii=False)
    assert payload.merchant == ""  # Not the EXACT reviewed platform name.
    for private in ("虚构甲", "杨京东", "622212", "RX998", "138123", "示例路", "未知诊断名甲", "123.45"):
        assert private not in text
    assert "网购药品" in data["summary"]
    assert ("amount_units" in data) == (mode == 2)
    for reason in ("网购药品，姓名虚构甲", "网购药品，订单号RX99887766", "网购123.45元"):
        with pytest.raises(LlmAdapterError) as error:
            parse_business_output(json.dumps({
                "item": payload.item, "decision": "suggestion",
                "suggestions": [{"tag": "t1", "reason": reason}],
            }), payload)
        assert error.value.details["reason_code"] == "UNSAFE_REASON"
