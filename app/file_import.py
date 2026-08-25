from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import openpyxl
import pyzipper
import xlrd

from app.importing import HEADER_ALIASES, ImportedRow, _normalise
from app.provider_templates import locate_provider_table

SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx", ".zip"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_ENTRY_BYTES = 25 * 1024 * 1024
MAX_ROWS = 10_000
REQUIRED_MAPPING_FIELDS = ("occurred_at", "merchant", "amount")


@dataclass(frozen=True)
class ParsedFile:
    source_type: str
    filename: str
    file_format: str
    archive_entry: str | None
    columns: list[str]
    rows: list[dict[str, str]]
    mapping: dict[str, str | None]
    file_sha256: str


def parse_upload(source_type: str, content: bytes, filename: str, password: str | None = None) -> ParsedFile:
    if not content:
        raise ValueError("Import file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Import file exceeds the 25 MB safety limit")
    safe_filename = Path(filename or "import").name
    extension = Path(safe_filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Supported formats are CSV, XLS, XLSX, and ZIP")
    archive_entry = None
    payload = content
    if extension == ".zip":
        archive_entry, payload = _read_zip(content, password)
        extension = Path(archive_entry).suffix.lower()
    columns, rows, mapping = _read_tabular(payload, extension, source_type)
    return ParsedFile(
        source_type=source_type,
        filename=safe_filename,
        file_format=extension.removeprefix("."),
        archive_entry=archive_entry,
        columns=columns,
        rows=rows,
        mapping=mapping,
        file_sha256=hashlib.sha256(content).hexdigest(),
    )


def normalise_rows(parsed: ParsedFile) -> list[ImportedRow]:
    mapping = parsed.mapping
    missing = [field for field in REQUIRED_MAPPING_FIELDS if not mapping.get(field)]
    if missing:
        raise ValueError("Transaction time, counterparty, and amount mappings are required")
    unknown_columns = [column for column in mapping.values() if column and column not in parsed.columns]
    if unknown_columns:
        raise ValueError("A selected mapping column is not present in the uploaded file")
    imported: list[ImportedRow] = []
    for index, row in enumerate(parsed.rows, start=2):
        alias_row = {
            HEADER_ALIASES[field][0]: row.get(column, "")
            for field, column in mapping.items()
            if column
        }
        try:
            imported.append(_normalise(alias_row))
        except ValueError as error:
            raise ValueError(f"Could not parse row {index}: {error}") from error
    if not imported:
        raise ValueError("No transaction rows were found")
    return imported


def preview_rows(parsed: ParsedFile, limit: int = 8) -> list[dict[str, str]]:
    return [
        {
            "交易时间": row.get(parsed.mapping["occurred_at"] or "", ""),
            "交易方": row.get(parsed.mapping["merchant"] or "", ""),
            "金额": row.get(parsed.mapping["amount"] or "", ""),
            "备注": row.get(parsed.mapping["note"] or "", ""),
            "收支": row.get(parsed.mapping["direction"] or "", ""),
            "流水号": row.get(parsed.mapping["reference"] or "", ""),
        }
        for row in parsed.rows[:limit]
    ]


def _read_zip(content: bytes, password: str | None) -> tuple[str, bytes]:
    try:
        with pyzipper.AESZipFile(io.BytesIO(content)) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) != 1:
                raise ValueError("ZIP must contain exactly one CSV, XLS, or XLSX file")
            entry = entries[0]
            entry_path = PurePosixPath(entry.filename)
            if entry_path.is_absolute() or ".." in entry_path.parts or entry_path.suffix.lower() not in SUPPORTED_EXTENSIONS - {".zip"}:
                raise ValueError("ZIP contains an unsupported file entry")
            if entry.file_size > MAX_ARCHIVE_ENTRY_BYTES or entry.file_size > MAX_UPLOAD_BYTES * 20:
                raise ValueError("ZIP entry exceeds the safety limit")
            try:
                payload = archive.read(entry, pwd=password.encode() if password else None)
            except (RuntimeError, NotImplementedError) as error:
                raise ValueError("ZIP password is required or invalid") from error
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Invalid ZIP archive") from error
    return entry.filename, payload


def _read_tabular(payload: bytes, extension: str, source_type: str) -> tuple[list[str], list[dict[str, str]], dict[str, str | None]]:
    if extension == ".csv":
        table = list(csv.reader(io.StringIO(_decode_csv(payload))))
    elif extension == ".xlsx":
        workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        try:
            table = [[_stringify(value) for value in row] for row in workbook.active.iter_rows(values_only=True)]
        finally:
            workbook.close()
    elif extension == ".xls":
        workbook = xlrd.open_workbook(file_contents=payload, on_demand=True)
        try:
            sheet = workbook.sheet_by_index(0)
            table = [[_stringify(sheet.cell_value(row, column)) for column in range(sheet.ncols)] for row in range(sheet.nrows)]
        finally:
            workbook.release_resources()
    else:
        raise ValueError("Unsupported tabular file format")
    if not table:
        raise ValueError("Import file has no rows")
    headers, rows, mapping = locate_provider_table(source_type, table)
    if len(rows) > MAX_ROWS:
        raise ValueError("Import file exceeds the 10,000-row safety limit")
    return headers, rows, mapping


def _decode_csv(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV must be UTF-8 or GB18030 encoded")


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()
