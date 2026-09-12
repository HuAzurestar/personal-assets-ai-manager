from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.mappers.target_tag_mapper import TargetTagMapper
from app.schemas.target_tag import (
    TargetTagCreateRequest,
    TargetTagStatusRequest,
    TargetTagViewCreateRequest,
    TargetTagViewRead,
)
from app.services.target_tag_projection_service import TargetTagProjectionService


class TargetTagError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class TargetTagService:
    def __init__(self, db: Session):
        self.mapper = TargetTagMapper(db)
        self.projection = TargetTagProjectionService(db)

    def list(self, include_archived: bool = False) -> list[TargetTagViewRead]:
        return self.mapper.list(include_archived)

    def create_view(self, payload: TargetTagViewCreateRequest) -> TargetTagViewRead:
        try:
            view_id = self.mapper.create_view(
                payload.name.strip(), payload.system_name, datetime.now()
            )
            self.projection.sync_all()
            self.mapper.commit()
            return self._required(view_id)
        except IntegrityError as error:
            self.mapper.rollback()
            raise TargetTagError(409, "tag view system name already exists") from error

    def create_tag(
        self,
        view_id: int,
        payload: TargetTagCreateRequest,
    ) -> TargetTagViewRead:
        if self.mapper.view(view_id) is None:
            raise TargetTagError(404, "tag view not found")
        if payload.system_name == "unclassified":
            raise TargetTagError(422, "unclassified is managed by the tag view")
        try:
            self.mapper.create_tag(
                view_id, payload.name.strip(), payload.system_name, datetime.now()
            )
            self.mapper.commit()
            return self._required(view_id)
        except IntegrityError as error:
            self.mapper.rollback()
            raise TargetTagError(409, "tag system name already exists in this view") from error

    def set_view_status(
        self,
        view_id: int,
        payload: TargetTagStatusRequest,
    ) -> TargetTagViewRead:
        if not self.mapper.set_view_status(view_id, payload.status, datetime.now()):
            raise TargetTagError(404, "tag view not found")
        self.projection.sync_all()
        self.mapper.commit()
        return self._required(view_id)

    def set_tag_status(
        self,
        view_id: int,
        tag_id: int,
        payload: TargetTagStatusRequest,
    ) -> TargetTagViewRead:
        view = self._required(view_id)
        tag = next((item for item in view.tags if item.id == tag_id), None)
        if tag is None:
            raise TargetTagError(404, "tag not found in this view")
        if tag.system_name == "unclassified" and payload.status != "ACTIVE":
            raise TargetTagError(422, "unclassified tag cannot be archived")
        if not self.mapper.set_tag_status(view_id, tag_id, payload.status, datetime.now()):
            raise TargetTagError(404, "tag not found in this view")
        self.projection.sync_all()
        self.mapper.commit()
        return self._required(view_id)

    def _required(self, view_id: int) -> TargetTagViewRead:
        view = self.mapper.view(view_id)
        if view is None:
            raise TargetTagError(404, "tag view not found")
        return view
