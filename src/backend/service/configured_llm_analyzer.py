"""Resolve one configured model and invoke the protected LiteLLM boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from backend.core import protected_secret_store
from backend.error import LlmAdapterError
from backend.schema.llm_analysis import (
    LlmAnalysisInput,
    LlmAnalysisResult,
    ProtectedLlmAnalysisInput,
)
from backend.schema.setting import AutomationModelWrite
from backend.service.llm_adapter import LiteLlmAdapter, build_litellm_request, parse_provider_response
from backend.service.llm_prompt_audit_service import PromptAuditContext
from backend.bootstrap import create_llm_client
from backend.service.model_call_service import read_model, connection_for, request_for, SavedModelAdmission, LEGACY_CODES
from backend.mapper.llm_prompt_audit_mapper import LlmPromptAuditMapper
from middleware.llm.contract import CallContext, LlmError, LlmResponse, canonical
from middleware.llm.prompt import prompt_store


class ProviderSecretReader(Protocol):
    def get_for_provider(self, model_id: int) -> str | None: ...


provider_secret_reader = protected_secret_store


class ConfiguredLlmAnalyzer:
    def __init__(
        self,
        sessions: Callable[[], Session],
        secret_store: ProviderSecretReader,
        adapter: LiteLlmAdapter | None = None,
    ):
        self._sessions = sessions
        self._secret_store = secret_store
        self._adapter = adapter or LiteLlmAdapter()
        self._client = create_llm_client(sessions, secret_store, completion=self._adapter._completion)

    async def analyze(
        self,
        payload: LlmAnalysisInput,
        *,
        rule_id: int,
        model_id: int,
        audit_context: PromptAuditContext,
    ) -> LlmAnalysisResult:
        if audit_context.rule_id != rule_id or audit_context.model_id != model_id:
            raise LlmAdapterError("Prompt audit context mismatch", code="CONFIG_ERROR")
        if not isinstance(payload, ProtectedLlmAnalysisInput):
            raise LlmAdapterError(
                "Scheduled analysis requires a protected Ledger payload",
                code="CONFIG_ERROR",
            )
        try:
            execution = audit_context.execution_snapshot
            if execution is not None:
                profile = AutomationModelWrite.model_validate_json(execution.model_json)
                if profile.id != model_id or not profile.enabled or execution.rule_revision != audit_context.rule_revision:
                    raise LlmError("CONFIG_INVALID")
                if prompt_store.get(execution.prompt_id).fingerprint != execution.prompt_fingerprint:
                    raise LlmError("CONFIG_INVALID")
            else:
                profile = await asyncio.to_thread(read_model, self._sessions, model_id)
            connection = connection_for(profile)
            legacy = build_litellm_request(payload, profile)
            request = request_for(legacy["messages"], profile, response_format=legacy["response_format"])
            semantic = request.unpack()
            # The ephemeral item alias must not make the same logical item look
            # like a new operation on the next CRON tick or process restart.
            for message in semantic["messages"]:
                message["content"] = message["content"].replace(payload.item, "<item>")
            operation_id = hashlib.sha256(canonical([
                rule_id, audit_context.ledger_id, audit_context.rule_revision,
                connection.effective_token, semantic,
            ]).encode()).hexdigest()
            context = CallContext("tag-scan", operation_id, audit_context.run_id or None,
                                  (("rule_id", rule_id), ("rule_revision", audit_context.rule_revision),
                                   ("ledger_id", audit_context.ledger_id), ("model_id", model_id)),
                                  prompt_id=execution.prompt_id if execution else "tag-suggestion",
                                  prompt_fingerprint=execution.prompt_fingerprint if execution else prompt_store.get("tag-suggestion").fingerprint)
            restored = await asyncio.to_thread(self._recover, context)
            if restored is not None:
                response, old_item = restored
                payload = payload.model_copy(update={"item": old_item})
            else:
                admission = await asyncio.to_thread(SavedModelAdmission, self._sessions, self._secret_store,
                                                    connection, run_control=audit_context.run_control)
                response = await self._client.generate(request, connection, context, admission=admission)
            if audit_context.run_control is not None and not audit_context.run_control.may_commit():
                await asyncio.to_thread(self.prevent_recovery, response.call_id)
                raise LlmError("CANCELLED", call_id=response.call_id, dispatch_state="RESPONSE_RECEIVED")
            try:
                result = parse_provider_response({"choices": [{"index": 0, "finish_reason": response.finish_reason,
                                                   "message": {"role": "assistant", "content": response.content}}]}, payload)
            except LlmAdapterError as error:
                error.details["call_id"] = response.call_id
                raise
            # Return the fresh caller alias after checking the original response.
            result.item = json.loads(legacy["messages"][-1]["content"])["item"]
            object.__setattr__(result, "_call_id", response.call_id)
            return result
        except LlmError as error:
            raise LlmAdapterError("The model call could not complete safely",
                                  code=LEGACY_CODES.get(error.code, "CONFIG_ERROR"), retryable=False,
                                  details={"call_id": error.call_id, "dispatch_state": error.dispatch_state}) from None
        except asyncio.CancelledError:
            if "context" in locals():
                with self._sessions() as db:
                    row = LlmPromptAuditMapper(db).latest_for_item(rule_id, audit_context.ledger_id)
                if row and row["operation_id"] == context.operation_id:
                    self.prevent_recovery(row["id"])
            raise

    def _recover(self, context):
        ids = dict(context.associations)
        with self._sessions() as db:
            mapper = LlmPromptAuditMapper(db)
            unknown = mapper.unresolved_for_item(ids["rule_id"], ids["ledger_id"])
            if unknown:
                raise LlmError("OPERATION_BLOCKED", call_id=unknown["id"], dispatch_state="MAY_HAVE_EXECUTED")
            row = mapper.latest_for_item(ids["rule_id"], ids["ledger_id"])
        if row is None:
            return None
        state = json.loads(row["metadata_json"]).get("dispatch_state", "MAY_HAVE_EXECUTED")
        if row["status"] == "SUCCEEDED" and row["operation_id"] == context.operation_id:
            if row["response_truncated"]:
                raise LlmError("OPERATION_BLOCKED", call_id=row["id"], dispatch_state=state)
            metadata = json.loads(row["metadata_json"])
            if metadata.get("envelope_validated") is not True:
                raise LlmError("OPERATION_BLOCKED", call_id=row["id"], dispatch_state=state)
            old_item = json.loads(json.loads(row["request_json"])["messages"][-1]["content"])["item"]
            return LlmResponse(row["id"], row["response_text"], metadata.get("finish_reason", ""),
                               metadata.get("model", "")), old_item
        if row["status"] == "STARTED" or state == "MAY_HAVE_EXECUTED":
            raise LlmError("OPERATION_BLOCKED", call_id=row["id"], dispatch_state=state)
        return None

    async def close(self):
        await self._client.close()

    def validate_commit(self, call_id):
        with self._sessions() as db:
            row = LlmPromptAuditMapper(db).call_basis(call_id)
        current = connection_for(read_model(self._sessions, row.model_id))
        return current.effective_token == json.loads(row.metadata_json).get("effective_token")

    def prevent_recovery(self, call_id):
        with self._sessions() as db:
            mapper = LlmPromptAuditMapper(db)
            mapper.begin_write()
            basis = mapper.call_basis(call_id)
            mapper.prevent_recovery(call_id)
            if basis.rule_id is not None and basis.ledger_id is not None:
                from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper
                AutoTagScanMapper(db).record_business(basis.rule_id, dict(
                    ledger_id=basis.ledger_id, call_id=call_id, validation="REJECTED", commit="CANCELLED"))
            db.commit()
