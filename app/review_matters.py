"""Compatibility helpers for callers not yet moved into Review services."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.schemas.matter import MatterLine, MatterUndo, MatterWrite
from app.services.matter_service import MatterService


def current_matters(db: Session) -> list[dict]:
    return [
        matter
        for matter in MatterService(db).current_snapshots()
        if matter["action"] != "revoke"
    ]


def allocated_bills(db: Session, except_matter: int | None = None) -> dict[int, int]:
    return MatterService(db).allocated_bills(except_matter)


def assert_no_matters(db: Session, bill_ids: set[int]) -> None:
    conflicts = MatterService(db).conflicting_matter_ids(bill_ids)
    if conflicts:
        raise HTTPException(409, f"流水已用于手工事项 {conflicts}；请先修改或撤销相关分配")


__all__ = [
    "MatterLine",
    "MatterUndo",
    "MatterWrite",
    "allocated_bills",
    "assert_no_matters",
    "current_matters",
]
