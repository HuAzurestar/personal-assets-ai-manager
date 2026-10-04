"""Auto-tag task: disclosure input, candidate aliases and semantic validation."""
from __future__ import annotations

import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from backend.core.money import currency_quantum
from backend.error import LlmAdapterError
from backend.schema.llm_analysis import LlmAnalysisInput, LlmAnalysisResult, LlmResolvedSuggestion, ProtectedLlmAnalysisInput, SyntheticLlmAnalysisInput
from backend.service.llm_privacy_service import LlmPrivacyService, require_protected_payload
from backend.middleware.provider import _error, response_content
from backend.middleware.prompt import ASSET_DIR, PromptVersion, bundled_prompt, render_text
from backend.middleware.task import PreparedTask, TaskDefinition

MAX_RESPONSE_BYTES = 32 * 1024
MAX_JSON_DEPTH = 12
ResponseMode = Literal["json_object", "json_schema"]
_CONTRACT = json.loads((ASSET_DIR / "auto_tag.contract.v1.json").read_text(encoding="utf-8"))

class _DuplicateJsonKey(ValueError):
    pass


class _BusinessSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tag: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason cannot be blank")
        return value


class _BusinessOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    item: str
    decision: Literal["suggestion", "insufficient"]
    suggestions: list[_BusinessSuggestion]


