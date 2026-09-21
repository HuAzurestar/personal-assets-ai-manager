from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    TargetTag,
    TargetTagView,
)
from backend.schema.target_tag import (
    TargetTagRead,
    TargetTagViewFilter,
    TargetTagViewListBody,
    TargetTagViewRead,
    TargetTagViewSorter,
)


class TargetTagMapper:
    """Explicit SQL boundary for the target tag dictionary."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def view_count(self) -> int:
        return int(self.db.scalar(select(func.count(TargetTagView.id))) or 0)

    def list(
        self,
        page: int,
        page_size: int,
        filter_value: TargetTagViewFilter,
        sorter: TargetTagViewSorter,
    ) -> TargetTagViewListBody:
        conditions = []
        if filter_value.id:
            conditions.append(TargetTagView.id == filter_value.id)
        if filter_value.status:
            conditions.append(TargetTagView.status == filter_value.status)
        if filter_value.created_time_start:
            conditions.append(TargetTagView.created_time >= filter_value.created_time_start)
        if filter_value.created_time_end:
            conditions.append(TargetTagView.created_time < filter_value.created_time_end)
        if filter_value.updated_time_start:
            conditions.append(TargetTagView.updated_time >= filter_value.updated_time_start)
        if filter_value.updated_time_end:
            conditions.append(TargetTagView.updated_time < filter_value.updated_time_end)
        total = self.db.scalar(select(func.count(TargetTagView.id)).where(
            *conditions,
        )) or 0
        view_statement = select(
            TargetTagView.id,
            TargetTagView.name,
            TargetTagView.system_name,
            TargetTagView.status,
            TargetTagView.created_time,
            TargetTagView.updated_time,
        )
        sort_columns = {
            "id": TargetTagView.id,
            "created_time": TargetTagView.created_time,
            "updated_time": TargetTagView.updated_time,
        }
        column = sort_columns[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = TargetTagView.id.asc() if sorter.order == "asc" else TargetTagView.id.desc()
        views = self.db.execute(view_statement.where(*conditions).order_by(
            order, id_order,
        ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        view_ids = [row["id"] for row in views]
        tag_statement = select(
            TargetTag.id,
            TargetTag.view_id,
            TargetTag.name,
            TargetTag.system_name,
            TargetTag.status,
        ).where(TargetTag.view_id.in_(view_ids))
        tags = self.db.execute(tag_statement.order_by(
            TargetTag.view_id,
            TargetTag.id,
        )).mappings().all() if view_ids else []
        by_view: dict[int, list[TargetTagRead]] = {}
        for row in tags:
            by_view.setdefault(row["view_id"], []).append(TargetTagRead(
                id=row["id"],
                name=row["name"],
                system_name=row["system_name"],
                status=row["status"],
            ))
        items = [TargetTagViewRead(
            **row,
            tags=by_view.get(row["id"], []),
        ) for row in views]
        return TargetTagViewListBody(
            items=items,
            total=int(total),
            page_index=page,
            page_size=page_size,
        )

    def view(self, view_id: int) -> TargetTagViewRead | None:
        view = self.db.execute(select(
            TargetTagView.id,
            TargetTagView.name,
            TargetTagView.system_name,
            TargetTagView.status,
            TargetTagView.created_time,
            TargetTagView.updated_time,
        ).where(TargetTagView.id == view_id)).mappings().one_or_none()
        if view is None:
            return None
        tags = self.db.execute(select(
            TargetTag.id,
            TargetTag.name,
            TargetTag.system_name,
            TargetTag.status,
        ).where(TargetTag.view_id == view_id).order_by(TargetTag.id)).mappings().all()
        return TargetTagViewRead(
            **view,
            tags=[TargetTagRead(**row) for row in tags],
        )

    def create_view(self, name: str, system_name: str, now: datetime) -> int:
        view = TargetTagView(
            name=name,
            system_name=system_name,
            status="ACTIVE",
            created_time=now,
            updated_time=now,
        )
        self.db.add(view)
        self.db.flush()
        tag = TargetTag(
            view_id=view.id,
            name="未分类",
            system_name="unclassified",
            status="ACTIVE",
            created_time=now,
            updated_time=now,
        )
        self.db.add(tag)
        self.db.flush()
        return view.id

    def create_tag(self, view_id: int, name: str, system_name: str, now: datetime) -> None:
        self.db.add(TargetTag(
            view_id=view_id,
            name=name,
            system_name=system_name,
            status="ACTIVE",
            created_time=now,
            updated_time=now,
        ))
        self.db.flush()

    def set_view_status(self, view_id: int, status: str, now: datetime) -> bool:
        view = self.db.get(TargetTagView, view_id)
        if view is None:
            return False
        view.status = status
        view.updated_time = now
        self.db.flush()
        return True

    def set_tag_status(self, view_id: int, tag_id: int, status: str, now: datetime) -> bool:
        tag = self.db.get(TargetTag, tag_id)
        if tag is None or tag.view_id != view_id:
            return False
        tag.status = status
        tag.updated_time = now
        self.db.flush()
        return True

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
