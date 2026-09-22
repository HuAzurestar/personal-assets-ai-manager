"""Shared application infrastructure."""

from backend.core.protected_secret_store import (
    ProtectedSecretStore,
    protected_secret_store,
)

__all__ = ["ProtectedSecretStore", "protected_secret_store"]
