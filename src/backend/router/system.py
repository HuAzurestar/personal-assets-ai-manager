"""System and frontend entry-point HTTP routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.core.config import APP_DISPLAY_NAME, RESOURCE_DIR


router = APIRouter(
    tags=["system"],
)
templates = Jinja2Templates(directory=RESOURCE_DIR / "frontend")


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="target.html",
        context={"app_name": APP_DISPLAY_NAME},
    )


@router.get("/api/health")
def health():
    return {"status": "ok", "schema": "pirc-9-target"}
