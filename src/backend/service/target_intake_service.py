"""The existing import entry now uses explicit bounded batches in place."""
from backend.service.import_batch_service import ImportBatchService


class TargetIntakeService(ImportBatchService):
    """Keep the application entry name, not the retired version/replay contract."""
