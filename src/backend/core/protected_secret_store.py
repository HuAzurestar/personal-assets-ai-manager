"""Protected storage for LLM credentials.

The application deliberately exposes only presence checks to callers. Plaintext
credentials never need to cross the service boundary after they are written.
"""

import os
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from backend.error import ProtectedSecretStoreError
from backend.core.config import DATA_DIR
from backend.core.encrypted_credential_store import EncryptedFileProtectedSecretStore


SERVICE_NAME = "PAAM.llm"


class ProtectedSecretStore(Protocol):
    """Minimal credential-store contract used by the setting service."""

    def is_configured(self, model_id: int) -> bool: ...

    def set(self, model_id: int, secret: str) -> None: ...

    def delete(self, model_id: int) -> None: ...

    def get_for_provider(self, model_id: int) -> str | None: ...


class KeyringProtectedSecretStore:
    """Persist model credentials in the platform keyring."""

    @staticmethod
    def _username(model_id: int) -> str:
        return f"model/{model_id}"

    def is_configured(self, model_id: int) -> bool:
        try:
            return keyring.get_password(SERVICE_NAME, self._username(model_id)) is not None
        except NoKeyringError:
            # A headless deployment can still inspect and edit model settings.
            # Writes remain strict: never fall back to plaintext credentials.
            return False
        except KeyringError as error:
            raise ProtectedSecretStoreError() from error

    def get_for_provider(self, model_id: int) -> str | None:
        try:
            return keyring.get_password(SERVICE_NAME, self._username(model_id))
        except KeyringError as error:
            raise ProtectedSecretStoreError() from error

    def set(self, model_id: int, secret: str) -> None:
        try:
            keyring.set_password(SERVICE_NAME, self._username(model_id), secret)
        except KeyringError as error:
            raise ProtectedSecretStoreError() from error

    def delete(self, model_id: int) -> None:
        try:
            if keyring.get_password(SERVICE_NAME, self._username(model_id)) is None:
                return
            keyring.delete_password(SERVICE_NAME, self._username(model_id))
        except PasswordDeleteError as error:
            try:
                if keyring.get_password(
                    SERVICE_NAME,
                    self._username(model_id),
                ) is None:
                    # A concurrent or external removal reached the desired state.
                    return
            except KeyringError as verification_error:
                raise ProtectedSecretStoreError() from verification_error
            raise ProtectedSecretStoreError() from error
        except KeyringError as error:
            raise ProtectedSecretStoreError() from error


def configured_secret_store() -> ProtectedSecretStore:
    mode = os.getenv("PAAM_CREDENTIAL_STORE", "keyring")
    if mode == "keyring":
        return KeyringProtectedSecretStore()
    if mode == "encrypted_file":
        key_file = os.getenv("PAAM_CREDENTIAL_KEY_FILE")
        if not key_file:
            raise RuntimeError("PAAM_CREDENTIAL_KEY_FILE is required for encrypted_file")
        return EncryptedFileProtectedSecretStore(
            DATA_DIR / "model-credentials.fernet", Path(key_file),
        )
    raise RuntimeError("PAAM_CREDENTIAL_STORE must be keyring or encrypted_file")


protected_secret_store = configured_secret_store()
