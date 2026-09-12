"""PIRC-9 target-only API application.

This runtime seam deliberately initializes only the 11 target tables.  It lets
the replacement API be verified without accidentally reading or writing a
compatibility table while the legacy UI is still being retired.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.controllers.target_intake import router as target_intake_router
from app.api.controllers.target_ledger import v1_router as target_ledger_router
from app.api.controllers.target_review import router as target_review_router
from app.api.controllers.target_tag import router as target_tag_router
from app.config import APP_DISPLAY_NAME
from app.database import init_target_db


APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_target_db()
    yield


app = FastAPI(
    title=f"{APP_DISPLAY_NAME} PIRC-9 API",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(target_intake_router)
app.include_router(target_ledger_router)
app.include_router(target_review_router)
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
