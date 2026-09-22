from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.error import LlmAdapterError
from backend.schema.llm_analysis import SyntheticLlmAnalysisInput
from backend.schema.setting import AutomationModelWrite
from backend.service.llm_adapter import (
    LiteLlmAdapter,
    build_litellm_request,
    parse_business_output,
    parse_provider_response,
)


def _payload(**changes) -> SyntheticLlmAnalysisInput:
    value = {
        "source": "SYNTHETIC_FIXTURE",
        "item": "item_1",
        "direction": "OUT",
        "merchant": "示例餐厅",
        "summary": "午餐",
        "rule_prompt": "结合商户与摘要判断标签。",
        "amount": {
            "mode": "BAND",
            "currency_code": "CNY",
            "band_code": "b1",
            "band_label": "[0,30)元",
        },
        "candidates": [
            {"tag_id": 41, "name": "餐饮", "status": "ACTIVE"},
            {"tag_id": 52, "name": "聚餐", "status": "ACTIVE"},
            {"tag_id": 63, "name": "交通", "status": "ACTIVE"},
        ],
    }
    value.update(changes)
    return SyntheticLlmAnalysisInput.model_validate(value)


def _profile(**changes) -> AutomationModelWrite:
    value = {
        "id": 1,
        "name": "Synthetic model",
        "enabled": True,
        "litellm_params": {
            "model": "openai/Qwen/Qwen3-8B",
            "api_base": "https://api.example.test/v1",
            "temperature": 0.0,
            "max_tokens": 512,
            "timeout": 30,
            "extra_body": {"enable_thinking": False},
        },
    }
    value.update(changes)
    return AutomationModelWrite.model_validate(value)


