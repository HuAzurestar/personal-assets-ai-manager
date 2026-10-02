"""Canonical source identities; not a receipt or a fuzzy cash matcher."""
from datetime import datetime, timezone
import hashlib
import json
from backend.core.money import MAX_ABS_AMOUNT, normalize_currency_code

SOURCE_CODES = {"unknown": 0, "manual": 1, "alipay": 101, "wechat": 102, "ccb": 201, "abc": 202, "cmb": 203}
FORMAT_CODES = {"unknown": 0, "csv": 1, "xls": 2, "xlsx": 3, "pdf": 4}


def canonical_json(value):
    def timestamp(item):
        if isinstance(item, datetime) and item.tzinfo is not None and item.utcoffset() is not None:
            return item.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        raise TypeError("unsupported canonical value type")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=timestamp)


def source_account_code(row):
    # Preserve parser-confirmed source strings, including leading zeroes.
    # Payment method, selected Ledger ref and counterparty are never this key.
    return str(row.get("account", {}).get("number", "") if row.get("source_type") in {"abc", "ccb", "cmb"}
               else row.get("profile", ""))


def source_identity(row, file_sha256):
    reference = row.get("reference", "")
    if reference:
        return dict(namespace="source-reference-v1", source_type=SOURCE_CODES[row["source_type"]],
                    account_code=source_account_code(row), source_reference=reference)
    return dict(namespace="source-row-v1", file_sha256=file_sha256, source_row_number=row["row_number"])


def fact_key(row, file_sha256):
    return hashlib.sha256(canonical_json(source_identity(row, file_sha256)).encode("utf-8")).hexdigest()


def fact_values(row, file_sha256):
    if row.get("error") or row.get("disposition") != "posted":
        raise ValueError("ROW_INVALID")
    signed = row.get("amount_minor")
    if type(signed) is not int or signed == 0 or abs(signed) > MAX_ABS_AMOUNT:
        raise ValueError("ROW_INVALID")
    occurred = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
    if occurred.tzinfo is None or occurred.utcoffset() is None:
        raise ValueError("INVALID_TIME")
    return dict(fact_key=fact_key(row, file_sha256), occurred_time=occurred.astimezone(timezone.utc),
                cash_direction=1 if signed > 0 else 2, amount=abs(signed),
                currency_code=normalize_currency_code(row["currency"]), account_code=source_account_code(row),
                counterparty_name=row.get("merchant", ""), counterparty_account_ref="", summary=row.get("note", ""))


def same_fact(parsed, accepted):
    return all(parsed[key] == accepted[key] for key in
               ("occurred_time", "cash_direction", "amount", "currency_code", "account_code"))


def raw_evidence(row, document):
    fields = ("occurred_at", "time_precision", "amount_minor", "balance_minor", "merchant", "note", "reference",
              "currency", "source_account", "account", "payment_method", "profile", "source_type", "status", "nature", "disposition")
    normalized = {key: row[key] for key in fields if key in row}
    payload = canonical_json(dict(normalized=normalized, raw=row.get("raw", {}),
                                  parser_version=document.get("parser_version", 1),
                                  source_timezone=document.get("source_timezone", "")))
    if len(payload.encode("utf-8")) > 1024 * 1024:
        raise ValueError("DETAIL_LIMIT")
    # Raw hash is the immutable source content only, never decisions/receipts.
    raw_hash = hashlib.sha256(canonical_json(row.get("raw", {})).encode("utf-8")).hexdigest()
    return raw_hash, payload
