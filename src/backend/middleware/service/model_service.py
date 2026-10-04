"""One configured-model resolver for all AI tasks; credentials stay separate."""
from backend.error import LlmAdapterError, ProtectedSecretStoreError, SettingError
from backend.mapper.setting_mapper import SettingMapper
from backend.service.setting_service import SettingService


class ModelService:
    def __init__(self, sessions, secret_reader):
        self.sessions = sessions
        self.secret_reader = secret_reader

    def resolve(self, model_id):
        try:
            with self.sessions() as db:
                setting = SettingMapper(db).get()
            models = SettingService._models_from_value(setting["value"] if setting else {})
        except (ValueError, TypeError, AttributeError, SettingError):
            raise LlmAdapterError("The stored model configuration is invalid", code="CONFIG_ERROR") from None
        profile = next((model for model in models if model.id == model_id), None)
        if profile is None or not profile.enabled:
            raise LlmAdapterError("The configured model is unavailable", code="CONFIG_ERROR")
        return profile

    def credential(self, model_id):
        try:
            secret = self.secret_reader.get_for_provider(model_id)
        except ProtectedSecretStoreError:
            raise LlmAdapterError("The configured credential store is unavailable", code="CONFIG_ERROR") from None
        if not isinstance(secret, str) or not secret.strip():
            raise LlmAdapterError("The configured model credential is missing", code="CONFIG_ERROR")
        return secret
