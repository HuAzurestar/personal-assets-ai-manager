from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import MatterCommandError
from app.schemas.matter import MatterPageRead, MatterRead, MatterUndo, MatterWrite
from app.services.matter_service import MatterService


router = APIRouter(prefix="/api/review-matters", tags=["review"])


def _http_error(error: MatterCommandError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.get("", response_model=list[MatterRead])
def list_matters(db: Session = Depends(get_db)):
    try:
        return MatterService(db).list()
    except MatterCommandError as error:
        raise _http_error(error) from error


@router.get("/page", response_model=MatterPageRead)
def page_matters(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return MatterService(db).page(page=page, page_size=page_size)


@router.get("/{matter_id}", response_model=MatterRead)
def get_matter(matter_id: int, db: Session = Depends(get_db)):
    try:
        return MatterService(db).get(matter_id)
    except MatterCommandError as error:
        raise _http_error(error) from error


@router.post("", response_model=MatterRead, status_code=201)
def create_matter(payload: MatterWrite, db: Session = Depends(get_db)):
    try:
        return MatterService(db).create(payload)
    except MatterCommandError as error:
        raise _http_error(error) from error


@router.put("/{matter_id}", response_model=MatterRead)
def replace_matter(
    matter_id: int,
    payload: MatterWrite,
    db: Session = Depends(get_db),
):
    try:
        return MatterService(db).replace(matter_id, payload)
    except MatterCommandError as error:
        raise _http_error(error) from error


@router.post("/{matter_id}/undo", response_model=MatterRead)
def undo_matter(
    matter_id: int,
    payload: MatterUndo,
    db: Session = Depends(get_db),
):
    try:
        return MatterService(db).undo(matter_id, payload)
    except MatterCommandError as error:
        raise _http_error(error) from error
