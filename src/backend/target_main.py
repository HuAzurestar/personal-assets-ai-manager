"""PAAM API application backed by the target SQLite schema."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.core import target_database
from backend.core.config import (
    APP_DISPLAY_NAME,
    AUTOTAG_REAL_ANALYSIS,
    AUTOTAG_SYNTHETIC_ACCEPTANCE,
    IMPORT_PREVIEW_SWEEP_INTERVAL_SECONDS,
    RESOURCE_DIR,
    SQL_WEB_ENABLED,
)
from backend.core.job_scheduler import JobRunContext, job_scheduler
from backend.router.auto_tag_rule import router as auto_tag_rule_router
from backend.router.error import register_error_handlers
from backend.router.import_conflict import router as import_conflict_router
from backend.router.import_file import router as import_file_router
from backend.router.import_router import router as import_router
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_review import router as ledger_review_router
from backend.router.ledger_review_candidate import (
    router as ledger_review_candidate_router,
)
from backend.router.ledger_transaction_fact import (
    router as ledger_transaction_fact_router,
)
from backend.router.system import router as system_router
from backend.router.system_setting import router as system_setting_router
from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.router.tag_assignment_request import (
    router as tag_assignment_request_router,
)
from backend.service.auto_tag_schedule_service import AutoTagScheduleService
from backend.service.configured_llm_analyzer import provider_secret_reader
from backend.service.target_intake_service import TargetIntakeService

if SQL_WEB_ENABLED:
    from a2wsgi import WSGIMiddleware
    from backend.core.sql_web import initialize_sql_web, sql_web_app


async def _sweep_timed_out_import_previews(_: JobRunContext) -> None:
    with target_database.SessionLocal() as db:
        TargetIntakeService(db).fail_expired_pending_files()


@asynccontextmanager
async def lifespan(application: FastAPI):
    target_database.init_target_db()
    if SQL_WEB_ENABLED:
        initialize_sql_web()
    with target_database.SessionLocal() as db:
        TargetIntakeService(db).fail_orphaned_pending_files()
    job_scheduler.register_interval(
        "system:import-preview-timeout",
        seconds=IMPORT_PREVIEW_SWEEP_INTERVAL_SECONDS,
        callback=_sweep_timed_out_import_previews,
    )
    auto_tag_schedule = AutoTagScheduleService(
        target_database.SessionLocal,
        job_scheduler,
        provider_secret_reader,
        synthetic_acceptance_enabled=AUTOTAG_SYNTHETIC_ACCEPTANCE,
        real_analysis_enabled=AUTOTAG_REAL_ANALYSIS,
    )
    auto_tag_schedule.register_persisted()
    application.state.auto_tag_schedule = auto_tag_schedule
    await job_scheduler.start()
    try:
        yield
    finally:
        await job_scheduler.shutdown()
        if hasattr(application.state, "auto_tag_schedule"):
            del application.state.auto_tag_schedule


app = FastAPI(
    title=f"{APP_DISPLAY_NAME} PIRC-9 API",
    version="1.0.0",
    lifespan=lifespan,
)
register_error_handlers(app)
app.mount("/static", StaticFiles(directory=RESOURCE_DIR / "frontend"), name="static")
app.mount("/asset", StaticFiles(directory=RESOURCE_DIR / "asset"), name="asset")
if SQL_WEB_ENABLED:
    app.mount("/sql", WSGIMiddleware(sql_web_app), name="sql")
app.include_router(import_router)
app.include_router(auto_tag_rule_router)
app.include_router(import_conflict_router)
app.include_router(import_file_router)
app.include_router(ledger_router)
app.include_router(ledger_review_router)
app.include_router(ledger_review_candidate_router)
app.include_router(ledger_transaction_fact_router)
app.include_router(ledger_account_router)
app.include_router(tag_router)
app.include_router(tag_assignment_router)
app.include_router(tag_assignment_request_router)
app.include_router(system_router)
app.include_router(system_setting_router)
