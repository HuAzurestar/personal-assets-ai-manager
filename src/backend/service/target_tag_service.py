from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from pypinyin import Style, lazy_pinyin
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.error import TargetTagError
from backend.entity.base import utc_now
from backend.mapper.target_tag_mapper import TargetTagMapper
from backend.schema.list_query import BetweenValue, iter_filter_fields
from backend.schema.target_tag import (
    TargetTagCreateRequest,
    TargetTagStatusRequest,
    TargetTagSystemNameRead,
    TargetTagViewCreateRequest,
    TargetTagViewFilter,
    TargetTagViewListBody,
    TargetTagViewListRequest,
    TargetTagViewRead,
    TargetTagViewSorter,
    parse_tag_view_time,
)
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetTagService:
    MAX_VIEWS = 100

    def __init__(self, db: Session):
        self.mapper = TargetTagMapper(db)
        self.projection = TargetTagProjectionService(db)

    def list(
        self,
        *,
        request: TargetTagViewListRequest,
    ) -> TargetTagViewListBody:
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = TargetTagViewSorter(
            field=sorter_expression.key if sorter_expression else "id",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        return self.mapper.list(
            request.page_index,
            request.page_size,
            self._mapper_filter(request),
            sorter,
        )

    @staticmethod
    def preview_system_name(name: str) -> TargetTagSystemNameRead:
        normalized = unicodedata.normalize("NFKC", name).strip()
        parts = lazy_pinyin(
            normalized,
            style=Style.NORMAL,
            errors=lambda value: [value],
        )
        system_name = re.sub(r"[^a-z0-9]+", "_", "_".join(parts).lower())
        system_name = system_name.strip("_")
        if not system_name:
            system_name = "tag"
        elif not system_name[0].isalpha():
            system_name = f"tag_{system_name}"
        system_name = system_name[:64].rstrip("_")
        return TargetTagSystemNameRead(system_name=system_name)

    @staticmethod
    def _mapper_filter(request: TargetTagViewListRequest) -> TargetTagViewFilter:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key in {"created_time", "updated_time"}:
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values[f"{expression.key}_start"] = parse_tag_view_time(
                        between.start,
                        expression.key,
                    )
                    values[f"{expression.key}_end"] = parse_tag_view_time(
                        between.end,
                        expression.key,
                    )
                elif expression.op == ">=":
                    values[f"{expression.key}_start"] = parse_tag_view_time(
                        expression.val,
                        expression.key,
                    )
                else:
                    values[f"{expression.key}_end"] = parse_tag_view_time(
                        expression.val,
                        expression.key,
                    )
            else:
                values[expression.key] = expression.val
        return TargetTagViewFilter.model_validate(values)

    def create_view(self, payload: TargetTagViewCreateRequest) -> TargetTagViewRead:
        if self.mapper.view_count() >= self.MAX_VIEWS:
            raise TargetTagError(422, "tag view limit is 100")
        try:
            view_id = self.mapper.create_view(
                payload.name.strip(), payload.system_name, utc_now()
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
                view_id, payload.name.strip(), payload.system_name, utc_now()
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
        try:
            if not self.mapper.set_view_status(view_id, payload.status, utc_now()):
                raise TargetTagError(404, "tag view not found")
            self.projection.sync_all()
            self.mapper.commit()
            return self._required(view_id)
        except TargetTagError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetTagError(
                409, f"tag view status conflicts with current ledger tags: {error}"
            ) from error

    def set_tag_status(
        self,
        view_id: int,
        tag_id: int,
        payload: TargetTagStatusRequest,
    ) -> TargetTagViewRead:
        try:
            view = self._required(view_id)
            tag = next((item for item in view.tags if item.id == tag_id), None)
            if tag is None:
                raise TargetTagError(404, "tag not found in this view")
            if tag.system_name == "unclassified" and payload.status != "ACTIVE":
                raise TargetTagError(422, "unclassified tag cannot be archived")
            if not self.mapper.set_tag_status(view_id, tag_id, payload.status, utc_now()):
                raise TargetTagError(404, "tag not found in this view")
            self.projection.sync_all()
            self.mapper.commit()
            return self._required(view_id)
        except TargetTagError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetTagError(
                409, f"tag status conflicts with current ledger tags: {error}"
            ) from error

    def _required(self, view_id: int) -> TargetTagViewRead:
        view = self.mapper.view(view_id)
        if view is None:
            raise TargetTagError(404, "tag view not found")
        return view
