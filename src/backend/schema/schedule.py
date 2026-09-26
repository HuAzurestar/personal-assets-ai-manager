from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schema.response import SuccessResponse


class ScheduleTaskRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_key: str
    queue_state: Literal["IDLE", "QUEUED", "RUNNING", "PAUSED"]
    queue_position: int | None
    enqueued_at: datetime | None
    started_at: datetime | None
    next_run_at: datetime | None
    last_result: Literal["COMPLETED", "PARTIAL_FAILURE", "FAILED", "CANCELLED"] | None
    last_error_code: str | None


class ScheduleStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduler_state: Literal["RUNNING", "STOPPED"]
    worker_state: Literal["HEALTHY", "STOPPED"]
    accepting: bool
    captured_at: datetime
    tag_scan_guard: Literal[
        "DISABLED", "SYNTHETIC_READY", "NON_SYNTHETIC_FACT", "REAL_READY",
    ]
    tasks: list[ScheduleTaskRead]


class ScheduleStatusResponse(SuccessResponse[ScheduleStatusRead]):
    body: ScheduleStatusRead
