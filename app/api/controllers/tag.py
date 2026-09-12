from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import TagCommandError
from app.schemas import TagStateBulkAssignmentRequest
from app.schemas.tagging import TagStateBulkResult
from app.services.tag_service import TagService


router = APIRouter(prefix="/api/transactions", tags=["tags"])


@router.put("/bulk-tag-state", response_model=TagStateBulkResult)
def assign_tag_state_bulk(
    payload: TagStateBulkAssignmentRequest,
    db: Session = Depends(get_db),
):
    try:
        return TagService(db).assign_bulk(payload)
    except TagCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
