from dataclasses import asdict
from decimal import Decimal
import json

from backend.error import LlmAdapterError
from backend.middleware.mapper.ai_invocation_mapper import AiInvocationMapper
from backend.middleware.usage import Usage

MAX_CAPTURED_RESPONSE_BYTES = 256 * 1024


class InvocationService:
    def __init__(self, sessions):
        self.sessions = sessions

    def start(self, *, task, prompt, profile, context, request):
        values = dict(
            task_key=task.key, task_version=task.version,
            prompt_key=prompt.key, prompt_version=prompt.version,
            model_id=profile.id, model_name=profile.litellm_params.model,
            run_id=context.run_id, attempt=context.attempt,
            request_json=json.dumps({"messages": request["messages"],
                                     "response_format": request["response_format"]},
                                    ensure_ascii=False, separators=(",", ":")),
        )
        try:
            with self.sessions() as db:
                mapper = AiInvocationMapper(db)
                mapper.begin_write()
                invocation_id = mapper.start(values)
                db.commit()
            return invocation_id
        except Exception:
            raise LlmAdapterError("AI 调用记录保存失败", code="AUDIT_STORAGE_ERROR") from None

    def finish(self, invocation_id, *, status, result_code="", error_code="",
               response_text="", latency_ms=0, usage=Usage()):
        encoded = response_text.encode("utf-8")
        truncated = len(encoded) > MAX_CAPTURED_RESPONSE_BYTES
        if truncated:
            response_text = encoded[:MAX_CAPTURED_RESPONSE_BYTES].decode("utf-8", errors="ignore")
        try:
            with self.sessions() as db:
                mapper = AiInvocationMapper(db)
                mapper.begin_write()
                mapper.finish(invocation_id, dict(
                    status=status, result_code=result_code, error_code=error_code,
                    response_text=response_text, response_truncated=int(truncated),
                    latency_ms=latency_ms, **asdict(usage),
                ))
                db.commit()
        except Exception:
            raise LlmAdapterError("AI 调用结果记录保存失败", code="AUDIT_STORAGE_ERROR") from None

    def list(self, request):
        with self.sessions() as db:
            return AiInvocationMapper(db).list(request)

    def usage(self):
        with self.sessions() as db:
            counters, costs = AiInvocationMapper(db).usage()
        values = {key: int(value or 0) for key, value in counters.items()}
        values["calls_with_cost"] = sum(count for _, count in costs)
        values["estimated_cost_usd"] = str(sum(
            (Decimal(cost) * count for cost, count in costs), Decimal("0"),
        ))
        values["cost_source"] = "SDK_ESTIMATE"
        return values
