class UnknownTagSelector(ValueError):
    """A requested tag view or value does not exist or is archived."""


class MultipleTagsForView(ValueError):
    """A query selected more than one exclusive value in a tag view."""


class ReviewCommandError(Exception):
    """A Review command failed without coupling the Service to HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TagCommandError(Exception):
    """A tag command failed without coupling the Service to HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class MatterCommandError(Exception):
    """A manual Review matter command failed independently of HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TagViewCommandError(Exception):
    """A tag-definition command failed independently of HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RefundCommandError(Exception):
    """A refund command failed independently of HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ImportIssueCommandError(Exception):
    """An import issue command failed independently of HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
