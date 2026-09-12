from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import ImportIssueCommandError
from app.schemas import BillRead, IssueResolve, UndoRequest
from app.schemas.import_issue import ImportIssuePageRead, ImportIssueRead, ImportIssueStatusRead
from app.services.import_issue_service import ImportIssueService


router = APIRouter(prefix="/api/import-issues", tags=["imports"])


@router.get("", response_model=list[ImportIssueRead])
def list_import_issues(db: Session = Depends(get_db)):
    return ImportIssueService(db).list()


@router.get("/page", response_model=ImportIssuePageRead)
def page_import_issues(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return ImportIssueService(db).page(page=page, page_size=page_size)


@router.get("/{issue_id}", response_model=ImportIssueRead)
def get_import_issue(issue_id: int, db: Session = Depends(get_db)):
    try:
        return ImportIssueService(db).get(issue_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/{issue_id}/resolve", response_model=BillRead)
def resolve_import_issue(
    issue_id: int,
    payload: IssueResolve,
    db: Session = Depends(get_db),
):
    try:
        return ImportIssueService(db).resolve(issue_id, payload)
    except ImportIssueCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post("/{issue_id}/dismiss", response_model=ImportIssueStatusRead)
def dismiss_import_issue(
    issue_id: int,
    payload: UndoRequest,
    db: Session = Depends(get_db),
):
    try:
        return ImportIssueService(db).dismiss(issue_id, payload)
    except ImportIssueCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post("/{issue_id}/reopen", response_model=ImportIssueStatusRead)
def reopen_import_issue(
    issue_id: int,
    payload: UndoRequest,
    db: Session = Depends(get_db),
):
    try:
        return ImportIssueService(db).reopen(issue_id, payload)
    except ImportIssueCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
