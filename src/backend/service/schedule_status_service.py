"""Read-only projection of the shared in-process scheduler."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobScheduler
from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper
from backend.schema.schedule import ScheduleStatusRead, ScheduleTaskRead


class ScheduleStatusService:
    def __init__(
        self,
        scheduler: JobScheduler,
        sessions: Callable[[], Session],
        *,
        synthetic_acceptance_enabled: bool,
    ):
        self._scheduler = scheduler
        self._sessions = sessions
        self._synthetic_acceptance_enabled = synthetic_acceptance_enabled

    def get(self) -> ScheduleStatusRead:
        snapshot = self._scheduler.snapshot()
        if not self._synthetic_acceptance_enabled:
            guard = "DISABLED"
        else:
            with self._sessions() as db:
                guard = (
                    "SYNTHETIC_READY"
                    if AutoTagScanMapper(db).is_synthetic_acceptance_database()
                    else "NON_SYNTHETIC_FACT"
                )
        return ScheduleStatusRead(
            scheduler_state=snapshot.scheduler_state,
            worker_state=snapshot.worker_state,
            accepting=snapshot.accepting,
            captured_at=snapshot.captured_at,
            tag_scan_guard=guard,
            tasks=[
                ScheduleTaskRead(
                    task_key=item.task_key,
                    queue_state=item.queue_state,
                    queue_position=item.queue_position,
                    enqueued_at=item.enqueued_at,
                    started_at=item.started_at,
                    next_run_at=item.next_run_at,
                    last_result=item.last_result,
                    last_error_code=item.last_error_code,
                )
                for item in snapshot.tasks
            ],
        )
