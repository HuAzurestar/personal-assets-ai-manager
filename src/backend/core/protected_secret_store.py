"""OS-backed storage for LLM credentials.

The application deliberately exposes only presence checks to callers. Plaintext
credentials never need to cross the service boundary after they are written.
"""

from typing import Protocol

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from backend.error import ProtectedSecretStoreError


SERVICE_NAME = "PAAM.llm"


class ProtectedSecretStore(Protocol):
    """Minimal credential-store contract used by the setting service."""

    def is_configured(self, model_id: int) -> bool: ...

    def set(self, model_id: int, secret: str) -> None: ...

    def delete(self, model_id: int) -> None: ...


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


protected_secret_store = KeyringProtectedSecretStore()
