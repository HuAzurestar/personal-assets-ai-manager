from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, func, insert, literal, or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    TargetTag,
    TargetTagView,
)
from backend.schema.target_tag import (
    TargetTagRead,
    TargetTagViewFilter,
    TargetTagViewPageRead,
    TargetTagViewRead,
    TargetTagViewSorter,
)


class TargetTagMapper:
    """Explicit SQL boundary for the target tag dictionary."""

    def __init__(self, db: Session):
        self.db = db

    def list(
        self,
        page: int,
        page_size: int,
        q: str,
        filter_value: TargetTagViewFilter,
        sorter: TargetTagViewSorter,
    ) -> TargetTagViewPageRead:
        conditions = []
        if q:
            pattern = f"%{q}%"
            conditions.append(or_(
                TargetTagView.name.like(pattern),
                TargetTagView.system_name.like(pattern),
            ))
        if filter_value.status:
            conditions.append(TargetTagView.status == filter_value.status)
        elif not filter_value.include_archived:
            conditions.append(TargetTagView.status == "ACTIVE")
        total = self.db.scalar(select(func.count(TargetTagView.id)).where(
            *conditions,
        )) or 0
        view_statement = select(
            TargetTagView.id,
            TargetTagView.name,
            TargetTagView.system_name,
            TargetTagView.status,
        )
        sort_columns = {
            "id": TargetTagView.id,
            "name": TargetTagView.name,
            "system_name": TargetTagView.system_name,
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
        if not filter_value.include_archived and filter_value.status != "ARCHIVED":
            tag_statement = tag_statement.where(TargetTag.status == "ACTIVE")
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
        return TargetTagViewPageRead(
            items=items,
            total=int(total),
            page=page,
            page_size=page_size,
            q=q,
            filter=filter_value,
            sorter=sorter,
        )

    def view(self, view_id: int) -> TargetTagViewRead | None:
        view = self.db.execute(select(
            TargetTagView.id,
            TargetTagView.name,
            TargetTagView.system_name,
            TargetTagView.status,
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
        self._assign_missing(view.id, tag.id)
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
        if status == "ACTIVE":
            tag_id = self.db.scalar(select(TargetTag.id).where(
                TargetTag.view_id == view_id,
                TargetTag.system_name == "unclassified",
                TargetTag.status == "ACTIVE",
            ))
            if tag_id:
                self._assign_missing(view_id, tag_id)
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

    def _assign_missing(self, view_id: int, tag_id: int) -> None:
        existing = exists(select(LedgerEntryTag.id).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).where(
            LedgerEntryTag.ledger_id == LedgerEntry.id,
            TargetTag.view_id == view_id,
        ))
        self.db.execute(insert(LedgerEntryTag).from_select(
            ["ledger_id", "tag_id"],
            select(LedgerEntry.id, literal(tag_id)).where(
                ~existing,
            ),
        ))

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
