"""Program-owned prompt examples must pass the same strict output boundary."""
import json

import pytest

from backend.error import LlmAdapterError
from backend.service.llm_adapter import build_litellm_request, parse_business_output
from test_llm_adapter import _profile
from test_privacy_contract import _input, _payload


def output(payload, reason):
    return json.dumps({
        "item": payload.item, "decision": "suggestion",
        "suggestions": [{"tag": "t1", "reason": reason}],
    }, ensure_ascii=False)


@pytest.mark.parametrize("mode", [1, 2, 3])
@pytest.mark.parametrize("direction", ["IN", "OUT"])
def test_program_example_is_valid_for_its_protected_payload(mode, direction):
    payload = _payload(_input(direction=direction), mode=mode)
    request = build_litellm_request(payload, _profile())
    example = next(line.removeprefix("有建议：") for line in request["messages"][0]["content"].splitlines()
                   if line.startswith("有建议："))
    assert parse_business_output(example, payload).kind == "SUGGESTED"


@pytest.mark.parametrize("mode", [1, 2, 3])
@pytest.mark.parametrize("response_mode", ["json_object", "json_schema"])
def test_reason_options_are_bounded_data_and_all_pass_existing_validation(mode, response_mode):
    payload = _payload(_input("夜宵", merchant="咖啡馆"), mode=mode)
    request = build_litellm_request(payload, _profile(), response_mode=response_mode)
    user = json.loads(request["messages"][1]["content"])
    options = user["reason_options"]
    assert 1 <= len(options) <= 3 and len(options) == len(set(options))
    assert "夜宵" not in request["messages"][0]["content"]
    assert any("夜宵" in reason for reason in options)
    assert "insufficient" in request["messages"][0]["content"]
    for reason in options:
        assert 0 < len(reason) <= 200
        result = parse_business_output(output(payload, reason), payload)
        assert result.suggestions[0].reason == reason
    if response_mode == "json_schema":
        reason_schema = request["response_format"]["json_schema"]["schema"]["properties"]["suggestions"]["items"]["properties"]["reason"]
        assert reason_schema["enum"] == options
    insufficient = {"item": payload.item, "decision": "insufficient", "suggestions": []}
    assert parse_business_output(json.dumps(insufficient), payload).kind == "NO_SUGGESTION"


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_long_or_unsafe_reason_sources_fall_back_without_clipping_or_money(mode):
    payload = _payload(_input("午餐" * 300, merchant="餐馆" * 100), mode=mode)
    options = json.loads(build_litellm_request(payload, _profile())["messages"][1]["content"])["reason_options"]
    assert options == ["依据支出用途建议分类。"]
    assert parse_business_output(output(payload, options[0]), payload).kind == "SUGGESTED"


@pytest.mark.parametrize("reason", ["简短依据", "张三的餐饮", "账号123456789012345678", "早餐金额29.00元"])
def test_invalid_model_reasons_are_not_repaired_or_allowed_by_prompt_guidance(reason):
    payload = _payload()
    with pytest.raises(LlmAdapterError) as caught:
        parse_business_output(output(payload, reason), payload)
    assert caught.value.code == "OUTPUT_SEMANTIC_INVALID"
    assert caught.value.details["reason_code"] == "UNSAFE_REASON"


def test_existing_safe_reason_remains_valid_without_new_output_fields():
    payload = _payload()
    assert parse_business_output(output(payload, "午餐语义支持餐饮。"), payload).kind == "SUGGESTED"