def _content(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _provider(content: object, **choice_changes):
    choice = {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": content},
    }
    choice.update(choice_changes)
    return {"choices": [choice]}


@pytest.mark.parametrize(
    ("value", "kind", "tag_ids"),
    [
        (
            {
                "item": "item_1",
                "decision": "suggestion",
                "suggestions": [{"tag": "t1", "reason": "午餐语义支持餐饮。"}],
            },
            "SUGGESTED",
            [41],
        ),
        (
            {"item": "item_1", "decision": "insufficient", "suggestions": []},
            "NO_SUGGESTION",
            [],
        ),
        (
            {
                "item": "item_1",
                "decision": "suggestion",
                "suggestions": [
                    {"tag": "t1", "reason": "餐饮语义。"},
                    {"tag": "t2", "reason": "可能为聚餐。"},
                ],
            },
            "SUGGESTED",
            [41, 52],
        ),
    ],
)
def test_strict_business_output_accepts_only_two_success_kinds(value, kind, tag_ids):
    result = parse_business_output(_content(value), _payload())
    assert result.kind == kind
    assert [item.tag_id for item in result.suggestions] == tag_ids


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (
            '{"item":"item_1","decision":"insufficient","suggestions":[]',
            "OUTPUT_JSON_INVALID",
        ),
        (
            '```json\n{"item":"item_1","decision":"insufficient","suggestions":[]}\n```',
            "OUTPUT_JSON_INVALID",
        ),
        (
            '{"item":"item_1","decision":"insufficient","suggestions":[]} trailing',
            "OUTPUT_JSON_INVALID",
        ),
        (
            '{"item":"item_1","item":"item_1","decision":"insufficient","suggestions":[]}',
            "OUTPUT_JSON_INVALID",
        ),
        (
            '{"item":"item_1","decision":"insufficient","suggestions":[],"x":NaN}',
            "OUTPUT_JSON_INVALID",
        ),
        (
            _content({"item": "item_1", "decision": "error", "suggestions": []}),
            "OUTPUT_SCHEMA_INVALID",
        ),
        (
            _content(
                {
                    "item": "item_1",
                    "decision": "insufficient",
                    "suggestions": [],
                    "extra": True,
                }
            ),
            "OUTPUT_SCHEMA_INVALID",
        ),
        (
            _content({"item": "item_1", "decision": "insufficient"}),
            "OUTPUT_SCHEMA_INVALID",
        ),
        (
            _content(
                {"item": "item_1", "decision": "insufficient", "suggestions": None}
            ),
            "OUTPUT_SCHEMA_INVALID",
        ),
        (
            _content({"item": "other", "decision": "insufficient", "suggestions": []}),
            "OUTPUT_SEMANTIC_INVALID",
        ),
        (
            _content({"item": "item_1", "decision": "suggestion", "suggestions": []}),
            "OUTPUT_SEMANTIC_INVALID",
        ),
        (
            _content(
                {
                    "item": "item_1",
                    "decision": "insufficient",
                    "suggestions": [{"tag": "t1", "reason": "x"}],
                }
            ),
            "OUTPUT_SEMANTIC_INVALID",
        ),
        (
            _content(
                {
                    "item": "item_1",
                    "decision": "suggestion",
                    "suggestions": [{"tag": "t9", "reason": "x"}],
                }
            ),
            "OUTPUT_SEMANTIC_INVALID",
        ),
        (
            _content(
                {
                    "item": "item_1",
                    "decision": "suggestion",
                    "suggestions": [
                        {"tag": "t1", "reason": "x"},
                        {"tag": "t1", "reason": "y"},
                    ],
                }
            ),
            "OUTPUT_SEMANTIC_INVALID",
        ),
        (
            _content(
                {
                    "item": "item_1",
                    "decision": "suggestion",
                    "suggestions": [{"tag": "t1", "reason": " "}],
                }
            ),
            "OUTPUT_SCHEMA_INVALID",
        ),
        (
            _content(
                {
                    "item": "item_1",
                    "decision": "suggestion",
                    "suggestions": [{"tag": "t1", "reason": "x" * 201}],
                }
            ),
            "OUTPUT_SCHEMA_INVALID",
        ),
    ],
)
def test_strict_business_output_rejects_invalid_content(content, code):
    with pytest.raises(LlmAdapterError) as caught:
        parse_business_output(content, _payload())
    assert caught.value.code == code
    assert "content" not in caught.value.details


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"status_code": 401}, "AUTH_ERROR"),
        ({"status_code": 429}, "RATE_LIMIT"),
        ({"status_code": 503}, "PROVIDER_UNAVAILABLE"),
        ({"choices": []}, "OUTPUT_UNEXPECTED"),
        (_provider("", finish_reason="stop"), "OUTPUT_EMPTY"),
        (_provider("{}", finish_reason="length"), "OUTPUT_TRUNCATED"),
        (_provider("{}", finish_reason="tool_calls"), "OUTPUT_UNEXPECTED"),
    ],
)
def test_provider_envelope_failures_are_not_business_insufficient(response, code):
    with pytest.raises(LlmAdapterError) as caught:
        parse_provider_response(response, _payload())
    assert caught.value.code == code


def test_provider_refusal_and_tool_calls_are_rejected():
    refusal = _provider("{}")
    refusal["choices"][0]["message"]["refusal"] = "cannot comply"
    tool_call = _provider("{}")
    tool_call["choices"][0]["message"]["tool_calls"] = [{"id": "call_1"}]
    for response, code in (
        (refusal, "MODEL_REFUSED"),
        (tool_call, "OUTPUT_UNEXPECTED"),
    ):
        with pytest.raises(LlmAdapterError) as caught:
            parse_provider_response(response, _payload())
        assert caught.value.code == code


def test_provider_non_assistant_message_is_rejected():
    response = _provider("{}")
    response["choices"][0]["message"]["role"] = "tool"
    with pytest.raises(LlmAdapterError) as caught:
        parse_provider_response(response, _payload())
    assert caught.value.code == "OUTPUT_UNEXPECTED"


def test_request_maps_private_tag_ids_to_scoped_aliases_and_amount_band():
    request = build_litellm_request(_payload(), _profile())
    assert request["model"] == "openai/Qwen/Qwen3-8B"
    assert request["stream"] is False
    assert request["response_format"] == {"type": "json_object"}
    user = json.loads(request["messages"][1]["content"])
    assert user["candidates"] == [
        {"id": "t1", "name": "餐饮"},
        {"id": "t2", "name": "聚餐"},
        {"id": "t3", "name": "交通"},
    ]
    assert user["amount_band_code"] == "b1"
    assert user["amount_band"] == "[0,30)元"
    assert not ({"amount", "ledger_id", "account_id", "tag_id"} & user.keys())


