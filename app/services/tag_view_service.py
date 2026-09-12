from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import TagViewCommandError
from app.mappers.tag_view_mapper import TagViewMapper
from app.schemas import (
    TagViewCreate,
    TagViewRead,
    TagViewUpdate,
    ViewTagCreate,
    ViewTagRead,
    ViewTagUpdate,
)
from app.schemas.tag_view import TagValueDefinitionVO, TagViewDefinitionVO


class TagViewService:
    def __init__(self, db: Session):
        self.db = db
        self.mapper = TagViewMapper(db)

    def list(self, *, include_archived: bool) -> list[TagViewRead]:
        views = self.mapper.views(include_archived=include_archived)
        tags = self.mapper.tags([view.id for view in views])
        return self._view_reads(views, tags)

    def create_view(self, payload: TagViewCreate) -> TagViewRead:
        views = self.mapper.all_views()
        name = payload.name.strip()
        if any(view.name == name for view in views):
            raise TagViewCommandError(409, "Tag view name already exists")
        system_name = payload.system_name or self._generated_system_name(
            "view",
            {view.system_name for view in views},
        )
        if any(view.system_name == system_name for view in views):
            raise TagViewCommandError(409, "Tag view system_name already exists")
        try:
            view, unclassified = self.mapper.create_view(
                name=name,
                system_name=system_name,
                created_at=datetime.now(),
            )
            self.db.commit()
            return self._view_reads((view,), (unclassified,))[0]
        except Exception:
            self.db.rollback()
            raise

    def update_view(self, view_id: int, payload: TagViewUpdate) -> TagViewRead:
        views = self.mapper.all_views()
        view = next((item for item in views if item.id == view_id), None)
        if not view:
            raise TagViewCommandError(404, "Tag view not found")
        tags = self.mapper.tags([view_id])
        name = view.name
        if payload.name and payload.name.strip() != view.name:
            name = payload.name.strip()
            if any(item.name == name and item.id != view_id for item in views):
                raise TagViewCommandError(409, "Tag view name already exists")
        archived = payload.archived if payload.archived is not None else view.archived
        try:
            self.mapper.update_view(
                view_id=view_id,
                name=name,
                archived=archived,
                created_at=datetime.now(),
            )
            self.db.commit()
            updated = TagViewDefinitionVO(
                id=view.id,
                name=name,
                system_name=view.system_name,
                archived=archived,
            )
            return self._view_reads((updated,), tags)[0]
        except Exception:
            self.db.rollback()
            raise

    def create_tag(self, view_id: int, payload: ViewTagCreate) -> ViewTagRead:
        view = next((item for item in self.mapper.all_views() if item.id == view_id), None)
        if not view:
            raise TagViewCommandError(404, "Tag view not found")
        tags = self.mapper.tags([view_id])
        name = payload.name.strip()
        if any(tag.name == name for tag in tags):
            raise TagViewCommandError(409, "Tag name already exists in this view")
        system_name = payload.system_name or self._generated_system_name(
            "tag",
            {tag.system_name for tag in tags},
        )
        if any(tag.system_name == system_name for tag in tags):
            raise TagViewCommandError(409, "Tag system_name already exists in this view")
        try:
            tag = self.mapper.create_tag(
                view_id=view_id,
                name=name,
                system_name=system_name,
                created_at=datetime.now(),
            )
            self.db.commit()
            return self._tag_read(tag)
        except Exception:
            self.db.rollback()
            raise

    def update_tag(
        self,
        view_id: int,
        tag_id: int,
        payload: ViewTagUpdate,
    ) -> ViewTagRead:
        tags = self.mapper.tags([view_id])
        tag = next((item for item in tags if item.id == tag_id), None)
        if not tag:
            raise TagViewCommandError(404, "Tag not found")
        if tag.is_unclassified and (payload.name or payload.archived is not None):
            raise TagViewCommandError(422, "The unclassified tag is protected")
        name = tag.name
        if payload.name and payload.name.strip() != tag.name:
            name = payload.name.strip()
            if any(item.name == name and item.id != tag.id for item in tags):
                raise TagViewCommandError(409, "Tag name already exists in this view")
        archived = payload.archived if payload.archived is not None else tag.archived
        try:
            self.mapper.update_tag(
                tag_id=tag.id,
                view_id=view_id,
                name=name,
                archived=archived,
                created_at=datetime.now(),
            )
            self.db.commit()
            return self._tag_read(TagValueDefinitionVO(
                id=tag.id,
                view_id=tag.view_id,
                name=name,
                system_name=tag.system_name,
                is_unclassified=tag.is_unclassified,
                archived=archived,
            ))
        except Exception:
            self.db.rollback()
            raise

    def reject_delete(self, view_id: int, tag_id: int) -> None:
        tag = next(
            (item for item in self.mapper.tags([view_id]) if item.id == tag_id),
            None,
        )
        if not tag:
            raise TagViewCommandError(404, "Tag not found")
        if tag.is_unclassified:
            raise TagViewCommandError(422, "The unclassified tag is protected")
        raise TagViewCommandError(409, "为保留当前标签与历史解释，请使用归档，不支持物理删除标签")

    @classmethod
    def _view_reads(
        cls,
        views: tuple[TagViewDefinitionVO, ...],
        tags: tuple[TagValueDefinitionVO, ...],
    ) -> list[TagViewRead]:
        tags_by_view: dict[int, list[ViewTagRead]] = {}
        for tag in tags:
            tags_by_view.setdefault(tag.view_id, []).append(cls._tag_read(tag))
        return [TagViewRead(
            id=view.id,
            name=view.name,
            system_name=view.system_name,
            archived=view.archived,
            tags=tags_by_view.get(view.id, []),
        ) for view in views]

    @staticmethod
    def _tag_read(tag: TagValueDefinitionVO) -> ViewTagRead:
        return ViewTagRead(
            id=tag.id,
            name=tag.name,
            system_name=tag.system_name,
            is_unclassified=tag.is_unclassified,
            archived=tag.archived,
        )

    @staticmethod
    def _generated_system_name(prefix: str, used: set[str]) -> str:
        for number in range(1, 100000):
            candidate = f"{prefix}_{number}"
            if candidate not in used:
                return candidate
        raise TagViewCommandError(409, "No available system name")
