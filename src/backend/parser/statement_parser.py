"""Local, deterministic statement readers. Raw evidence is never discarded."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone, tzinfo
from pathlib import Path

import openpyxl
import pdfplumber
import xlrd

from backend.parser.file_import import (
    MAX_UPLOAD_BYTES,
    MAX_ROWS,
    _decode_csv,
    _read_zip,
    _stringify,
)
from backend.core.money import amount_from_decimal
from backend.parser.provider_template import PROVIDER_TEMPLATES, normalise_header

LABELS = {
    "alipay": "支付宝",
    "wechat": "微信",
    "ccb": "建设银行",
    "abc": "农业银行",
    "cmb": "招商银行",
}
BANKS = {"ccb", "abc", "cmb"}
EMPTY = {"", "/", "--", "-", "无"}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def amount_minor(value: str) -> int:
    value = str(value).strip().replace(",", "").lstrip("¥￥")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?", value):
        raise ValueError("金额格式不明确")
    return amount_from_decimal(value, "CNY")


def _statement_utc_time(local_time: datetime, source_timezone: tzinfo) -> datetime:
    candidates = set()
    for fold in (0, 1):
        candidate = local_time.replace(
            tzinfo=source_timezone,
            fold=fold,
        ).astimezone(timezone.utc)
        if candidate.astimezone(source_timezone).replace(tzinfo=None) == local_time:
            candidates.add(candidate)
    if not candidates:
        raise ValueError("交易时间处于时区切换的不存在区间")
    if len(candidates) > 1:
        raise ValueError("交易时间处于夏令时重复区间，账单缺少明确偏移")
    return candidates.pop()


def timestamp(value: str, clock: str, source_timezone: tzinfo) -> tuple[str, str]:
    value = value.strip()
    if re.fullmatch(r"\d{8}\.0", value):
        value = value[:-2]
    if re.fullmatch(r"\d{1,6}\.0", clock):
        clock = clock[:-2]
    if re.fullmatch(r"\d{8}", value) and clock.strip() not in EMPTY:
        value += clock.strip().zfill(6)
    patterns = [
        ("%Y%m%d%H%M%S", "second"),
        ("%Y%m%d", "day"),
        ("%Y-%m-%d", "day"),
        ("%Y/%m/%d", "day"),
        ("%Y-%m-%d %H:%M:%S", "second"),
        ("%Y-%m-%d %H:%M", "minute"),
        ("%Y/%m/%d %H:%M:%S", "second"),
    ]
    for pattern, precision in patterns:
        try:
            local_time = datetime.strptime(value, pattern)
        except ValueError:
            continue
        utc_time = _statement_utc_time(local_time, source_timezone)
        return (
            utc_time.isoformat(timespec="seconds").replace("+00:00", "Z"),
            precision,
        )
    raise ValueError("交易日期或时间无法识别")


def identify(text: str, filename: str, headers: list[str], override: str | None) -> str:
    # Only document headings/preamble, not transaction counterparties, identify the issuer.
    content = set()
    for provider, markers in {
        "ccb": ["建设银行个人", "建设银行交易"],
        "abc": ["农业银行"],
        "cmb": ["招商银行交易流水"],
        "wechat": ["微信支付账单", "微信昵称"],
        "alipay": [
            "支付宝账户",
            "支付宝交易明细",
            "支付宝支付科技有限公司  电子客户回单",
        ],
    }.items():
        if any(marker in text for marker in markers):
            content.add(provider)
    if len(content) > 1:
        raise ValueError("文件包含多个来源标题，请检查文件内容")
    detected = next(iter(content), None)
    if override and override not in LABELS:
        raise ValueError("不支持的账单来源")
    if override and detected and override != detected:
        raise ValueError(f"正文明确来自{LABELS[detected]}，与所选来源不符")
    if detected:
        return detected
    if override:
        return override
    names = [provider for provider, label in LABELS.items() if label in filename]
    if len(names) == 1:
        return names[0]
    normal = set(map(normalise_header, headers))
    if {"交易单号", "当前状态"} <= normal:
        return "wechat"
    if "交易订单号" in normal or "支付宝交易号" in normal:
        return "alipay"
    raise ValueError("来源证据不足，请在预览中选择来源")


def read_table(content: bytes, extension: str):
    if extension == ".csv":
        return list(csv.reader(io.StringIO(_decode_csv(content))))
    if extension == ".xlsx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if (
                len(archive.infolist()) > 5000
                or sum(info.file_size for info in archive.infolist())
                > MAX_UPLOAD_BYTES * 2
            ):
                raise ValueError("Excel 解压内容超过安全上限")
        with io.BytesIO(content) as stream:
            book = openpyxl.load_workbook(stream, read_only=True, data_only=True)
            try:
                nonempty = []
                for sheet in book:
                    rows = [
                        [_stringify(v) for v in row]
                        for row in sheet.iter_rows(values_only=True)
                    ]
                    if any(any(v for v in row) for row in rows):
                        nonempty.append(rows)
                if len(nonempty) != 1:
                    raise ValueError("需要一个明细工作表，请分别导出多个账单工作表")
                return nonempty[0]
            finally:
                book.close()
    if extension == ".xls":
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
        try:
            sheets = [s for s in book.sheets() if s.nrows]
            if len(sheets) != 1:
                raise ValueError("需要一个明细工作表")
            sheet = sheets[0]
            return [
                [
                    _stringify(
                        xlrd.xldate.xldate_as_datetime(
                            sheet.cell_value(i, j), book.datemode
                        )
                    )
                    if sheet.cell_type(i, j) == xlrd.XL_CELL_DATE
                    else _stringify(sheet.cell_value(i, j))
                    for j in range(sheet.ncols)
                ]
                for i in range(sheet.nrows)
            ]
        finally:
            book.release_resources()
    raise ValueError("支持 CSV、XLS、XLSX、PDF 和 ZIP")


def pdf_table(content: bytes):
    tables, metadata, provider = [], "", None
    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            if len(document.pages) > 100:
                raise ValueError("PDF 超过 100 页，请按区间导出")
            for page_number, page in enumerate(document.pages, 1):
                text = page.extract_text(x_tolerance=1) or ""
                if not text.strip():
                    raise ValueError(
                        "PDF 没有可读取文本；请提供银行电子明细，而非扫描图片"
                    )
                if provider is None:
                    provider = (
                        "cmb"
                        if "招商银行" in text
                        else "abc"
                        if "农业银行" in text
                        else None
                    )
                if provider not in {"cmb", "abc"}:
                    raise ValueError("尚未识别该 PDF 的银行明细格式")
                words = page.extract_words(x_tolerance=1, y_tolerance=2)
                date_label = "记账日期" if provider == "cmb" else "交易日期"
                heading = next((w for w in words if w["text"] == date_label), None)
                if not heading:
                    raise ValueError(f"第 {page_number} 页未找到明细表头，未跳过该页")
                labels = (
                    [
                        "记账日期",
                        "货币",
                        "交易金额",
                        "联机余额",
                        "交易摘要",
                        "对手信息",
                        "客户摘要",
                    ]
                    if provider == "cmb"
                    else [
                        "交易日期",
                        "交易时间",
                        "交易摘要",
                        "交易金额",
                        "本次余额",
                        "对手信息",
                        "日志号",
                        "交易渠道",
                        "交易附言",
                    ]
                )
                header_words = [w for w in words if abs(w["top"] - heading["top"]) < 3]
                # Agricultural Bank spaces out the three characters 日 志 号.
                for w in header_words:
                    if w["text"] == "日":
                        w["text"] = "日志号"
                columns = sorted(
                    [(w["x0"], w["text"]) for w in header_words if w["text"] in labels]
                )
                if "交易金额" not in [c[1] for c in columns] or len(columns) < 5:
                    raise ValueError("PDF 表头不完整，不能可靠定位金额列")
                metadata += "\n" + "\n".join(
                    w["text"] for w in words if w["top"] < heading["top"]
                )
                pattern = r"20\d{2}-\d{2}-\d{2}" if provider == "cmb" else r"20\d{6}"
                anchors = sorted(
                    [
                        w
                        for w in words
                        if re.fullmatch(pattern, w["text"])
                        and abs(w["x0"] - heading["x0"]) < 5
                        and w["top"] > heading["top"]
                    ],
                    key=lambda w: w["top"],
                )
                if not anchors:
                    raise ValueError(f"第 {page_number} 页没有可定位的交易行")
                footer = min(
                    [
                        w["top"]
                        for w in words
                        if w["top"] > anchors[-1]["top"]
                        and ("温馨提示" in w["text"] or "合计" in w["text"])
                    ]
                    or [page.height - 35]
                )
                for i, anchor in enumerate(anchors):
                    if provider == "cmb":
                        low = (
                            (anchors[i - 1]["top"] + anchor["top"]) / 2
                            if i
                            else anchor["top"] - 14
                        )
                        high = (
                            (anchors[i + 1]["top"] + anchor["top"]) / 2
                            if i + 1 < len(anchors)
                            else min(footer, anchor["top"] + 25)
                        )
                    else:
                        low = anchor["top"] - 2
                        high = (
                            anchors[i + 1]["top"] - 2
                            if i + 1 < len(anchors)
                            else min(footer, anchor["top"] + 14)
                        )
                    row = {}
                    for j, (x, label) in enumerate(columns):
                        right = (
                            columns[j + 1][0] - 2
                            if j + 1 < len(columns)
                            else page.width
                        )
                        tokens = sorted(
                            [
                                w
                                for w in words
                                if low <= w["top"] < high and x - 2 <= w["x0"] < right
                            ],
                            key=lambda w: (round(w["top"], 1), w["x0"]),
                        )
                        row[label] = "".join(w["text"] for w in tokens)
                    row["_page"] = str(page_number)
                    row["_position"] = str(round(anchor["top"], 2))
                    tables.append(row)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF 损坏、加密或文本无法解析") from error
    return provider, metadata, tables


def parse_statement(
    content: bytes,
    filename: str,
    password: str | None = None,
    source: str | None = None,
    *,
    source_timezone: tzinfo,
) -> dict:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("文件为空或超过 25 MB")
    extension = Path(filename).suffix.lower()
    sha = hashlib.sha256(content).hexdigest()
    archive_entry = None
    if extension == ".zip":
        archive_entry, content = _read_zip(content, password)
        extension = Path(archive_entry).suffix.lower()
    if extension == ".pdf":
        detected, preamble, raw_rows = pdf_table(content)
        if source and source != detected:
            raise ValueError(f"PDF 正文来自{LABELS[detected]}，与所选来源不符")
        provider = detected
        indexed_rows = list(enumerate(raw_rows, 1))
    else:
        try:
            table = read_table(content, extension)
        except (
            OSError,
            zipfile.BadZipFile,
            xlrd.XLRDError,
            KeyError,
            TypeError,
        ) as error:
            raise ValueError("表格损坏或内容与扩展名不符") from error
        idx = next(
            (
                i
                for i, row in enumerate(table[:80])
                if any(
                    str(v).strip() in {"交易金额", "金额", "金额(元)", "金额（元）"}
                    for v in row
                )
                and len(row) > 3
            ),
            None,
        )
        if idx is None:
            raise ValueError("未找到交易表头")
        headers = [str(v).strip() for v in table[idx]]
        if len([h for h in headers if h]) != len(set(h for h in headers if h)):
            raise ValueError("表头存在重复字段，不能可靠匹配")
        preamble = "\n".join(" ".join(map(str, row)) for row in table[:idx])
        provider = identify(preamble, archive_entry or filename, headers, source)
        indexed_rows = []
        for n, values in enumerate(table[idx + 1 :], idx + 2):
            if not any(str(v).strip() for v in values):
                continue
            nonempty = [str(v).strip() for v in values if str(v).strip()]
            if len(nonempty) == 1 and (
                nonempty[0].startswith(
                    ("共计", "导出时间", "温馨提示", "说明：", "收入：", "支出：")
                )
                or set(nonempty[0]) <= {"-", "="}
            ):
                continue
            indexed_rows.append(
                (n, {h: str(v).strip() for h, v in zip(headers, values) if h})
            )
    if not indexed_rows or len(indexed_rows) > MAX_ROWS:
        raise ValueError("文件没有交易或超过 10,000 行")
    compact = re.sub(r"\s+", "", preamble)
    number_match = (
        re.search(r"(?:卡号/账号|账号|账户)[：:]([\d*]{10,30})(?!\d)", compact)
        if provider in BANKS
        else None
    )
    owner_match = re.search(r"(?:客户名称|户名|姓名)[：:]([^\s\n]+)", preamble)
    owner = owner_match.group(1) if owner_match else ""
    profile_match = (
        re.search(r"支付宝账户[：:]([^\s\n]+)", preamble)
        if provider == "alipay"
        else re.search(r"微信昵称[：:]\[?([^\]\n]+)", preamble)
    )
    profile = profile_match.group(1).strip() if profile_match else ""
    number = number_match.group(1) if number_match else ""
    if provider in BANKS and not number:
        raise ValueError("银行明细缺少本方账号，请在文件中保留账户信息")
    account = {
        "provider": provider,
        "number": number,
        "owner": owner,
        "identity": digest([provider, number]) if number else "",
        "display_name": f"{LABELS[provider]} · 尾号 {number[-4:]}" if number else "",
    }
    rows = []
    for n, raw in indexed_rows:
        try:
            if provider == "abc":
                currency_match = re.search(r"币种[：:]([^\s\n]+)", preamble)
                if not currency_match:
                    raise ValueError("农行文档缺少币种信息")
                raw = {**raw, "币种": currency_match.group(1)}
            row = normalise_statement_row(
                provider,
                raw,
                profile,
                account,
                source_timezone,
            )
            row.update(row_number=n, raw=raw, error=None)
        except ValueError as error:
            row = {
                "row_number": n,
                "raw": raw,
                "error": str(error),
                "disposition": "error",
            }
        rows.append(row)
    # Check the declared row count and CCB debit/credit totals before offering confirmation.
    expected = re.search(r"共\s*(\d+)\s*笔记录", preamble)
    if expected and int(expected.group(1)) != len(rows):
        raise ValueError("解析条数与文件声明不一致，未提交不完整明细")
    if provider == "ccb" and not any(r["error"] for r in rows):
        for label, sign in [("总支出", -1), ("总收入", 1)]:
            declared = re.search(label + r"[：:]\s*([\d,]+\.\d{2})", preamble)
            actual = sum(
                abs(r["amount_minor"]) for r in rows if r["amount_minor"] * sign > 0
            )
            if declared and amount_minor(declared.group(1)) != actual:
                raise ValueError(f"{label}与文件汇总不一致，请核对解析结果")
    return {
        "filename": Path(filename).name,
        "sha256": sha,
        "format": extension.lstrip("."),
        "archive_entry": archive_entry,
        "source_type": provider,
        "profile": profile,
        "account": account,
        "rows": rows,
        "parser_version": 1,
        "source_timezone": getattr(source_timezone, "key", str(source_timezone)),
    }


def normalise_statement_row(
    provider: str,
    raw: dict,
    profile: str,
    account: dict,
    source_timezone: tzinfo,
) -> dict:
    bank = provider in BANKS

    def value(*names):
        return next(
            (raw[n].strip() for n in names if raw.get(n, "").strip() not in EMPTY), ""
        )

    if bank:
        occurred, precision = timestamp(
            value("记账日期", "交易日期"),
            value("交易时间"),
            source_timezone,
        )
        amount = amount_minor(value("交易金额"))
        balance = value("账户余额", "本次余额", "联机余额")
        balance = amount_minor(balance) if balance else None
        currency = value("币别", "货币", "币种") or "CNY"
        merchant = value("对手信息", "对方账号与户名")
        note = " · ".join(
            dict.fromkeys(
                raw[k]
                for k in ["摘要", "交易摘要", "交易地点/附言", "交易附言", "客户摘要"]
                if raw.get(k)
            )
        )
        reference = value(
            "交易流水号"
        )  # 日志号 is evidence until its issuer uniqueness scope is established.
        method = account["display_name"]
        nature = "ordinary"
        if amount == 0 or any(
            k in note
            for k in [
                "基金申购",
                "基金赎回",
                "基金定投",
                "零钱通",
                "余额宝",
                "享定存",
                "开户起息",
                "定期到期",
            ]
        ):
            nature = "neutral"
        disposition = "posted"
        status = "银行已记账"
        summary = value("摘要", "交易摘要")
        if amount > 0 and any(
            marker in summary for marker in ["退款", "退货", "消费撤销"]
        ):
            nature = "refund"
    else:
        template = PROVIDER_TEMPLATES[provider]
        occurred, precision = timestamp(
            value(*template["occurred_at"]),
            "",
            source_timezone,
        )
        amount = abs(amount_minor(value(*template["amount"])))
        merchant = value(*template["merchant"])
        note = value(*template["note"])
        method = value("收/付款方式", *template["account"])
        reference = value(*template["reference"])
        # A merchant order is not a platform transaction ID.
        if not value("交易订单号", "交易单号", "交易号", "支付宝交易号", "交易订单号"):
            reference = ""
        currency = value("币种", "货币") or "CNY"
        direction = value("收/支", "收支")
        status = value("交易状态", "当前状态")
        category = value("交易分类", "交易类型")
        nature = "ordinary"
        disposition = "posted"
        balance = None
        if status in {"交易关闭", "支付失败", "已取消", "交易取消"}:
            disposition = "non_posted"
            nature = "non_posted"
        elif status and status not in {
            "交易成功",
            "支付成功",
            "转账成功",
            "收款成功",
            "退款成功",
            "已退款",
            "SUCCESS",
            "已存入零钱",
            "已全额退款",
            "对方已收钱",
            "已到账",
            "已转账",
            "资金已到账",
            "等待确认收货",
        }:
            raise ValueError(f"交易状态“{status}”尚不能确定是否实际收付")
        if direction in {"支出", "付款", "支"}:
            amount = -amount
        elif direction in {"收入", "收款", "收"}:
            pass
        elif direction in {"不计收支", "/", ""}:
            nature = "neutral" if disposition == "posted" else nature
            if status == "退款成功" or category == "退款" or category.endswith("-退款"):
                nature = "refund"
            elif disposition == "non_posted":
                pass
            elif (
                provider == "wechat" and "零钱通转出" in category and method == "零钱通"
            ):
                amount = -amount  # Money leaves this source account; the receiving bank records the credit.
            elif any(
                k in category + note
                for k in ["赎回", "转出", "提现", "卖出", "到期", "收益发放", "收款到"]
            ):
                pass
            elif any(
                k in category + note
                for k in ["买入", "申购", "定投", "转入", "购买", "充值"]
            ):
                amount = -amount
            else:
                # Preserve neutral observations without inventing a signed cash flow.
                disposition = "neutral_evidence"
        else:
            raise ValueError("无法识别收支方向")
        if amount > 0 and (
            category == "退款" or category.endswith("-退款") or status == "退款成功"
        ):
            nature = "refund"
    if currency not in {"CNY", "人民币", "人民币元", "元", "RMB"}:
        raise ValueError("当前仅支持人民币入账，已保留原币种")
    if not bank and method in EMPTY and status == "已存入零钱":
        method = "零钱"
    if not bank and method in EMPTY and "建设银行" in value("交易类型"):
        method = value("交易类型")
    return {
        "occurred_at": occurred,
        "time_precision": precision,
        "amount_minor": amount,
        "balance_minor": balance,
        "merchant": merchant or "未提供交易方",
        "note": note,
        "reference": reference,
        "currency": "CNY",
        "account": dict(account)
        if bank
        else payment_account(provider, profile, method),
        "payment_method": method,
        "profile": profile,
        "source_type": provider,
        "status": status,
        "nature": nature,
        "disposition": disposition,
    }


def payment_account(provider: str, profile: str, method: str) -> dict:
    bank_names = {"建设银行": "ccb", "农业银行": "abc", "招商银行": "cmb"}
    found = next(
        ((name, code) for name, code in bank_names.items() if name in method), None
    )
    tail = re.search(r"[（(](\d{4})[)）]", method)
    if found and tail:
        label, code = found
        return {
            "identity": digest(["masked", provider, profile, code, tail.group(1)]),
            "provider": code,
            "display_name": f"{label} · 尾号 {tail.group(1)}",
            "number": "****" + tail.group(1),
            "owner": profile,
        }
    name = method if method not in EMPTY else "未提供资金账户"
    return {
        "identity": digest([provider, profile, name]),
        "provider": provider,
        "number": "",
        "owner": profile,
        "display_name": f"{LABELS[provider]} · {name}",
    }
