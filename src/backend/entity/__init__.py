"""Storage entities; importing this package registers the target tables."""

from backend.entity.base import TargetTable
from backend.entity.import_file import ImportFile
from backend.entity.bill_raw import BillRaw
from backend.entity.bill_fact import BillFact
from backend.entity.review_case import ReviewCase
from backend.entity.review_case_bill import ReviewCaseBill
from backend.entity.review_history import ReviewHistory
from backend.entity.ledger_entry import LedgerEntry
from backend.entity.tag_view import TargetTagView
from backend.entity.tag import TargetTag
from backend.entity.ledger_entry_tag import LedgerEntryTag

# Existing domain aliases retain the same mapped classes.
EconomicFlow = LedgerEntry
FlowAllocation = ReviewCaseBill
