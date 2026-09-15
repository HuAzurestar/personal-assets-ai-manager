"""Domain error translation shared by PAAM HTTP routers."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute

from backend.service.target_economic_service import TargetEconomicError
from backend.service.target_intake_service import TargetIntakeError
from backend.service.target_review_service import TargetReviewError
from backend.service.target_tag_service import TargetTagError


DOMAIN_ERROR_TYPES = (
    TargetEconomicError,
    TargetIntakeError,
    TargetReviewError,
    TargetTagError,
)


class DomainErrorRoute(APIRoute):
    """Keep the domain-error HTTP contract attached to every Router."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def translated(request: Request) -> Response:
            try:
                return await original(request)
            except DOMAIN_ERROR_TYPES as error:
                raise HTTPException(
                    status_code=error.status_code,
                    detail=str(error),
                ) from error

        return translated
