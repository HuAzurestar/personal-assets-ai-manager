from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import ListRequest, iter_filter_fields, validate_list_capabilities
from backend.schema.response import ListResponse, SuccessResponse


class ScheduleProgressRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str = "SCAN"
    page_total: int = 0
    inspected_count: int = 0
    submitted_count: int = 0
    request_count: int = 0
    failed_count: int = 0
    no_call_count: int = 0
    insufficient_count: int = 0
    skipped_count: int = 0
    attempt: int = 0
    rule_revision: int = 0


class ScheduleEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    run_id: str
    task_key: str
    phase: str
    code: str
    severity: Literal["INFO", "WARNING", "ERROR"]
    safe_message: str
    rule_revision: int | None = None
    ledger_id: int | None = None
    attempt: int | None = None
    detail_code: str | None = None


class ScheduleTaskRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_key: str
    queue_state: Literal["IDLE", "QUEUED", "RUNNING", "PAUSED", "BLOCKED"]
    display_name: str | None = None
    queue_position: int | None
    enqueued_at: datetime | None
    started_at: datetime | None
    next_run_at: datetime | None
    last_result: Literal["COMPLETED", "PARTIAL_FAILURE", "FAILED", "CANCELLED"] | None
    last_error_code: str | None
    run_id: str | None = None
    last_run_id: str | None = None
    elapsed_ms: int | None = None
    wait_ms: int | None = None
    progress: ScheduleProgressRead = Field(default_factory=ScheduleProgressRead)
    last_progress: ScheduleProgressRead | None = None
    last_outcome_code: str | None = None
    last_failure: ScheduleEventRead | None = None
    blocked_attempts: int = 0


class ScheduleStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduler_state: Literal["RUNNING", "STOPPED"]
    worker_state: Literal["HEALTHY", "STOPPED", "UNHEALTHY"]
    accepting: bool
    captured_at: datetime
    tag_scan_guard: Literal[
        "DISABLED", "SYNTHETIC_READY", "NON_SYNTHETIC_FACT", "REAL_READY",
    ]
    tasks: list[ScheduleTaskRead]
    diagnostics_health: Literal["HEALTHY", "DEGRADED"] = "HEALTHY"
    diagnostics_persistent: bool = False
    worker_heartbeat_at: datetime | None = None
    history_complete: Literal[False] = False


class ScheduleStatusResponse(SuccessResponse[ScheduleStatusRead]):
    body: ScheduleStatusRead


class ScheduleTaskListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        _validate_request(self, {"task_key", "queue_state"}, {"task_key"})
        return self


class ScheduleEventListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        _validate_request(self, {"task_key", "code", "severity"}, {"time"})
        if any(sorter.direction != "desc" for sorter in self.sorter):
            raise ListQueryError("Diagnostic events require descending time order")
        return self


class ScheduleTaskListResponse(ListResponse[ScheduleTaskRead]):
    pass


class ScheduleEventListResponse(ListResponse[ScheduleEventRead]):
    pass


def _validate_request(request, filters, sorters):
    validate_list_capabilities(
        request, query_fields=(), filter_operators={key: ("=",) for key in filters},
        sorter_fields=sorters, logical_operators=("AND",), max_sorters=1,
    )
    for item in iter_filter_fields(request.filter):
        if not isinstance(item.val, str) or not 1 <= len(item.val) <= 96:
            raise ListQueryError("Invalid diagnostic filter value", code="LIST_FILTER_VALUE_INVALID")
        allowed = {
            "severity": {"INFO", "WARNING", "ERROR"},
            "queue_state": {"IDLE", "QUEUED", "RUNNING", "PAUSED", "BLOCKED"},
        }.get(item.key)
        if allowed is not None and item.val not in allowed:
            raise ListQueryError("Invalid diagnostic filter value", code="LIST_FILTER_VALUE_INVALID")
