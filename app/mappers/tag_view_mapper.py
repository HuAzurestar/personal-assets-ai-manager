from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.database import TagChangeLog, TagView, ViewTag
from app.schemas.tag_view import TagValueDefinitionVO, TagViewDefinitionVO


class TagViewMapper:
    """Explicit-column, set-based SQL for tag definitions."""

    def __init__(self, db: Session):
        self.db = db

    def views(self, *, include_archived: bool) -> tuple[TagViewDefinitionVO, ...]:
        statement = select(
            TagView.id,
            TagView.name,
            TagView.system_name,
            TagView.archived,
        )
        if not include_archived:
            statement = statement.where(TagView.archived.is_(False))
        rows = self.db.execute(statement.order_by(TagView.id)).mappings().all()
        return tuple(TagViewDefinitionVO(**row) for row in rows)

    def all_views(self) -> tuple[TagViewDefinitionVO, ...]:
        return self.views(include_archived=True)

    def tags(self, view_ids: list[int]) -> tuple[TagValueDefinitionVO, ...]:
        if not view_ids:
            return ()
        rows = self.db.execute(
            select(
                ViewTag.id,
                ViewTag.view_id,
                ViewTag.name,
                ViewTag.system_name,
                ViewTag.is_unclassified,
                ViewTag.archived,
            )
            .where(ViewTag.view_id.in_(view_ids))
            .order_by(
                ViewTag.view_id,
                ViewTag.is_unclassified.desc(),
                ViewTag.name,
            )
        ).mappings().all()
        return tuple(TagValueDefinitionVO(**row) for row in rows)

    def create_view(
        self,
        *,
        name: str,
        system_name: str,
        created_at: datetime,
    ) -> tuple[TagViewDefinitionVO, TagValueDefinitionVO]:
        view = TagView(
            name=name,
            system_name=system_name,
            archived=False,
            created_at=created_at,
        )
        self.db.add(view)
        self.db.flush()
        tag = ViewTag(
            view_id=view.id,
            name="未分类",
            system_name="unclassified",
            is_unclassified=True,
            archived=False,
        )
        self.db.add(tag)
        self.db.flush()
        self._append_log(
            view_id=view.id,
            tag_id=None,
            action="create_view",
            detail=view.name,
            created_at=created_at,
        )
        return (
            TagViewDefinitionVO(
                id=view.id,
                name=view.name,
                system_name=view.system_name,
                archived=False,
            ),
            TagValueDefinitionVO(
                id=tag.id,
                view_id=view.id,
                name=tag.name,
                system_name=tag.system_name,
                is_unclassified=True,
                archived=False,
            ),
        )

    def update_view(
        self,
        *,
        view_id: int,
        name: str,
        archived: bool,
        created_at: datetime,
    ) -> None:
        self.db.execute(
            update(TagView)
            .where(TagView.id == view_id)
            .values(name=name, archived=archived)
        )
        self._append_log(
            view_id=view_id,
            tag_id=None,
            action="update_view",
            detail=name,
            created_at=created_at,
        )

    def create_tag(
        self,
        *,
        view_id: int,
        name: str,
        system_name: str,
        created_at: datetime,
    ) -> TagValueDefinitionVO:
        tag = ViewTag(
            view_id=view_id,
            name=name,
            system_name=system_name,
            is_unclassified=False,
            archived=False,
        )
        self.db.add(tag)
        self.db.flush()
        self._append_log(
            view_id=view_id,
            tag_id=tag.id,
            action="create_tag",
            detail=tag.name,
            created_at=created_at,
        )
        return TagValueDefinitionVO(
            id=tag.id,
            view_id=view_id,
            name=tag.name,
            system_name=tag.system_name,
            is_unclassified=False,
            archived=False,
        )

    def update_tag(
        self,
        *,
        tag_id: int,
        view_id: int,
        name: str,
        archived: bool,
        created_at: datetime,
    ) -> None:
        self.db.execute(
            update(ViewTag)
            .where(ViewTag.id == tag_id, ViewTag.view_id == view_id)
            .values(name=name, archived=archived)
        )
        self._append_log(
            view_id=view_id,
            tag_id=tag_id,
            action="update_tag",
            detail=name,
            created_at=created_at,
        )

    def _append_log(
        self,
        *,
        view_id: int,
        tag_id: int | None,
        action: str,
        detail: str,
        created_at: datetime,
    ) -> None:
        self.db.execute(insert(TagChangeLog).values(
            view_id=view_id,
            tag_id=tag_id,
            action=action,
            detail=detail,
            created_at=created_at,
        ))