def test_json_schema_mode_uses_dynamic_item_candidates_and_count():
    request = build_litellm_request(_payload(), _profile(), response_mode="json_schema")
    response_format = request["response_format"]
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["item"]["const"] == "item_1"
    assert schema["properties"]["suggestions"]["maxItems"] == 3
    assert schema["properties"]["suggestions"]["items"]["properties"]["tag"][
        "enum"
    ] == ["t1", "t2", "t3"]


def test_unknown_response_mode_is_rejected_as_configuration_error():
    with pytest.raises(LlmAdapterError) as caught:
        build_litellm_request(_payload(), _profile(), response_mode="text")
    assert caught.value.code == "CONFIG_ERROR"


def test_adapter_calls_one_model_once_without_fallback_and_resolves_candidate():
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _provider(
            _content(
                {
                    "item": "item_1",
                    "decision": "suggestion",
                    "suggestions": [{"tag": "t2", "reason": "聚餐语义。"}],
                }
            )
        )

    result = LiteLlmAdapter(completion).analyze_synthetic(
        _payload(),
        _profile(),
        api_key="sk-synthetic",
    )
    assert len(calls) == 1
    assert calls[0]["api_key"] == "sk-synthetic"
    assert "fallbacks" not in calls[0]
    assert result.suggestions[0].tag_id == 52


class _ProviderFailure(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (_ProviderFailure(403), "AUTH_ERROR", False),
        (_ProviderFailure(429), "RATE_LIMIT", True),
        (_ProviderFailure(500), "PROVIDER_UNAVAILABLE", True),
        (TimeoutError(), "REQUEST_TIMEOUT", True),
        (_ProviderFailure(400), "CONFIG_ERROR", False),
    ],
)
def test_adapter_maps_provider_exceptions_without_exposing_raw_error(
    failure, code, retryable
):
    def completion(**_):
        raise failure

    with pytest.raises(LlmAdapterError) as caught:
        LiteLlmAdapter(completion).analyze_synthetic(
            _payload(), _profile(), api_key="sk-synthetic"
        )
    assert caught.value.code == code
    assert caught.value.details == {"retryable": retryable}


def test_adapter_refuses_disabled_model_missing_key_and_real_ledger_shape():
    disabled = _profile(enabled=False)
    with pytest.raises(LlmAdapterError) as caught:
        LiteLlmAdapter(lambda **_: None).analyze_synthetic(
            _payload(), disabled, api_key="sk-synthetic"
        )
    assert caught.value.code == "CONFIG_ERROR"

    with pytest.raises(LlmAdapterError) as caught:
        LiteLlmAdapter(lambda **_: None).analyze_synthetic(
            _payload(), _profile(), api_key=" "
        )
    assert caught.value.code == "CONFIG_ERROR"

    real_shape = _payload().model_dump()
    real_shape["ledger_id"] = 7
    real_shape["source"] = "LEDGER"
    with pytest.raises(ValidationError):
        SyntheticLlmAnalysisInput.model_validate(real_shape)


def test_archived_or_duplicate_candidates_are_rejected_before_call():
    archived = _payload().model_dump()
    archived["candidates"][0]["status"] = "ARCHIVED"
    duplicate = _payload().model_dump()
    duplicate["candidates"][1]["tag_id"] = 41
    for value in (archived, duplicate):
        with pytest.raises(ValidationError):
            SyntheticLlmAnalysisInput.model_validate(value)


@pytest.mark.parametrize("key", ["response_format", "stream", "fallbacks"])
def test_model_profile_cannot_override_protocol_or_add_fallback(key):
    profile = _profile().model_dump()
    profile["litellm_params"][key] = {"unsafe": True}
    with pytest.raises(ValidationError):
        AutomationModelWrite.model_validate(profile)
