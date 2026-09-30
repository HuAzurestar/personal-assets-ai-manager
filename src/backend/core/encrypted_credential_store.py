"""Single-process, authenticated encrypted storage for headless deployments."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from cryptography.fernet import Fernet, InvalidToken

from backend.error import ProtectedSecretStoreError


class EncryptedFileProtectedSecretStore:
    """Keep ciphertext in the data volume and its key in a separate mount."""

    def __init__(self, data_path: Path, key_path: Path):
        self.data_path = Path(data_path)
        self.key_path = Path(key_path)
        if self.key_path.resolve().is_relative_to(self.data_path.parent.resolve()):
            raise RuntimeError("Credential key file must be outside the data directory")
        self._lock = RLock()
        # Do not serve an empty-looking configuration with a missing/wrong key.
        self._read()

    def _cipher(self) -> Fernet:
        try:
            return Fernet(self.key_path.read_bytes().strip())
        except (OSError, ValueError) as error:
            raise ProtectedSecretStoreError() from error

    def _read(self) -> dict[str, str]:
        cipher = self._cipher()
        try:
            token = self.data_path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ProtectedSecretStoreError() from error
        try:
            payload = json.loads(cipher.decrypt(token).decode("utf-8"))
            if (not isinstance(payload, dict) or type(payload.get("version")) is not int
                    or payload["version"] != 1
                    or not isinstance(payload.get("models"), dict)
                    or any(not isinstance(key, str) or not isinstance(value, str)
                           for key, value in payload["models"].items())):
                raise ValueError("Invalid credential document")
            return payload["models"]
        except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ProtectedSecretStoreError() from error

    def _write(self, values: dict[str, str]) -> None:
        token = self._cipher().encrypt(json.dumps(
            {"version": 1, "models": values}, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8"))
        temporary_path = None
        try:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".model-credentials-", dir=self.data_path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                output.write(token)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.data_path)
        except OSError as error:
            raise ProtectedSecretStoreError() from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def is_configured(self, model_id: int) -> bool:
        with self._lock:
            return str(model_id) in self._read()

    def get_for_provider(self, model_id: int) -> str | None:
        with self._lock:
            return self._read().get(str(model_id))

    def set(self, model_id: int, secret: str) -> None:
        with self._lock:
            values = self._read()
            values[str(model_id)] = secret
            self._write(values)

    def delete(self, model_id: int) -> None:
        with self._lock:
            values = self._read()
            if values.pop(str(model_id), None) is not None:
                self._write(values)
