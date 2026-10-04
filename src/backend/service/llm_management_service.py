"""Read-only prompt management and body-free call projections."""
import hmac
import json
import os
from backend.error import SettingError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.llm_prompt_audit_mapper import LlmPromptAuditMapper
from middleware.llm.prompt import prompt_store, TAG_VARIABLES, PromptVariableSpec


class LlmManagementService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _template(prompt_id):
        try:
            return prompt_store.get(prompt_id)
        except KeyError:
            raise SettingError(404, "Prompt not found", code="PROMPT_NOT_FOUND") from None

    def prompts(self):
        return [dict(id=t.id, name=t.name, fingerprint=t.fingerprint, read_only=True)
                for t in prompt_store.list()]

    def prompt(self, prompt_id):
        t = self._template(prompt_id)
        return dict(id=t.id, name=t.name, fingerprint=t.fingerprint,
                    read_only=True, messages=json.loads(t.content_json))

    def references(self, prompt_id):
        self._template(prompt_id)
        return AutoTagRuleMapper(self.db).prompt_references(prompt_id)

    def preview(self, prompt_id):
        t = self._template(prompt_id)
        # Server-owned fictional DTOs only. Never read ledger/fact rows here.
        if prompt_id == "tag-suggestion":
            values, specs = {"disclosed_input": {"item": "item_demo", "rule_prompt": "Classify fictional purchases",
                              "direction": "OUT", "merchant": "Fictional Cafe", "summary": "coffee",
                              "candidates": [{"alias": "tag_demo", "name": "Meals"}]}}, TAG_VARIABLES
        else:
            values, specs = {"summary_input": {"month": "2026-01", "entry_count": 3,
                                                   "categories": [{"name": "Meals", "count": 3}]}}, (
                PromptVariableSpec("summary_input", "json"),)
        return dict(synthetic=True, prompt_id=t.id, fingerprint=t.fingerprint,
                    messages=t.render(values, specs))

    def calls(self, page_index, page_size):
        return LlmPromptAuditMapper(self.db).list_calls(page_index, page_size)

    def statistics(self):
        return LlmPromptAuditMapper(self.db).statistics()

    def detail(self, call_id, authorization):
        # This local app has no account/role platform: detail is explicitly
        # opt-in, independently protected, and disabled by default.
        expected = os.getenv("PAAM_LLM_AUDIT_DETAIL_TOKEN", "")
        if len(expected) < 32 or not authorization or not hmac.compare_digest(expected, authorization):
            raise SettingError(403, "Sensitive audit detail is not authorized", code="AUDIT_DETAIL_FORBIDDEN")
        row = LlmPromptAuditMapper(self.db).detail(call_id)
        if row is None:
            raise SettingError(404, "Call not found", code="LLM_CALL_NOT_FOUND")
        return dict(row)
