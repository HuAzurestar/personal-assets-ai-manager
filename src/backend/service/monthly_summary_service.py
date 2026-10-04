"""Non-tag LLM use case demonstrating caller-owned disclosure and validation."""
import asyncio
from backend.bootstrap import create_llm_client
from backend.schema.monthly_summary import DisclosedMonthlySummary, MonthlySummaryResult
from backend.service.model_call_service import connection_for, read_model, request_for, SavedModelAdmission
from middleware.llm import CallContext
from middleware.llm.prompt import prompt_store, PromptVariableSpec


class MonthlySummaryService:
    VARIABLES = (PromptVariableSpec("summary_input", "json", max_bytes=16384),)

    def __init__(self, sessions, secrets, *, completion=None):
        self.sessions, self.secrets = sessions, secrets
        self.client = create_llm_client(sessions, secrets, completion=completion)

    async def summarize(self, payload: DisclosedMonthlySummary, *, model_id: int):
        if not isinstance(payload, DisclosedMonthlySummary):
            raise ValueError("Only the disclosed fictional DTO is accepted")
        profile = await asyncio.to_thread(read_model, self.sessions, model_id)
        connection = connection_for(profile)
        template = prompt_store.get("monthly-summary")
        messages = template.render({"summary_input": payload.model_dump(mode="json")}, self.VARIABLES)
        request = request_for(messages, profile, response_format={"type": "json_object"})
        admission = await asyncio.to_thread(SavedModelAdmission, self.sessions, self.secrets, connection)
        response = await self.client.generate(request, connection,
                      CallContext("monthly-summary", prompt_id=template.id, prompt_fingerprint=template.fingerprint),
                      admission=admission)
        # Provider success is not business success. Keep the audit immutable and
        # return the linked call id separately after this use case validates it.
        return response.call_id, MonthlySummaryResult.model_validate_json(response.content)

    async def close(self):
        await self.client.close()
