"""Explicit-column, set-oriented dictionary reads for a bounded tag picker."""
from sqlalchemy import or_, select

from backend.entity import TargetTag, TargetTagView
from backend.mapper.bounded_query_mapper import page_rows, scan_rows


class TagChoiceMapper:
    def __init__(self, db):
        self.db = db

    def statement(self):
        return select(TargetTag.id, TargetTag.name, TargetTag.system_name, TargetTag.status,
            TargetTag.view_id, TargetTag.created_time, TargetTag.updated_time,
            TargetTagView.name.label("view_name"), TargetTagView.status.label("view_status")
        ).outerjoin(TargetTagView, TargetTagView.id == TargetTag.view_id)

    def columns(self):
        return {"id": TargetTag.id, "view_id": TargetTag.view_id,
                "status": TargetTag.status, "view_status": TargetTagView.status,
                "created_time": TargetTag.created_time, "updated_time": TargetTag.updated_time}

    def broken(self):
        # Run before user filters; an orphan must not disappear behind a join,
        # an unrelated View filter, or an empty text search.
        return self.db.scalar(select(TargetTag.id).outerjoin(
            TargetTagView, TargetTagView.id == TargetTag.view_id).where(or_(
            TargetTag.id <= 0, TargetTag.view_id <= 0, TargetTagView.id.is_(None),
            TargetTag.status.not_in(("ACTIVE", "ARCHIVED")),
            TargetTagView.status.not_in(("ACTIVE", "ARCHIVED")))).limit(1)) is not None

    def get(self, tag_id):
        return self.db.execute(self.statement().where(TargetTag.id == tag_id)).mappings().one_or_none()

    def page(self, request):
        return page_rows(self.db, self.statement(), request, self.columns())

    def search(self, request, project):
        return scan_rows(self.db, self.statement(), request, self.columns(),
                         scope="local:tag-v1:tag-choice", project=project)
