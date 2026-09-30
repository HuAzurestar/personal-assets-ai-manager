"""Headless credential persistence and failure-closed behavior."""

from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.fernet import Fernet

from backend.core.encrypted_credential_store import EncryptedFileProtectedSecretStore
from backend.error import ProtectedSecretStoreError


def _store(tmp_path: Path) -> tuple[EncryptedFileProtectedSecretStore, Path, Path]:
    key_path = tmp_path / "credential-key"
    key_path.write_bytes(Fernet.generate_key())
    data_path = tmp_path / "data" / "model-credentials.fernet"
    return EncryptedFileProtectedSecretStore(data_path, key_path), data_path, key_path


def test_encrypted_credentials_survive_recreation_without_plaintext(tmp_path):
    store, data_path, key_path = _store(tmp_path)
    assert store.is_configured(3) is False
    store.set(3, "sk-synthetic-only")
    store.set(7, "sk-other-synthetic-only")
    ciphertext = data_path.read_bytes()
    assert b"sk-synthetic-only" not in ciphertext
    assert b"sk-other-synthetic-only" not in ciphertext
    assert b"model/3" not in ciphertext

    recreated = EncryptedFileProtectedSecretStore(data_path, key_path)
    assert recreated.is_configured(3) is True
    assert recreated.get_for_provider(3) == "sk-synthetic-only"
    assert recreated.get_for_provider(7) == "sk-other-synthetic-only"
    recreated.delete(3)
    assert EncryptedFileProtectedSecretStore(data_path, key_path).get_for_provider(3) is None
    assert recreated.get_for_provider(7) == "sk-other-synthetic-only"


def test_missing_wrong_or_corrupt_key_never_looks_like_unconfigured(tmp_path):
    store, data_path, key_path = _store(tmp_path)
    store.set(3, "sk-synthetic-only")
    key_path.write_bytes(Fernet.generate_key())
    with pytest.raises(ProtectedSecretStoreError):
        EncryptedFileProtectedSecretStore(data_path, key_path)
    key_path.unlink()
    with pytest.raises(ProtectedSecretStoreError):
        EncryptedFileProtectedSecretStore(data_path, key_path)
    key_path.write_bytes(Fernet.generate_key())
    data_path.write_bytes(b"not-a-valid-token")
    with pytest.raises(ProtectedSecretStoreError):
        EncryptedFileProtectedSecretStore(data_path, key_path)


def test_key_must_be_outside_data_directory(tmp_path):
    data_path = tmp_path / "data" / "model-credentials.fernet"
    key_path = data_path.parent / "key"
    key_path.parent.mkdir()
    key_path.write_bytes(Fernet.generate_key())
    with pytest.raises(RuntimeError, match="outside the data directory"):
        EncryptedFileProtectedSecretStore(data_path, key_path)


def test_failed_atomic_replace_preserves_previous_credentials(tmp_path, monkeypatch):
    store, data_path, key_path = _store(tmp_path)
    store.set(3, "sk-original")
    before = data_path.read_bytes()

    def fail_replace(*_args):
        raise OSError("synthetic failure")

    monkeypatch.setattr("backend.core.encrypted_credential_store.os.replace", fail_replace)
    with pytest.raises(ProtectedSecretStoreError):
        store.set(3, "sk-replacement")
    assert data_path.read_bytes() == before
    assert EncryptedFileProtectedSecretStore(data_path, key_path).get_for_provider(3) == "sk-original"
    assert not list(data_path.parent.glob(".model-credentials-*"))


def test_key_creation_script_never_prints_or_overwrites_key(tmp_path):
    key_path = tmp_path / "credential-key"
    script = Path(__file__).parents[1] / "script" / "create_credential_key.py"
    created = subprocess.run(
        [sys.executable, str(script), str(key_path)], capture_output=True, text=True,
        check=True,
    )
    key = key_path.read_bytes().strip()
    Fernet(key)
    assert key.decode("ascii") not in created.stdout
    repeated = subprocess.run(
        [sys.executable, str(script), str(key_path)], capture_output=True, text=True,
    )
    assert repeated.returncode != 0
    assert key_path.read_bytes().strip() == key
