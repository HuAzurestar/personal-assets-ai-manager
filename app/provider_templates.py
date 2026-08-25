from __future__ import annotations

import unicodedata


PROVIDER_LABELS = {"alipay": "支付宝", "wechat": "微信"}
REQUIRED_FIELDS = ("occurred_at", "merchant", "amount")

# These are deliberately provider-owned templates, not a user-configurable mapping.
# The aliases cover known desktop exports and retain a deterministic precedence order.
PROVIDER_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "alipay": {
        "occurred_at": ("交易创建时间", "付款时间", "交易时间"),
        "merchant": ("交易对方", "交易对方名称", "对方账户"),
        "amount": ("金额（元）", "金额(元)", "金额"),
        "note": ("商品名称", "商品说明", "备注", "商品"),
        "direction": ("收/支", "收支"),
        "reference": ("交易号", "支付宝交易号", "商家订单号"),
        "account": ("收/支方式", "支付方式", "付款方式", "资金渠道", "收款方式"),
    },
    "wechat": {
        "occurred_at": ("交易时间",),
        "merchant": ("交易对手", "交易对方", "收/付款方"),
        "amount": ("金额(元)", "金额（元）", "金额"),
        "note": ("商品", "备注", "商品名称"),
        "direction": ("收/支", "收支"),
        "reference": ("交易单号", "交易订单号", "商户单号"),
        "account": ("支付方式", "收/支方式", "付款方式", "资金渠道", "收款方式"),
    },
}


def normalise_header(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def locate_provider_table(source_type: str, table: list[list[str]]) -> tuple[list[str], list[dict[str, str]], dict[str, str | None]]:
    template = PROVIDER_TEMPLATES[source_type]
    for header_index, header_row in enumerate(table[:40]):
        headers = [value.strip() for value in header_row]
        lookup = {normalise_header(header): header for header in headers if header}
        mapping = {
            field: next((lookup[normalise_header(alias)] for alias in aliases if normalise_header(alias) in lookup), None)
            for field, aliases in template.items()
        }
        if all(mapping[field] for field in REQUIRED_FIELDS):
            rows = []
            for row in table[header_index + 1 :]:
                values = dict(zip(headers, row))
                if not any(value.strip() for value in values.values()):
                    continue
                if not values.get(mapping["occurred_at"] or "", "").strip():
                    continue
                rows.append(values)
            if not rows:
                raise ValueError(f"{PROVIDER_LABELS[source_type]}账单模板已识别，但没有交易数据")
            return headers, rows, mapping
    raise ValueError(f"未识别为{PROVIDER_LABELS[source_type]}账单模板：缺少交易时间、交易方或金额字段")
