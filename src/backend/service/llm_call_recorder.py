"""Backend persistence adapter for the one evolved LLM audit table."""
from __future__ import annotations

import json
from backend.mapper.llm_prompt_audit_mapper import LlmPromptAuditMapper
from middleware.llm.contract import LlmError, canonical


class SqlCallRecorder:
    def __init__(self, sessions):
        self.sessions = sessions

    def begin(self, request, connection, context):
        associations = dict(context.associations)
        with self.sessions() as db:
            mapper = LlmPromptAuditMapper(db)
            mapper.begin_write()
            if {"rule_id", "ledger_id"} <= associations.keys():
                unknown = mapper.unresolved_for_item(associations["rule_id"], associations["ledger_id"])
                if unknown:
                    raise LlmError("OPERATION_BLOCKED", call_id=unknown["id"],
                                   dispatch_state=json.loads(unknown["metadata_json"]).get("dispatch_state", "MAY_HAVE_EXECUTED"))
                previous = mapper.latest_for_item(associations["rule_id"], associations["ledger_id"])
                if previous is not None:
                    state = json.loads(previous["metadata_json"]).get("dispatch_state", "MAY_HAVE_EXECUTED")
                    # A new configuration must not erase unresolved old work.
                    if previous["status"] == "STARTED" or state == "MAY_HAVE_EXECUTED" or (
                            previous["status"] == "SUCCEEDED" and previous["operation_id"] == context.operation_id):
                        raise LlmError("OPERATION_BLOCKED", call_id=previous["id"], dispatch_state=state)
            value = request.unpack()
            call_id = mapper.start({
                **associations, "run_id": context.job_run_id or "", "attempt": 1,
                "model_id": connection.model_profile_id, "model_name": connection.model,
                "source": context.source, "operation_id": context.operation_id,
                "prompt_id": context.prompt_id, "prompt_fingerprint": context.prompt_fingerprint,
                "request_json": canonical({"messages": value["messages"], "response_format": value["response_format"]}),
                "metadata_json": canonical({"dispatch_state": "NOT_SENT", "effective_token": connection.effective_token}),
            })
            db.commit()
            return call_id

    def dispatch(self, call_id):
        with self.sessions() as db:
            mapper = LlmPromptAuditMapper(db)
            mapper.begin_write()
            mapper.dispatch(call_id)
            db.commit()

    def finish(self, call_id, *, response, error, dispatch_state):
        content = response.content if response else ""
        raw = content.encode("utf-8")
        truncated = len(raw) > 256 * 1024
        content = raw[:256 * 1024].decode("utf-8", errors="ignore")
        metadata = {"dispatch_state": dispatch_state}
        if response:
            # SDK headers and exception bodies never cross this allowlist.
            metadata.update(model=response.model[:512], finish_reason=response.finish_reason[:32])
            metadata["envelope_validated"] = True
            if response.provider_request_id:
                metadata["provider_request_id"] = response.provider_request_id[:160]
        with self.sessions() as db:
            mapper = LlmPromptAuditMapper(db)
            mapper.begin_write()
            mapper.finish_call(call_id, status="SUCCEEDED" if response else "ERROR",
                               response_text=content, response_truncated=truncated,
                               error_code=error.code if error else "", metadata=metadata,
                               usage=dict(response.usage) if response else {})
            db.commit()
