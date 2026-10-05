from fastapi import APIRouter, Request

from backend.router.dependency import validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.unit_dictionary import UnitDictionaryResponse
from backend.service.unit_dictionary_service import UnitDictionaryService

router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger"],
    route_class=DomainErrorRoute,
)


@router.get("/unit", response_model=UnitDictionaryResponse)
def unit_dictionary(request: Request):
    validate_query_parameter_names(request, set())
    return UnitDictionaryResponse(status=200, message="ok", body=UnitDictionaryService().get())
