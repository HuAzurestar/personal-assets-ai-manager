"""PIRC-9 API application backed by exactly the 11 ledger tables."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.router.target_intake import router as target_intake_router
from backend.router.target_economic import router as target_economic_router
from backend.router.target_review import router as target_review_router, v2_router as economic_review_router
from backend.router.target_tag import router as target_tag_router
from backend.core import target_database
from backend.core.config import APP_DISPLAY_NAME, RESOURCE_DIR
from backend.service.target_economic_service import TargetEconomicService


templates = Jinja2Templates(directory=RESOURCE_DIR / "frontend")


@asynccontextmanager
async def lifespan(_: FastAPI):
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        TargetEconomicService(db).backfill_defaults()
    yield


app = FastAPI(
    title=f"{APP_DISPLAY_NAME} PIRC-9 API",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=RESOURCE_DIR / "frontend"), name="static")
app.mount("/asset", StaticFiles(directory=RESOURCE_DIR / "asset"), name="asset")
app.include_router(target_intake_router)
app.include_router(target_economic_router)
app.include_router(target_review_router)
app.include_router(economic_review_router)
app.include_router(target_tag_router)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="target.html",
        context={"app_name": APP_DISPLAY_NAME},
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "schema": "pirc-9-target"}
