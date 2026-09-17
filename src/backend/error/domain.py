"""Domain errors that may cross the backend's service boundary."""

from typing import Any


class DomainError(Exception):
    """A domain failure with enough metadata for an HTTP adapter."""

    default_code = "DOMAIN_ERROR"

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code or self.default_code
        self.details = details or {}


class TargetIntakeError(DomainError):
    default_code = "INTAKE_ERROR"


class TargetEconomicError(DomainError):
    default_code = "ECONOMIC_ERROR"


class TargetFactError(DomainError):
    default_code = "FACT_ERROR"


class ListQueryError(DomainError):
    default_code = "LIST_QUERY_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(422, message, code=code, details=details)


class TargetReviewError(DomainError):
    default_code = "REVIEW_ERROR"


class TargetTagError(DomainError):
    default_code = "TAG_ERROR"


class UnknownTagSelector(DomainError):
    """A requested tag view or value does not exist or is archived."""

    default_code = "UNKNOWN_TAG_SELECTOR"

    def __init__(self, message: str):
        super().__init__(422, message)


class MultipleTagsForView(DomainError):
    """A query selected more than one exclusive value in a tag view."""

    default_code = "MULTIPLE_TAGS_FOR_VIEW"

    def __init__(self, message: str):
        super().__init__(400, message)