def build_messages(
    payload: LlmAnalysisInput,
    *,
    response_mode: ResponseMode = "json_object",
    prompt: PromptVersion | None = None,
) -> list[dict[str, str]]:
    if isinstance(payload, ProtectedLlmAnalysisInput):
        try:
            require_protected_payload(payload)
        except ValueError:
            raise _error("CONFIG_ERROR", "The protected input boundary rejected this request") from None
    elif not isinstance(payload, SyntheticLlmAnalysisInput):
        raise _error("CONFIG_ERROR", "The model input type is unsupported")
    aliases = [f"t{index}" for index in range(1, len(payload.candidates) + 1)]
    alias_text = "/".join(aliases)
    maximum = len(aliases)
    variables = {
        "maximum": maximum, "aliases": alias_text,
        "suggestion_example": json.dumps({
            "item": payload.item, "decision": "suggestion",
            "suggestions": [{"tag": "t1", "reason": _generic_reason(payload)}],
        }, ensure_ascii=False, separators=(",", ":")),
        "insufficient_example": json.dumps({
            "item": payload.item, "decision": "insufficient", "suggestions": [],
        }, ensure_ascii=False, separators=(",", ":")),
    }
    system_parts = [
        (prompt or AUTO_TAG_TASK.default_prompt).instruction,
        *_CONTRACT["guard"],
        _amount_instruction(payload),
        *(render_text(template, variables) for template in _CONTRACT["output"][response_mode]),
    ]

    user_value: dict[str, object] = {
        "rule_prompt": payload.rule_prompt,
        "item": payload.item,
        "direction": payload.direction,
        "currency_code": payload.amount.currency_code,
        "merchant": payload.merchant,
        "summary": payload.summary,
        # Source-derived text remains low-priority data, never system instructions.
        "reason_options": _reason_options(payload),
        "candidates": [
            {"id": alias, "name": candidate.name}
            for alias, candidate in zip(aliases, payload.candidates, strict=True)
        ],
    }
    if payload.date is not None:
        user_value["date"] = payload.date
    if payload.payment_channel is not None:
        user_value["payment_channel"] = payload.payment_channel
    if payload.amount.mode == "BAND":
        user_value["amount_band_code"] = payload.amount.band_code
        user_value["amount_band"] = payload.amount.band_label
    elif payload.amount.mode == "EXACT":
        user_value["amount_units"] = payload.amount.amount_units
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {
            "role": "user",
            "content": json.dumps(
                user_value,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def _generic_reason(payload: LlmAnalysisInput) -> str:
    direction = "收入" if payload.direction == "IN" else "支出"
    return render_text(_CONTRACT["reason"], {"direction": direction})


def _reason_options(payload: LlmAnalysisInput) -> list[str]:
    """Offer bounded excerpts, checked by exactly the existing output validator.

    No inference, bill-derived vocabulary expansion or repair of a model output.
    An overlong/unsafe excerpt is omitted whole, not clipped into a new claim.
    """
    privacy = LlmPrivacyService()
    amount_mode = {"BAND": 1, "EXACT": 2, "NONE": 3}[payload.amount.mode]
    reasons = []
    for label, text in (("用途", payload.summary), ("商户", payload.merchant)):
        if not text:
            continue
        reason = f"{label}：{text}。"
        if len(reason) > 200:
            continue
        candidate = LlmResolvedSuggestion(tag_id=1, tag_name="", reason=reason)
        try:
            privacy.validate_suggestions((candidate,), amount_mode=amount_mode)
        except ValueError:
            continue
        reasons.append(reason)
    generic = _generic_reason(payload)
    privacy.validate_suggestions(
        (LlmResolvedSuggestion(tag_id=1, tag_name="", reason=generic),), amount_mode=amount_mode,
    )
    return [*reasons, generic]


def parse_provider_response(response: object, payload: LlmAnalysisInput) -> LlmAnalysisResult:
    return parse_business_output(response_content(response), payload)


def parse_business_output(
    content: str,
    payload: LlmAnalysisInput,
) -> LlmAnalysisResult:
    if len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise _error("OUTPUT_SCHEMA_INVALID", "The model output is too large")
    if _json_depth(content) > MAX_JSON_DEPTH:
        raise _error("OUTPUT_SCHEMA_INVALID", "The model output is too deeply nested")
    try:
        raw = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, _DuplicateJsonKey, ValueError):
        raise _error(
            "OUTPUT_JSON_INVALID", "The model output is not strict JSON"
        ) from None
    try:
        value = _BusinessOutput.model_validate(raw)
    except ValidationError as error:
        # Extra-property names are provider-controlled and may themselves be PII.
        location = error.errors()[0]["loc"]
        field_path = ".".join(
            str(part) for part in location
            if part in {"item", "decision", "suggestions", "tag", "reason"}
            or (isinstance(part, int) and 0 <= part <= 100)
        ) or "output"
        raise _error(
            "OUTPUT_SCHEMA_INVALID",
            "The model output does not match the business schema",
            details={"field_path": field_path},
        ) from None

    if value.item != payload.item:
        raise _error(
            "OUTPUT_SEMANTIC_INVALID",
            "The model output item does not match the request",
            details={"field_path": "item", "reason_code": "ITEM_MISMATCH"},
        )
    if (value.decision == "suggestion") != bool(value.suggestions):
        raise _error(
            "OUTPUT_SEMANTIC_INVALID",
            "The model decision and suggestions disagree",
            details={"field_path": "suggestions", "reason_code": "DECISION_MISMATCH"},
        )
    if len(value.suggestions) > len(payload.candidates):
        raise _error(
            "OUTPUT_SEMANTIC_INVALID",
            "The model returned too many suggestions",
            details={"field_path": "suggestions", "reason_code": "TOO_MANY_SUGGESTIONS"},
        )

    aliases = {
        f"t{index}": candidate
        for index, candidate in enumerate(payload.candidates, start=1)
    }
    output_aliases = [suggestion.tag for suggestion in value.suggestions]
    if len(output_aliases) != len(set(output_aliases)):
        raise _error(
            "OUTPUT_SEMANTIC_INVALID",
            "The model returned a duplicate candidate",
            details={"field_path": "suggestions.tag", "reason_code": "DUPLICATE_TAG"},
        )
    if any(alias not in aliases for alias in output_aliases):
        raise _error(
            "OUTPUT_SEMANTIC_INVALID",
            "The model returned an unknown candidate",
            details={"field_path": "suggestions.tag", "reason_code": "UNKNOWN_TAG"},
        )

    suggestions = [
        LlmResolvedSuggestion(
            tag_id=aliases[suggestion.tag].tag_id,
            tag_name=aliases[suggestion.tag].name,
            reason=suggestion.reason.strip(),
        )
        for suggestion in value.suggestions
    ]
    if isinstance(payload, ProtectedLlmAnalysisInput):
        try:
            LlmPrivacyService().validate_suggestions(
                tuple(suggestions), amount_mode={"BAND": 1, "EXACT": 2, "NONE": 3}[payload.amount.mode],
            )
        except ValueError:
            raise _error(
                "OUTPUT_SEMANTIC_INVALID", "The model reason failed the privacy boundary",
                details={"field_path": "suggestions.reason", "reason_code": "UNSAFE_REASON"},
            ) from None
    return LlmAnalysisResult(
        kind="SUGGESTED" if suggestions else "NO_SUGGESTION",
        item=value.item,
        suggestions=suggestions,
    )


def _amount_instruction(payload: LlmAnalysisInput) -> str:
    amount = payload.amount
    return render_text(_CONTRACT["amount"][amount.mode], {
        "currency_code": amount.currency_code,
        "band_code": amount.band_code,
        "band_label": amount.band_label,
        "quantum": currency_quantum(amount.currency_code),
    })


def _response_format(
    payload: LlmAnalysisInput,
    response_mode: ResponseMode,
) -> dict[str, object]:
    if response_mode == "json_object":
        return {"type": "json_object"}
    aliases = [f"t{index}" for index in range(1, len(payload.candidates) + 1)]
    suggestion = {
        "type": "object",
        "additionalProperties": False,
        "required": ["tag", "reason"],
        "properties": {
            "tag": {"type": "string", "enum": aliases},
            "reason": {
                "type": "string", "minLength": 1, "maxLength": 200,
                "enum": _reason_options(payload),
            },
        },
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["item", "decision", "suggestions"],
        "properties": {
            "item": {"type": "string", "const": payload.item},
            "decision": {"type": "string", "enum": ["suggestion", "insufficient"]},
            "suggestions": {
                "type": "array",
                "maxItems": len(aliases),
                "items": suggestion,
            },
        },
        "oneOf": [
            {
                "properties": {
                    "decision": {"const": "suggestion"},
                    "suggestions": {"minItems": 1},
                },
                "required": ["decision", "suggestions"],
            },
            {
                "properties": {
                    "decision": {"const": "insufficient"},
                    "suggestions": {"maxItems": 0},
                },
                "required": ["decision", "suggestions"],
            },
        ],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "paam_tag_suggestion",
            "strict": True,
            "schema": schema,
        },
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = child
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON constant: {value}")


def _json_depth(content: str) -> int:
    depth = 0
    maximum = 0
    quoted = False
    escaped = False
    for character in content:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            maximum = max(maximum, depth)
        elif character in "]}":
            depth = max(0, depth - 1)
    return maximum



def prepare_task(payload, prompt, response_mode):
    return PreparedTask(
        messages=build_messages(payload, response_mode=response_mode, prompt=prompt),
        response_format=_response_format(payload, response_mode),
    )


AUTO_TAG_TASK = TaskDefinition(
    key="auto_tag.classify", version=1, title="自动标签建议",
    input_type=ProtectedLlmAnalysisInput, output_type=LlmAnalysisResult,
    default_prompt=bundled_prompt("auto_tag.classify"),
    prepare=prepare_task, parse=parse_business_output,
    result_status=lambda result: "SUGGESTED" if result.kind == "SUGGESTED" else "INSUFFICIENT",
)
