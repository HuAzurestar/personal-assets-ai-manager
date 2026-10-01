"""Shared two-second write scope for manual and approved tag decisions."""
from time import monotonic

from backend.error import TargetTagError
from backend.mapper.review_command_mapper import ReviewCommandMapper


class TagWriteMapper:
    def __init__(self, db):
        self.db = db
        self.writer = ReviewCommandMapper(db)

    def begin_write(self):
        self.writer.begin_write()

    def commit(self):
        if monotonic() - self.writer.write_started > 2:
            raise TargetTagError(503, "tag write budget exceeded", code="WRITE_BUSY")
        self.writer.end_write()
        try:
            self.db.commit()
        except Exception as error:
            raise TargetTagError(503, "tag commit outcome uncertain; inspect current state", code="RESULT_UNKNOWN") from error

    def rollback(self):
        self.writer.end_write()
        self.db.rollback()
