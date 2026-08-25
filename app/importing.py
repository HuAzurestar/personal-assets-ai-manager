from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ImportedRow:
    occurred_at: datetime
    merchant: str
    note: str
    amount: float
    reference: str
    raw_payload: str


HEADER_ALIASES = {
    "occurred_at": ("交易时间", "交易创建时间", "交易日期"),
    "merchant": ("交易对方", "交易对方名称", "收/付款方", "对方"),
    "note": ("商品", "商品说明", "备注", "商品名称"),
    "amount": ("金额(元)", "金额（元）", "金额"),
    "direction": ("收/支", "收支类型", "交易类型"),
    "reference": ("交易单号", "交易订单号", "支付宝交易号", "商家订单号"),
}


def parse_csv(content: bytes, source_type: str) -> list[ImportedRow]:
    if source_type not in {"alipay", "wechat"}:
        raise ValueError("Only alipay and wechat adapters are enabled in the MVP")
    rows = list(csv.reader(io.StringIO(_decode(content))))
    header_index = next((i for i, row in enumerate(rows) if _find(row, HEADER_ALIASES["amount"]) and _find(row, HEADER_ALIASES["occurred_at"])), None)
    if header_index is None:
        raise ValueError("Could not find transaction-time and amount headers")
    headers = rows[header_index]
    return [_normalise(dict(zip(headers, row))) for row in rows[header_index + 1 :] if any(cell.strip() for cell in row)]


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("CSV must be UTF-8 or GB18030 encoded")


def _find(row: list[str], aliases: tuple[str, ...]) -> str | None:
    return next((cell for cell in row if cell.strip() in aliases), None)


def _value(row: dict[str, str], field: str) -> str:
    return next((row.get(name, "").strip() for name in HEADER_ALIASES[field] if row.get(name, "").strip()), "")


def _normalise(row: dict[str, str]) -> ImportedRow:
    timestamp = _value(row, "occurred_at")
    occurred_at = next((datetime.strptime(timestamp, pattern) for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S") if _valid_time(timestamp, pattern)), None)
    if not occurred_at:
        raise ValueError(f"Unrecognised transaction time: {timestamp}")
    raw_amount = _value(row, "amount")
    amount = float(re.sub(r"[^0-9.-]", "", raw_amount) or "0")
    direction = _value(row, "direction")
    if direction in {"支出", "付款", "支"} and amount > 0:
        amount = -amount
    elif direction in {"收入", "收款", "收"} and amount < 0:
        amount = -amount
    return ImportedRow(occurred_at, _value(row, "merchant") or "未知交易方", _value(row, "note"), amount, _value(row, "reference"), str(row))


def _valid_time(value: str, pattern: str) -> bool:
    try:
        datetime.strptime(value, pattern)
        return True
    except ValueError:
        return False
