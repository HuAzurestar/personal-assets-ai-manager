"""Storage entities; importing this package registers the target tables."""

from backend.entity.base import TargetTable
from backend.entity.transaction_import_file import (
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_FORMAT_PDF,
    IMPORT_FILE_FORMAT_UNKNOWN,
    IMPORT_FILE_FORMAT_XLS,
    IMPORT_FILE_FORMAT_XLSX,
    IMPORT_FILE_STATUS_FAILED,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PARTIAL,
    IMPORT_FILE_STATUS_PENDING,
    IMPORT_SOURCE_ABC_BANK,
    IMPORT_SOURCE_ALIPAY,
    IMPORT_SOURCE_CCB_BANK,
    IMPORT_SOURCE_CMB_BANK,
    IMPORT_SOURCE_MANUAL,
    IMPORT_SOURCE_UNKNOWN,
    IMPORT_SOURCE_WECHAT,
    TransactionImportFile,
)
from backend.entity.transaction_import_row import (
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
    IMPORT_ROW_STATUS_UNKNOWN,
    TransactionImportRow,
)
from backend.entity.transaction_fact import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    TransactionFact,
)
from backend.entity.review_case import ReviewCase
from backend.entity.review_allocation import ReviewAllocation
from backend.entity.review_revision import ReviewRevision
from backend.entity.ledger_entry import LedgerEntry
from backend.entity.tag_view import TargetTagView
from backend.entity.tag import TargetTag
from backend.entity.ledger_entry_tag import LedgerEntryTag
from backend.entity.setting import Setting
from backend.entity.auto_tag_rule import (
    AMOUNT_MODE_BAND,
    AMOUNT_MODE_EXACT,
    AMOUNT_MODE_NONE,
    AUTO_TAG_METHOD_LLM_DIRECT,
    MAX_COUNTER_VALUE,
    AutoTagRule,
)
from backend.entity.tag_assignment_request import (
    TAG_REQUEST_STATUS_CANCELLED,
    TAG_REQUEST_STATUS_ENABLED,
    TAG_REQUEST_STATUS_PENDING,
    TAG_REQUEST_STATUS_REJECTED,
    TAG_REQUEST_STATUS_REPLACED,
    TagAssignmentRequest,
)
