"""PIRC-9 API application backed by exactly the 10 target tables."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.core import target_database
from backend.core.config import (
    APP_DISPLAY_NAME,
    IMPORT_PREVIEW_SWEEP_INTERVAL_SECONDS,
    RESOURCE_DIR,
)
from backend.router.error import register_error_handlers
from backend.router.import_conflict import router as import_conflict_router
from backend.router.import_file import router as import_file_router
from backend.router.import_router import router as import_router
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_review import router as ledger_review_router
from backend.router.ledger_review_candidate import router as ledger_review_candidate_router
from backend.router.ledger_transaction_fact import router as ledger_transaction_fact_router
from backend.router.system import router as system_router
from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.service.target_economic_service import TargetEconomicService
from backend.service.target_intake_service import TargetIntakeService


logger = logging.getLogger(__name__)


async def _sweep_timed_out_import_previews() -> None:
    while True:
        await asyncio.sleep(IMPORT_PREVIEW_SWEEP_INTERVAL_SECONDS)
        try:
            with target_database.SessionLocal() as db:
                TargetIntakeService(db).fail_expired_pending_files()
        except Exception:
            logger.exception("Failed to sweep timed-out import previews")


@asynccontextmanager
async def lifespan(_: FastAPI):
    target_database.init_target_db()
    with target_database.SessionLocal() as db:
        TargetIntakeService(db).fail_orphaned_pending_files()
        TargetEconomicService(db).backfill_defaults()
    sweep_task = asyncio.create_task(_sweep_timed_out_import_previews())
    try:
        yield
    finally:
        sweep_task.cancel()
        with suppress(asyncio.CancelledError):
            await sweep_task


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
app.include_router(import_file_router)
app.include_router(ledger_router)
app.include_router(ledger_review_router)
app.include_router(ledger_review_candidate_router)
app.include_router(ledger_transaction_fact_router)
app.include_router(ledger_account_router)
app.include_router(tag_router)
app.include_router(tag_assignment_router)
app.include_router(system_router)
