"""Read-only projection of the shared in-process scheduler."""

from collections.abc import Callable
from dataclasses import asdict

from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobScheduler
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.schema.schedule import ScheduleStatusRead, ScheduleTaskRead
from backend.schema.list_query import iter_filter_fields
from backend.schema.response import ListBody
from backend.service.runtime_config_service import resolve_scan_enabled


class ScheduleStatusService:
    def __init__(
        self,
        scheduler: JobScheduler,
        sessions: Callable[[], Session],
        *,
        synthetic_acceptance_enabled: bool,
        real_analysis_enabled: bool = False,
        runtime_config=None,
    ):
        self._scheduler = scheduler
        self._sessions = sessions
        self._synthetic_acceptance_enabled = synthetic_acceptance_enabled
        self._real_analysis_enabled = real_analysis_enabled
        self._runtime_config = runtime_config

    def get(self) -> ScheduleStatusRead:
        snapshot = self._scheduler.snapshot()
        if self._runtime_config is not None:
            scan_enabled = self._runtime_config.scan_enabled()
        else:
            # Services used without an application lifespan still resolve the
            # same typed deployment/persisted/default precedence.
            try:
                with self._sessions() as db:
                    scan_enabled = resolve_scan_enabled(SettingMapper(db).scan_enabled())
            except (ValueError, TypeError):
                scan_enabled = False
        if not scan_enabled:
            guard = "DISABLED"
        elif self._real_analysis_enabled:
            guard = "REAL_READY"
        elif not self._synthetic_acceptance_enabled:
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
            diagnostics_health=snapshot.diagnostics_health,
            diagnostics_persistent=snapshot.diagnostics_persistent,
            worker_heartbeat_at=snapshot.worker_heartbeat_at,
            tasks=[ScheduleTaskRead(**item) for item in self._with_names([asdict(item) for item in snapshot.tasks])],
        )

    def tasks(self, request) -> ListBody:
        values = [asdict(item) for item in self._scheduler.snapshot().tasks]
        reverse = bool(request.sorter and request.sorter[0].direction == "desc")
        page = self._page(sorted(values, key=lambda item: item["task_key"], reverse=reverse), request)
        page.items = self._with_names(page.items)
        return page

    def _with_names(self, values):
        for item in values:
            progress = self._scheduler.progress_snapshot(item["task_key"])
            if progress is not None:
                item["generic_progress"] = progress
        ids = {int(item["task_key"].split(":")[1]) for item in values if (
            item["task_key"].startswith("tag-scan:") and item["task_key"].split(":")[1].isdigit()
            and len(item["task_key"].split(":")[1]) <= 19
            and 0 < int(item["task_key"].split(":")[1]) <= 2**63 - 1
        )}
        if not ids:
            return values
        with self._sessions() as db:
            names = AutoTagRuleMapper(db).display_names(sorted(ids))
        for item in values:
            if item["task_key"].startswith("tag-scan:"):
                suffix = item["task_key"].split(":")[1]
                item["display_name"] = names.get(int(suffix)) if suffix.isdigit() else None
        return values

    def events(self, request) -> ListBody:
        return self._page(self._scheduler.diagnostics.events(), request)

    @staticmethod
    def _page(values, request) -> ListBody:
        for expression in iter_filter_fields(request.filter):
            values = [item for item in values if item.get(expression.key) == expression.val]
        offset = (request.page_index - 1) * request.page_size
        return ListBody(
            items=values[offset:offset + request.page_size], total=len(values),
            page_index=request.page_index, page_size=request.page_size,
        )
