"""Shared platform capabilities; services keep their transaction ownership."""
from dataclasses import dataclass

from backend.core.job_scheduler import JobScheduler
from backend.service.setting_service import SettingService


@dataclass(frozen=True)
class PlatformMiddleware:
    sessions: object
    credentials: object
    scheduler: JobScheduler

    def setting(self, db, *, secret_store=None, on_scan_setting_changed=None):
        return SettingService(db, secret_store or self.credentials, on_scan_setting_changed)
