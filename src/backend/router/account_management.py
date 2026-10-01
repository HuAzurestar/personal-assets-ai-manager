"""Three-layer account metadata routes, separate from immutable Ledger bindings."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.account_management import (
    PartyCreate, PartyUpdate, AccountCreate, AccountUpdate, RefCreate, RefUpdate,
    RefMove, RefMoveCommand, PartyResponse, AccountResponse, RefResponse,
    PartyListResponse, AccountListResponse, RefListResponse, MovePreviewResponse)
from backend.schema.list_query import ListRequest, parse_list_request
from backend.service.account_management_service import AccountManagementService

router = APIRouter(prefix="/paam/ledger/v1", tags=["account-management"], route_class=DomainErrorRoute)


def envelope(body):
    return dict(status=200, message="ok", body=body)


def metadata_request(http_request: Request, page_index: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), filter: str | None = None, sorter: str | None = None):
    validate_query_parameter_names(http_request, {"page_index", "page_size", "filter", "sorter"})
    return parse_list_request(ListRequest, page_index=page_index, page_size=page_size, filter=filter, sorter=sorter)


@router.get("/account-party/list", response_model=PartyListResponse)
def party_list(request: ListRequest = Depends(metadata_request), db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).page("party", request))


@router.get("/account/list", response_model=AccountListResponse)
def account_list(request: ListRequest = Depends(metadata_request), db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).page("account", request))


@router.get("/account-ref/list", response_model=RefListResponse)
def ref_list(request: ListRequest = Depends(metadata_request), db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).page("ref", request))


@router.post("/account-party", response_model=PartyResponse)
def party_create(payload: PartyCreate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).create("party", payload))


@router.post("/account", response_model=AccountResponse)
def account_create(payload: AccountCreate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).create("account", payload))


@router.post("/account-ref", response_model=RefResponse)
def ref_create(payload: RefCreate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).create("ref", payload))


@router.get("/account-party/{entity_id}", response_model=PartyResponse)
def party_get(entity_id: int, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).get("party", entity_id))


@router.get("/account/{entity_id}", response_model=AccountResponse)
def account_get(entity_id: int, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).get("account", entity_id))


@router.get("/account-ref/{entity_id}", response_model=RefResponse)
def ref_get(entity_id: int, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).get("ref", entity_id))


@router.put("/account-party/{entity_id}/metadata", response_model=PartyResponse)
def party_update(entity_id: int, payload: PartyUpdate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).update("party", entity_id, payload))


@router.put("/account/{entity_id}/metadata", response_model=AccountResponse)
def account_update(entity_id: int, payload: AccountUpdate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).update("account", entity_id, payload))


@router.put("/account-ref/{entity_id}/metadata", response_model=RefResponse)
def ref_update(entity_id: int, payload: RefUpdate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).update("ref", entity_id, payload))


@router.post("/account-ref/{entity_id}/move-preview", response_model=MovePreviewResponse)
def ref_move_preview(entity_id: int, payload: RefMove, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).move_preview(entity_id, payload))


@router.post("/account-ref/{entity_id}/move-command", response_model=RefResponse)
def ref_move_command(entity_id: int, payload: RefMoveCommand, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).move_command(entity_id, payload))
