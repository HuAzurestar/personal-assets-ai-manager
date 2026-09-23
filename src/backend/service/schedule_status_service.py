"""Read-only projection of the shared in-process scheduler."""

from backend.core.job_scheduler import JobScheduler
from backend.schema.schedule import ScheduleStatusRead, ScheduleTaskRead


class ScheduleStatusService:
    def __init__(self, scheduler: JobScheduler):
        self._scheduler = scheduler

    def get(self) -> ScheduleStatusRead:
        snapshot = self._scheduler.snapshot()
        return ScheduleStatusRead(
            scheduler_state=snapshot.scheduler_state,
            worker_state=snapshot.worker_state,
            accepting=snapshot.accepting,
            captured_at=snapshot.captured_at,
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
