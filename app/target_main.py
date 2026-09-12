"""PIRC-9 target-only API application.

This runtime seam deliberately initializes only the 11 target tables.  It lets
the replacement API be verified without accidentally reading or writing a
compatibility table while the legacy UI is still being retired.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.controllers.intake import target_router as target_intake_router
from app.api.controllers.target_ledger import v1_router as target_ledger_router
from app.api.controllers.target_review import router as target_review_router
from app.api.controllers.target_tag import router as target_tag_router
from app.config import APP_DISPLAY_NAME
from app.database import init_target_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_target_db()
    yield


app = FastAPI(
    title=f"{APP_DISPLAY_NAME} PIRC-9 API",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(target_intake_router)
app.include_router(target_ledger_router)
app.include_router(target_review_router)
app.include_router(target_tag_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "schema": "pirc-9-target"}
