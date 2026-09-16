"""PIRC-9 API application backed by exactly the 11 ledger tables."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.core import target_database
from backend.core.config import APP_DISPLAY_NAME, RESOURCE_DIR
from backend.router.error import register_error_handlers
from backend.router.import_conflict import router as import_conflict_router
from backend.router.import_router import router as import_router
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_fact import router as ledger_fact_router
from backend.router.ledger_fact_legacy import router as ledger_fact_legacy_router
from backend.router.ledger_legacy import router as ledger_legacy_router
from backend.router.ledger_review import router as ledger_review_router
from backend.router.ledger_review_legacy import router as ledger_review_legacy_router
from backend.router.system import router as system_router
from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.service.target_economic_service import TargetEconomicService


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
register_error_handlers(app)
app.mount("/static", StaticFiles(directory=RESOURCE_DIR / "frontend"), name="static")
app.mount("/asset", StaticFiles(directory=RESOURCE_DIR / "asset"), name="asset")
app.include_router(import_router)
app.include_router(import_conflict_router)
app.include_router(ledger_legacy_router)
app.include_router(ledger_router)
app.include_router(ledger_review_legacy_router)
app.include_router(ledger_review_router)
app.include_router(ledger_fact_router)
app.include_router(ledger_fact_legacy_router)
app.include_router(ledger_account_router)
app.include_router(tag_router)
app.include_router(tag_assignment_router)
app.include_router(system_router)
