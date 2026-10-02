"""Build fictional statement fixtures from locally authorized format examples.

No source values, filenames, hashes or paths are copied into the fixtures.
Only a recognized provider and allowlisted column names cross the boundary.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.parser.statement_parser import BANKS, LABELS, parse_statement


HEADERS = {
    "记账日期", "交易日期", "交易时间", "交易创建时间", "付款时间",
    "交易金额", "金额", "金额(元)", "金额（元）", "币别", "货币", "币种",
    "账户余额", "本次余额", "联机余额", "对手信息", "对方账号与户名",
    "交易对方", "交易对手", "交易对方名称", "对方账户", "收/付款方",
    "摘要", "交易摘要", "交易地点/附言", "交易附言", "客户摘要",
    "交易流水号", "交易订单号", "交易单号", "交易号", "支付宝交易号",
    "商家订单号", "商户单号", "日志号", "收/支", "收支", "当前状态",
    "交易状态", "收/付款方式", "收/支方式", "支付方式", "付款方式",
    "资金渠道", "收款方式", "交易分类", "交易类型", "商品", "商品名称",
    "商品说明", "备注", "交易渠道",
}


def mock_document(document: dict, count: int = 24) -> bytes:
    provider = document["source_type"]
    headers = list(dict.fromkeys(
        key for row in document["rows"] for key in row["raw"] if key in HEADERS
    ))
    if not headers:
        raise ValueError("NO_SUPPORTED_COLUMNS")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    title = {
        "ccb": "建设银行个人交易明细", "cmb": "招商银行交易流水",
        "abc": "农业银行交易明细", "wechat": "微信支付账单明细",
        "alipay": "支付宝交易明细",
    }[provider]
    writer.writerow([title])
    if provider in BANKS:
        writer.writerow(["账号：990000000000001234"])
        writer.writerow(["姓名：测试用户"])
        writer.writerow(["币种：人民币"])
    else:
        writer.writerow(["微信昵称：[测试用户]" if provider == "wechat"
                         else "支付宝账户：mock@example.invalid"])
    writer.writerow(headers)
    for i in range(count):
        moment = datetime(2024, 1, 1, 12) + timedelta(days=i)
        income = i % 3 == 0
        amount = f"{10 + i}.25"
        values = []
        for key in headers:
            value = ""
            if key in {"交易日期", "记账日期"}:
                value = moment.strftime("%Y%m%d" if provider == "abc" else "%Y-%m-%d")
            elif key == "交易时间":
                value = moment.strftime("%H%M%S" if provider in BANKS else "%Y-%m-%d %H:%M:%S")
            elif key in {"交易创建时间", "付款时间"}:
                value = moment.strftime("%Y-%m-%d %H:%M:%S")
            elif key in {"交易金额", "金额", "金额(元)", "金额（元）"}:
                value = ("-" if provider in BANKS and not income else "") + amount
            elif key in {"币别", "货币", "币种"}:
                value = "人民币" if provider == "abc" else "CNY"
            elif key in {"账户余额", "本次余额", "联机余额"}:
                value = "1000.00"
            elif key in {"收/支", "收支"}:
                value = "收入" if income else "支出"
            elif key in {"当前状态", "交易状态"}:
                value = "交易成功"
            elif key in {"收/付款方式", "收/支方式", "支付方式", "付款方式", "资金渠道", "收款方式"}:
                value = "零钱" if provider == "wechat" else "余额"
            elif key in {"交易分类", "交易类型"}:
                value = "转账" if income else "商户消费"
            elif key in {"交易流水号", "交易订单号", "交易单号", "交易号", "支付宝交易号", "商家订单号", "商户单号", "日志号"}:
                value = f"MOCK-{provider}-{i:04d}"
            elif key in {"对手信息", "对方账号与户名", "交易对方", "交易对手", "交易对方名称", "对方账户", "收/付款方"}:
                value = f"测试商户{i % 4}"
            elif key != "交易渠道":
                value = f"测试交易{i:04d}"
            values.append(value)
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig")


def generate(source: Path, output: Path) -> dict:
    if not source.is_dir():
        raise ValueError("SOURCE_DIRECTORY_NOT_FOUND")
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError("OUTPUT_MUST_BE_OUTSIDE_SOURCE")
    output.mkdir(parents=True, exist_ok=True)
    report = {"fixtures": [], "unreadable_sources": 0}
    seen = set()
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".xls", ".xlsx", ".pdf", ".zip"}:
            continue
        try:
            document = parse_statement(path.read_bytes(), path.name, source_timezone=ZoneInfo("Asia/Hong_Kong"))
        except (ValueError, OSError):
            report["unreadable_sources"] += 1
            continue
        signature = (document["source_type"], path.suffix.lower(),
                     tuple(sorted({key for row in document["rows"] for key in row["raw"] if key in HEADERS})))
        if signature in seen:
            continue
        content = mock_document(document)
        filename = f"{document['source_type']}-{len(report['fixtures']) + 1}.csv"
        parsed = parse_statement(content, filename, source_timezone=ZoneInfo("Asia/Hong_Kong"))
        if any(row["error"] or row["disposition"] != "posted" for row in parsed["rows"]):
            raise ValueError("MOCK_PARSER_VALIDATION_FAILED")
        (output / filename).write_bytes(content)
        report["fixtures"].append({"file": filename, "provider": document["source_type"],
                                   "source_format": path.suffix.lower().lstrip("."),
                                   "mock_rows": len(parsed["rows"])})
        seen.add(signature)
    if not report["fixtures"]:
        raise ValueError("NO_READABLE_STATEMENT_FORMAT")
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.source, args.output), ensure_ascii=False))
