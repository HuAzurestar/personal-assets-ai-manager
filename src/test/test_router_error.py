from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient

from backend.error import TargetReviewError
from backend.router.error import DomainErrorRoute, register_error_handlers


def _client() -> TestClient:
    router = APIRouter(route_class=DomainErrorRoute)

    @router.get("/domain")
    def domain_error():
        raise TargetReviewError(409, "review changed")

    @router.get("/http")
    def http_error():
        raise HTTPException(status_code=404, detail="resource missing")

    @router.get("/validation")
    def validation_error(page: int = Query(ge=1)):
        return page

    @router.get("/unexpected")
    def unexpected_error():
        raise RuntimeError("private implementation detail")

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    return TestClient(app)


def test_business_router_uses_one_error_envelope():
    client = _client()

    domain = client.get("/domain")
    assert domain.status_code == 409
    assert domain.json() == {
        "status": 409,
        "message": "review changed",
        "body": {"code": "REVIEW_ERROR"},
    }
    http = client.get("/http")
    assert http.status_code == 404
    assert http.json() == {
        "status": 404,
        "message": "resource missing",
        "body": {"code": "HTTP_404"},
    }

    validation = client.get("/validation?page=0")
    assert validation.status_code == 422
    assert validation.json()["status"] == 422
    assert validation.json()["message"] == "Request validation failed"
    assert validation.json()["body"]["code"] == "VALIDATION_ERROR"
    assert validation.json()["body"]["details"]

    unexpected = client.get("/unexpected")
    assert unexpected.status_code == 500
    assert unexpected.json() == {
        "status": 500,
        "message": "Internal server error",
        "body": {"code": "INTERNAL_SERVER_ERROR"},
    }
    assert "private implementation detail" not in unexpected.text


def test_application_handler_formats_unmatched_route():
    client = _client()

    missing = client.get("/missing")
    assert missing.status_code == 404
    assert missing.json() == {
        "status": 404,
        "message": "Not Found",
        "body": {"code": "HTTP_404"},
    }
