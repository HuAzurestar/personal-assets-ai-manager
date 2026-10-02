"""Retire the pseudo-Review source-history writer at the service boundary too."""
from backend.error import TargetIntakeError


class TargetFactConflictService:
    def __init__(self, db):
        self.db = db

    def retired(self, *_args, **_kwargs):
        raise TargetIntakeError(410, "use explicit import choices/recheck; original evidence is immutable",
                               code="IMPORT_WRITE_RETIRED")

    page = detail = resolve = dismiss = reopen = retired
