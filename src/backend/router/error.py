"""Domain error translation shared by PAAM HTTP routers."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute

from backend.error import DomainError


class DomainErrorRoute(APIRoute):
    """Keep the domain-error HTTP contract attached to every Router."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def translated(request: Request) -> Response:
            try:
                return await original(request)
            except DomainError as error:
                raise HTTPException(
                    status_code=error.status_code,
                    detail=str(error),
                ) from error

        return translated
