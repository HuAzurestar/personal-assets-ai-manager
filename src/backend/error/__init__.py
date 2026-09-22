"""Public backend errors shared across services and HTTP adapters."""

from backend.error.domain import (
    AutoTagRuleError,
    DomainError,
    ListQueryError,
    MultipleTagsForView,
    ProtectedSecretStoreError,
    SettingError,
    TargetEconomicError,
    TargetFactError,
    TargetIntakeError,
    TargetReviewError,
    TargetTagError,
    UnknownTagSelector,
)

__all__ = [
    "AutoTagRuleError",
    "DomainError",
    "ListQueryError",
    "MultipleTagsForView",
    "ProtectedSecretStoreError",
    "SettingError",
    "TargetEconomicError",
    "TargetFactError",
    "TargetIntakeError",
    "TargetReviewError",
    "TargetTagError",
    "UnknownTagSelector",
]
