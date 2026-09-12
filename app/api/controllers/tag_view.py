from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import TagViewCommandError
from app.schemas import (
    TagViewCreate,
    TagViewRead,
    TagViewUpdate,
    ViewTagCreate,
    ViewTagRead,
    ViewTagUpdate,
)
from app.services.tag_view_service import TagViewService


router = APIRouter(prefix="/api/tag-views", tags=["tags"])


def _http_error(error: TagViewCommandError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.get("", response_model=list[TagViewRead])
def list_tag_views(
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    return TagViewService(db).list(include_archived=include_archived)


@router.post("", response_model=TagViewRead, status_code=201)
def create_tag_view(payload: TagViewCreate, db: Session = Depends(get_db)):
    try:
        return TagViewService(db).create_view(payload)
    except TagViewCommandError as error:
        raise _http_error(error) from error


@router.patch("/{view_id}", response_model=TagViewRead)
def update_tag_view(
    view_id: int,
    payload: TagViewUpdate,
    db: Session = Depends(get_db),
):
    try:
        return TagViewService(db).update_view(view_id, payload)
    except TagViewCommandError as error:
        raise _http_error(error) from error


@router.post("/{view_id}/tags", response_model=ViewTagRead, status_code=201)
def create_view_tag(
    view_id: int,
    payload: ViewTagCreate,
    db: Session = Depends(get_db),
):
    try:
        return TagViewService(db).create_tag(view_id, payload)
    except TagViewCommandError as error:
        raise _http_error(error) from error


@router.patch("/{view_id}/tags/{tag_id}", response_model=ViewTagRead)
def update_view_tag(
    view_id: int,
    tag_id: int,
    payload: ViewTagUpdate,
    db: Session = Depends(get_db),
):
    try:
        return TagViewService(db).update_tag(view_id, tag_id, payload)
    except TagViewCommandError as error:
        raise _http_error(error) from error


@router.delete("/{view_id}/tags/{tag_id}", status_code=204)
def delete_view_tag(
    view_id: int,
    tag_id: int,
    migrate_to_tag_id: int | None = None,
    db: Session = Depends(get_db),
):
    del migrate_to_tag_id
    try:
        TagViewService(db).reject_delete(view_id, tag_id)
    except TagViewCommandError as error:
        raise _http_error(error) from error
