"""Public backend errors shared across services and HTTP adapters."""

from backend.error.domain import (
    DomainError,
    ListQueryError,
    MultipleTagsForView,
    TargetEconomicError,
    TargetFactError,
    TargetIntakeError,
    TargetReviewError,
    TargetTagError,
    UnknownTagSelector,
)

__all__ = [
    "DomainError",
    "ListQueryError",
    "MultipleTagsForView",
    "TargetEconomicError",
    "TargetFactError",
    "TargetIntakeError",
    "TargetReviewError",
    "TargetTagError",
    "UnknownTagSelector",
]
