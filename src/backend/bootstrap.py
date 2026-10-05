"""Composition root: public providers and backend ports are registered here."""
from middleware.llm import LlmClient
from middleware.llm.provider import LiteLlmProvider
from backend.service.llm_call_recorder import SqlCallRecorder


class ModelCredentialPort:
    def __init__(self, reader):
        self.reader = reader

    def resolve(self, credential_ref):
        prefix, model_id = credential_ref.split(":", 1)
        if prefix != "model":
            raise ValueError("Unknown credential reference")
        return self.reader.get_for_provider(int(model_id))


def create_llm_client(sessions, reader, *, completion=None):
    client = LlmClient(SqlCallRecorder(sessions), ModelCredentialPort(reader))
    client.register("litellm", LiteLlmProvider(completion))
    return client
