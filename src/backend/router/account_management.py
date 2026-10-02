"""Three-layer account metadata routes, separate from immutable Ledger bindings."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.router.dependency import ResourceId, get_db
from backend.router.error import DomainErrorRoute
from backend.schema.account_management import (
    PartyCreate, PartyUpdate, AccountCreate, AccountUpdate, RefCreate, RefUpdate,
    RefMove, RefMoveCommand, PartyResponse, AccountResponse, RefResponse,
    PartyListResponse, AccountListResponse, RefListResponse, MovePreviewResponse,
    PartyListRequest, AccountListRequest, RefListRequest,
    PartySearchRequest, AccountSearchRequest, RefSearchRequest,
    PartySearchResponse, AccountSearchResponse, RefSearchResponse)
from backend.router.bounded_query import bounded_list_dependency, bounded_search_dependency
from backend.service.account_management_service import AccountManagementService

router = APIRouter(prefix="/paam/ledger/v1", tags=["account-management"], route_class=DomainErrorRoute)


def envelope(body):
    return dict(status=200, message="ok", body=body)


@router.get("/account-party/list", response_model=PartyListResponse)
def party_list(request: PartyListRequest = Depends(bounded_list_dependency(PartyListRequest)), db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).page("party", request))


@router.get("/account/list", response_model=AccountListResponse)
def account_list(request: AccountListRequest = Depends(bounded_list_dependency(AccountListRequest)), db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).page("account", request))


@router.get("/account-ref/list", response_model=RefListResponse)
def ref_list(request: RefListRequest = Depends(bounded_list_dependency(RefListRequest)), db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).page("ref", request))


@router.get("/account-party/search", response_model=PartySearchResponse)
def party_search(request: PartySearchRequest = Depends(bounded_search_dependency(PartySearchRequest)), db: Session = Depends(get_db)):
    return PartySearchResponse(status=200, message="ok", body=AccountManagementService(db).search("party", request))


@router.get("/account/search", response_model=AccountSearchResponse)
def account_search(request: AccountSearchRequest = Depends(bounded_search_dependency(AccountSearchRequest)), db: Session = Depends(get_db)):
    return AccountSearchResponse(status=200, message="ok", body=AccountManagementService(db).search("account", request))


@router.get("/account-ref/search", response_model=RefSearchResponse)
def ref_search(request: RefSearchRequest = Depends(bounded_search_dependency(RefSearchRequest)), db: Session = Depends(get_db)):
    return RefSearchResponse(status=200, message="ok", body=AccountManagementService(db).search("ref", request))


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
def party_get(entity_id: ResourceId, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).get("party", entity_id))


@router.get("/account/{entity_id}", response_model=AccountResponse)
def account_get(entity_id: ResourceId, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).get("account", entity_id))


@router.get("/account-ref/{entity_id}", response_model=RefResponse)
def ref_get(entity_id: ResourceId, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).get("ref", entity_id))


@router.put("/account-party/{entity_id}/metadata", response_model=PartyResponse)
def party_update(entity_id: ResourceId, payload: PartyUpdate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).update("party", entity_id, payload))


@router.put("/account/{entity_id}/metadata", response_model=AccountResponse)
def account_update(entity_id: ResourceId, payload: AccountUpdate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).update("account", entity_id, payload))


@router.put("/account-ref/{entity_id}/metadata", response_model=RefResponse)
def ref_update(entity_id: ResourceId, payload: RefUpdate, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).update("ref", entity_id, payload))


@router.post("/account-ref/{entity_id}/move-preview", response_model=MovePreviewResponse)
def ref_move_preview(entity_id: ResourceId, payload: RefMove, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).move_preview(entity_id, payload))


@router.post("/account-ref/{entity_id}/move-command", response_model=RefResponse)
def ref_move_command(entity_id: ResourceId, payload: RefMoveCommand, db: Session = Depends(get_db)):
    return envelope(AccountManagementService(db).move_command(entity_id, payload))
