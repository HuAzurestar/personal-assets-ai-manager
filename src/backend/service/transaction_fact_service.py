"""Compatibility import; all Fact reads use the canonical snapshot service."""
from backend.service.fact_read_service import FactReadService
from backend.schema.fact_query import FactListRequest


class TransactionFactService(FactReadService):
    def page(self, *, request):
        canonical = FactListRequest.model_validate(request.model_dump())
        return super().page(canonical)
