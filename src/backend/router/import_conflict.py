"""Retired source-row pseudo-Review/history API; use selected import rows."""
from fastapi import APIRouter
from backend.router.error import DomainErrorRoute
from backend.error import TargetIntakeError

router = APIRouter(prefix="/paam/import/v1", tags=["import-fact-conflict"], route_class=DomainErrorRoute)


@router.get("/fact_conflict/list", deprecated=True)
@router.get("/fact_conflict/{conflict_id}", deprecated=True)
@router.post("/fact_conflict/{conflict_id}/resolve", deprecated=True)
@router.post("/fact_conflict/{conflict_id}/dismiss", deprecated=True)
@router.post("/fact_conflict/{conflict_id}/reopen", deprecated=True)
def retired(conflict_id: int | None = None):
    raise TargetIntakeError(410, "use source-row reads and explicit preview/recheck/confirm; never rewrite source evidence",
                           code="IMPORT_WRITE_RETIRED")
