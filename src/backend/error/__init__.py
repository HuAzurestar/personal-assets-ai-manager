"""Public backend errors shared across services and HTTP adapters."""

from backend.error.domain import (
    DomainError,
    MultipleTagsForView,
    TargetEconomicError,
    TargetIntakeError,
    TargetReviewError,
    TargetTagError,
    UnknownTagSelector,
)

__all__ = [
    "DomainError",
    "MultipleTagsForView",
    "TargetEconomicError",
    "TargetIntakeError",
    "TargetReviewError",
    "TargetTagError",
    "UnknownTagSelector",
]
