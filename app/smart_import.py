"""Upload -> durable preview -> atomic, idempotent confirmation."""

from __future__ import annotations

import base64
import copy
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.database import (
    Account,
    AccountBinding,
    AccountRevision,
    Bill,
    ImportArtifact,
    ImportBatch,
    ImportEvidence,
    ImportIdentity,
    ImportPreview,
    LedgerOrigin,
    RefundDesignation,
    RefundNatureAudit,
)
from app.money import cents, money
from app.review_matters import allocated_bills
from app.statement_parser import BANKS, digest, parse_statement


class UploadFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=35_000_000)
    source_type: str | None = None
    password: str | None = Field(default=None, max_length=256)


class UploadRequest(BaseModel):
    files: list[UploadFile] = Field(min_length=1, max_length=100)


class ReviseRequest(BaseModel):
    # Map the detected account identity to a selected account, not every row to one account.
    accounts: dict[str, str] = Field(default_factory=dict)
    decisions: dict[str, str] = Field(default_factory=dict)


class ConfirmRequest(BaseModel):
    version: str


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def account_dict(account):
    return {
        k: getattr(account, k)
        for k in ["id", "identity", "provider", "display_name", "number", "owner"]
    }


def identity_keys(row, ordinal=0):
    account = row["account"]["identity"]
    source = row["source_type"]
    if row["reference"]:
        # Source-user scope is separate from the payment method, which may be omitted on export.
        return [
            digest(
                [
                    "reference",
                    source,
                    row["profile"],
                    row["reference"],
                    "refund" if row["nature"] == "refund" else "payment",
                ]
            )
        ]
    if source in BANKS and row["balance_minor"] is not None:
        return [
            digest(
                [
                    "bank",
                    source,
                    account,
                    row["occurred_at"][:10],
                    row["amount_minor"],
                    row["balance_minor"],
                    ordinal,
                ]
            )
        ]
    return []


def compatible(a, b):
    if a["amount_minor"] != b["amount_minor"] or a["currency"] != b["currency"]:
        return False
    return a["occurred_at"][:10] == b["occurred_at"][:10]


def build_plan(db, documents, overrides=None, decisions=None):
    overrides, decisions = overrides or {}, decisions or {}
    documents = copy.deepcopy(documents)
    accounts = {a.id: account_dict(a) for a in db.scalars(select(Account)).all()}
    bindings = {
        binding.detected_identity: binding.account_id
        for binding in db.scalars(select(AccountBinding)).all()
    }
    known = {a["identity"]: a for a in accounts.values()}
    for doc in documents:
        if doc.get("error"):
            continue
        if doc["source_type"] in BANKS:
            known.setdefault(doc["account"]["identity"], doc["account"])
        for row in doc["rows"]:
            if not row.get("error"):
                known.setdefault(row["account"]["identity"], row["account"])
    bank_accounts = [
        a
        for a in known.values()
        if a["provider"] in BANKS and a["number"] and "*" not in a["number"]
    ]
    for doc in documents:
        for row in doc.get("rows", []):
            if row.get("error"):
                continue
            original = row["account"]["identity"]
            row["detected_account_identity"] = original
            if original in overrides:
                selected = known.get(overrides[original])
                if selected is None:
                    raise ValueError("所选账户已不存在")
                row["account"] = selected
                row["account_basis"] = "预览中人工指定"
            elif original in bindings and bindings[original] in accounts:
                row["account"] = accounts[bindings[original]]
                row["account_basis"] = "使用之前确认的账户匹配"
            elif row["account"]["number"].startswith("****"):
                candidates = [
                    a
                    for a in bank_accounts
                    if a["provider"] == row["account"]["provider"]
                    and a["number"].endswith(row["account"]["number"][-4:])
                ]
                if len(candidates) == 1:
                    row["account"] = candidates[0]
                    row["account_basis"] = "银行与尾号匹配，请核对"
                elif len(candidates) > 1:
                    row["error"] = "同银行有多张卡尾号相同，请在预览中选择账户"
            known.setdefault(row["account"]["identity"], row["account"])

    identities = {
        identity.key: identity.bill_id
        for identity in db.scalars(select(ImportIdentity)).all()
    }
    bills = {bill.id: bill for bill in db.scalars(select(Bill)).all()}
    evidence = defaultdict(list)
    for item in db.scalars(select(ImportEvidence)).all():
        if item.bill_id:
            evidence[item.bill_id].append(json.loads(item.record_json))
    # Read-only compatibility bridge: old origins retain exact references or platform IDs in raw fields.
    legacy_refs = defaultdict(list)
    for origin in db.scalars(select(LedgerOrigin)).all():
        raw = json.loads(origin.raw_payload or "{}")
        ref = next(
            (
                raw[k].strip()
                for k in ["交易订单号", "交易单号", "支付宝交易号", "交易号"]
                if raw.get(k)
            ),
            origin.source_reference,
        )
        if ref and (
            origin.bill_id not in evidence
            or any(not record.get("profile") for record in evidence[origin.bill_id])
        ):
            legacy_refs[(origin.source_type, ref)].append(origin.bill_id)
    seen_files = {
        artifact.sha256 for artifact in db.scalars(select(ImportArtifact)).all()
    }
    virtual = {}
    counts = Counter()
    for d, doc in enumerate(documents):
        if doc.get("error"):
            counts["errors"] += 1
            continue
        doc["duplicate"] = doc["sha256"] in seen_files
        seen_files.add(doc["sha256"])
        used = set()
        occurrences = Counter()
        for i, row in enumerate(doc["rows"]):
            row["row_id"] = f"{d}:{i}"
            row["action"] = (
                "error"
                if row.get("error")
                else "record"
                if row["disposition"] != "posted"
                else "new"
            )
            row["match"] = None
            row["keys"] = []
            if doc["duplicate"]:
                row["action"] = "duplicate_file"
            elif row["action"] == "new":
                base = digest(
                    [
                        row["account"]["identity"],
                        row["occurred_at"][:10],
                        row["amount_minor"],
                        row["balance_minor"],
                    ]
                )
                ordinal = occurrences[base]
                occurrences[base] += 1
                keys = identity_keys(row, ordinal)
                row["keys"] = keys
                targets = {identities[k] for k in keys if k in identities}
                legacy = (
                    legacy_refs.get((row["source_type"], row["reference"]), [])
                    if row["reference"]
                    else []
                )
                targets.update(legacy)
                if len(targets) > 1:
                    row.update(
                        action="error",
                        error="已有多笔流水使用同一交易标识，请先核验历史重复",
                    )
                elif targets:
                    target = next(iter(targets))
                    other = virtual.get(target)
                    if other is None:
                        bill = bills[target]
                        other = {
                            "amount_minor": cents(bill.amount),
                            "currency": bill.currency,
                            "occurred_at": bill.occurred_at.isoformat(),
                        }
                    if not compatible(row, other):
                        row.update(
                            action="error",
                            error="交易标识相同但日期或金额冲突，不能覆盖已有交易",
                        )
                    else:
                        row.update(action="supplement", match=target)
                elif not keys:
                    # Missing identifiers: show a decision in the same preview instead of silently merging.
                    candidates = []
                    for target, items in evidence.items():
                        if target in used:
                            continue
                        if any(
                            other["account"]["identity"] == row["account"]["identity"]
                            and compatible(row, other)
                            and (
                                row["source_type"] in BANKS
                                or row["occurred_at"] == other["occurred_at"]
                            )
                            for other in items
                        ):
                            candidates.append(target)
                    if candidates:
                        choice = decisions.get(row["row_id"])
                        selected = next(
                            (
                                candidate
                                for candidate in candidates
                                if choice == f"match:{candidate}"
                            ),
                            None,
                        )
                        if choice == "new":
                            pass
                        elif selected is not None:
                            row.update(action="supplement", match=selected)
                        else:
                            row.update(
                                action="ambiguous",
                                candidates=candidates,
                                error="缺少交易标识，请选择补充已有交易或保留为新交易",
                            )
                if row["action"] == "new":
                    target = f"new:{d}:{i}"
                    virtual[target] = row
                    row["match"] = target
                if row["action"] in {"new", "supplement"}:
                    used.add(row["match"])
                    for key in keys:
                        identities[key] = row["match"]
                    if isinstance(row["match"], str):
                        evidence[row["match"]].append(row)
            counts[row["action"]] += 1

    # Match across separate uploads as well as within this batch. Resolve a masked
    # card only when exactly one full account of that bank shares its suffix.
    def resolved_account(spec):
        if spec["number"].startswith("****"):
            options = [
                a
                for a in bank_accounts
                if a["provider"] == spec["provider"]
                and a["number"].endswith(spec["number"][-4:])
            ]
            if len(options) == 1:
                return options[0]["identity"]
        return spec["identity"]

    new_rows = {
        r["match"]: r
        for d in documents
        for r in d.get("rows", [])
        if r.get("action") == "new"
    }
    nodes = {
        target: items for target, items in evidence.items() if isinstance(target, int)
    }
    nodes.update({target: [row] for target, row in new_rows.items()})
    possible = {}
    for target, row in new_rows.items():
        options = set()
        if row["account"]["provider"] not in BANKS:
            continue
        for other_target, items in nodes.items():
            if other_target == target:
                continue
            # A distinct native transaction ID or bank balance proves this is not a second observation.
            if any(
                o["source_type"] == row["source_type"]
                and (
                    (
                        o.get("reference")
                        and row["reference"]
                        and o["reference"] != row["reference"]
                    )
                    or (
                        o.get("balance_minor") is not None
                        and row["balance_minor"] is not None
                        and o["balance_minor"] != row["balance_minor"]
                    )
                )
                for o in items
            ):
                continue
            for other in items:
                if (row["source_type"] in BANKS) == (other["source_type"] in BANKS):
                    continue
                if not compatible(row, other) or resolved_account(
                    row["account"]
                ) != resolved_account(other["account"]):
                    continue
                bank, wallet = (
                    (row, other) if row["source_type"] in BANKS else (other, row)
                )
                channel = (
                    ["支付宝", "蚂蚁"]
                    if wallet["source_type"] == "alipay"
                    else ["微信", "财付通"]
                )
                if any(marker in bank["note"] + bank["merchant"] for marker in channel):
                    options.add(other_target)
        possible[target] = options
    reverse = defaultdict(set)
    for target, options in possible.items():
        for other in options:
            reverse[other].add(target)
    for target, options in possible.items():
        row = new_rows[target]
        if row["action"] != "new" or not options:
            continue
        unique = len(options) == 1
        other = next(iter(options))
        unique = unique and len(reverse[other]) == 1
        if other in new_rows:
            unique = unique and possible.get(other) == {target}
        if unique:
            if other in new_rows and row["source_type"] in BANKS:
                continue  # Keep the bank fact as canonical for a new pair.
            old = target
            row.update(
                action="supplement",
                match=other,
                match_basis="同一银行卡、同日同额、银行渠道证据一致",
            )
            for doc in documents:
                for linked in doc.get("rows", []):
                    if linked.get("match") == old:
                        linked["match"] = other
            counts["new"] -= 1
            counts["supplement"] += 1
        else:
            choice = decisions.get(row["row_id"])
            selected = next(
                (candidate for candidate in options if choice == f"match:{candidate}"),
                None,
            )
            if selected is not None:
                row.update(
                    action="supplement", match=selected, match_basis="预览中人工确认"
                )
                counts["new"] -= 1
                counts["supplement"] += 1
            elif choice != "new":
                row.update(
                    action="ambiguous",
                    candidates=sorted(options, key=str),
                    error="存在多个同卡同额记录，请核对是否为同一笔支付",
                )
                counts["new"] -= 1
                counts["ambiguous"] += 1
    nodes_by_row = {
        f"new:{r['row_id']}": r for doc in documents for r in doc.get("rows", [])
    }
    for doc in documents:
        for row in doc.get("rows", []):
            if row.get("action") != "supplement":
                continue
            target = row["match"]
            visited = set()
            while isinstance(target, str):
                if target in visited:
                    row.update(action="error", error="匹配形成循环，请保留一笔为新交易")
                    break
                visited.add(target)
                related = nodes_by_row.get(target)
                if related is None or related["action"] == "new":
                    break
                if related["action"] != "supplement":
                    row.update(
                        action="error", error="所选交易仍待处理，请先确定要保留的交易"
                    )
                    break
                target = related["match"]
            row["match"] = target
    counts = Counter(r["action"] for doc in documents for r in doc.get("rows", []))
    counts["errors"] = sum(bool(doc.get("error")) for doc in documents)
    result = {
        "documents": documents,
        "counts": dict(counts),
        "accounts": list(known.values()),
        "can_confirm": not any(counts[k] for k in ["error", "errors", "ambiguous"]),
    }
    result["version"] = digest(result)
    return result


def public_plan(token, plan):
    return {"token": token, **plan}


def commit_plan(db, plan):
    protected_bill_ids = set(allocated_bills(db))
    protected_bill_ids.update(db.scalars(select(RefundNatureAudit.bill_id)).all())
    new_targets = {}
    batches = []
    account_cache = {a.identity: a for a in db.scalars(select(Account)).all()}

    def ensure_account(spec):
        account = account_cache.get(spec["identity"])
        if account is None:
            account = Account(
                **{
                    k: spec[k]
                    for k in ["identity", "provider", "display_name", "number", "owner"]
                }
            )
            db.add(account)
            db.flush()
            account_cache[account.identity] = account
        return account

    # Create canonical facts first, because a wallet file can precede its bank statement.
    for doc in plan["documents"]:
        if doc.get("duplicate"):
            continue
        for row in doc["rows"]:
            if row["action"] != "new":
                continue
            spec = row["account"]
            account = ensure_account(spec)
            bill = Bill(
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                merchant=row["merchant"],
                note=row["note"],
                amount=money(row["amount_minor"]),
                currency=row["currency"],
                account_name=account.display_name,
                account_id=account.id,
                time_precision=row["time_precision"],
                import_nature=row["nature"],
                category="未分类",
                tags="",
                aggregate_excluded=row["nature"] == "neutral",
            )
            db.add(bill)
            db.flush()
            new_targets[row["match"]] = bill.id
            if row["nature"] == "refund":
                db.add(RefundDesignation(bill_id=bill.id, created_at=datetime.now()))
    for doc in plan["documents"]:
        if doc.get("duplicate"):
            continue
        batch = ImportBatch(
            source_type=doc["source_type"],
            filename=doc["filename"],
            imported_at=datetime.now(),
            row_count=len(doc["rows"]),
            imported_count=sum(r["action"] == "new" for r in doc["rows"]),
        )
        db.add(batch)
        db.flush()
        db.add(
            ImportArtifact(
                import_batch_id=batch.id,
                source_type=doc["source_type"],
                filename=doc["filename"],
                file_format=doc["format"],
                archive_entry=doc["archive_entry"],
                sha256=doc["sha256"],
            )
        )
        for row in doc["rows"]:
            target = row["match"]
            bill_id = new_targets.get(target, target) if target is not None else None
            if bill_id:
                if (
                    row.get("detected_account_identity")
                    and row["detected_account_identity"] != row["account"]["identity"]
                ):
                    selected = ensure_account(row["account"])
                    binding = db.get(AccountBinding, row["detected_account_identity"])
                    if binding is None:
                        binding = AccountBinding(
                            detected_identity=row["detected_account_identity"],
                            account_id=selected.id,
                            basis=row.get("account_basis", "导入确认"),
                        )
                        db.add(binding)
                    else:
                        binding.account_id = selected.id
                        binding.basis = row.get("account_basis", "导入确认")
                    db.flush()
                bill = db.get(Bill, bill_id)
                previous_account = (
                    db.get(Account, bill.account_id) if bill.account_id else None
                )
                if (
                    row["source_type"] in BANKS
                    and previous_account
                    and previous_account.number.startswith("****")
                    and row["account"]["number"].endswith(previous_account.number[-4:])
                    and not db.scalar(
                        select(AccountRevision.id).where(
                            AccountRevision.bill_id == bill_id
                        )
                    )
                ):
                    account = ensure_account(row["account"])
                    bill.account_id = account.id
                    bill.account_name = account.display_name
                if not db.scalar(
                    select(LedgerOrigin.id).where(LedgerOrigin.bill_id == bill_id)
                ):
                    db.add(
                        LedgerOrigin(
                            bill_id=bill_id,
                            source_type=doc["source_type"],
                            source_reference=row["reference"],
                            import_batch_id=batch.id,
                            source_row_number=row["row_number"],
                            raw_payload=dump(row["raw"]),
                        )
                    )
                    db.flush()
                # Complementary evidence never overwrites existing or manually corrected fields.
                if not bill.note and row["note"]:
                    bill.note = row["note"]
                if bill.time_precision == "day" and row["time_precision"] == "second":
                    bill.occurred_at = datetime.fromisoformat(row["occurred_at"])
                    bill.time_precision = "second"
                # A platform describes the purpose where a bank may only say "payment".
                # Apply that richer evidence independently of upload order.
                semantic_editable = (
                    bill.id not in protected_bill_ids
                    and not bill.duplicate_of_id
                    and not bill.transfer_group_id
                )
                if (
                    semantic_editable
                    and row["source_type"] not in BANKS
                    and row["account"]["provider"] in BANKS
                ):
                    was_auto_neutral = (
                        bill.import_nature == "neutral"
                        and not bill.duplicate_of_id
                        and not bill.transfer_group_id
                    )
                    if bill.import_nature != "refund":
                        bill.import_nature = row["nature"]
                    if bill.import_nature == "neutral":
                        bill.aggregate_excluded = True
                    elif was_auto_neutral:
                        bill.aggregate_excluded = False
                elif (
                    semantic_editable
                    and row["source_type"] in BANKS
                    and row["nature"] in {"neutral", "refund"}
                ):
                    platform_evidence = db.scalar(
                        select(ImportEvidence.id)
                        .join(
                            ImportBatch,
                            ImportEvidence.import_batch_id == ImportBatch.id,
                        )
                        .where(
                            ImportEvidence.bill_id == bill_id,
                            ImportBatch.source_type.not_in(BANKS),
                        )
                    )
                    if not platform_evidence:
                        if bill.import_nature != "refund":
                            bill.import_nature = row["nature"]
                        bill.aggregate_excluded = bill.import_nature == "neutral"
                if (
                    semantic_editable
                    and bill.import_nature == "refund"
                    and not db.get(RefundDesignation, bill.id)
                ):
                    db.add(
                        RefundDesignation(bill_id=bill.id, created_at=datetime.now())
                    )
                    db.flush()
                for key in row["keys"]:
                    if not db.get(ImportIdentity, key):
                        db.add(ImportIdentity(key=key, bill_id=bill_id))
                        db.flush()
            db.add(
                ImportEvidence(
                    bill_id=bill_id,
                    import_batch_id=batch.id,
                    row_number=row["row_number"],
                    record_json=dump(row),
                    disposition=row["action"] if bill_id else row["disposition"],
                )
            )
        batches.append(batch.id)
    return {"counts": plan["counts"], "batch_ids": batches}


def register_import_routes(app, get_db):
    @app.get("/api/intake/history")
    def history(db=Depends(get_db)):
        batches = db.scalars(
            select(ImportBatch)
            .where(ImportBatch.id.in_(select(ImportEvidence.import_batch_id)))
            .order_by(ImportBatch.id.desc())
            .limit(30)
        ).all()
        return [
            {
                "id": b.id,
                "filename": b.filename,
                "source_type": b.source_type,
                "row_count": b.row_count,
                "imported_count": b.imported_count,
                "imported_at": b.imported_at.isoformat(),
            }
            for b in batches
        ]

    @app.get("/api/accounts")
    def list_accounts(db=Depends(get_db)):
        return [
            account_dict(a)
            for a in db.scalars(select(Account).order_by(Account.display_name)).all()
        ]

    @app.post("/api/intake/preview")
    def upload(payload: UploadRequest, db=Depends(get_db)):
        if sum(len(f.content_base64) for f in payload.files) > 140_000_000:
            raise HTTPException(413, "一次最多上传约 100 MB 文件")
        documents = []
        for item in payload.files:
            try:
                content = base64.b64decode(item.content_base64, validate=True)
                documents.append(
                    parse_statement(
                        content, item.filename, item.password, item.source_type
                    )
                )
            except (ValueError, TypeError) as error:
                documents.append(
                    {"filename": item.filename, "error": str(error), "rows": []}
                )
        plan = build_plan(db, documents)
        token = uuid4().hex
        # Passwords and binary uploads are not staged. Expired preview evidence is removed locally.
        cutoff = datetime.now() - timedelta(hours=24)
        for expired in db.scalars(
            select(ImportPreview).where(
                ImportPreview.created_at < cutoff, ImportPreview.result_json.is_(None)
            )
        ).all():
            db.delete(expired)
        db.add(
            ImportPreview(
                token=token,
                payload_json=dump(documents),
                plan_json=dump(plan),
                created_at=datetime.now(),
            )
        )
        db.commit()
        return public_plan(token, plan)

    @app.put("/api/intake/{token}/preview")
    def revise(token: str, payload: ReviseRequest, db=Depends(get_db)):
        stage = db.get(ImportPreview, token)
        if (
            not stage
            or stage.result_json
            or stage.created_at < datetime.now() - timedelta(hours=24)
        ):
            raise HTTPException(409, "预览已失效，请重新上传")
        stored = json.loads(stage.payload_json)
        documents = stored["documents"] if isinstance(stored, dict) else stored
        try:
            plan = build_plan(db, documents, payload.accounts, payload.decisions)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        stage.payload_json = dump(
            {
                "documents": documents,
                "accounts": payload.accounts,
                "decisions": payload.decisions,
            }
        )
        stage.plan_json = dump(plan)
        db.commit()
        return public_plan(token, plan)

    @app.post("/api/intake/{token}/confirm")
    def confirm(token: str, payload: ConfirmRequest, db=Depends(get_db)):
        try:
            if db.bind.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            stage = db.get(ImportPreview, token)
            if not stage:
                raise HTTPException(404, "预览不存在，请重新上传")
            if stage.result_json:
                if payload.version != json.loads(stage.plan_json)["version"]:
                    raise HTTPException(409, "确认版本不符")
                return json.loads(stage.result_json)
            if stage.created_at < datetime.now() - timedelta(hours=24):
                raise HTTPException(409, "预览已过期，请重新上传")
            previous = json.loads(stage.plan_json)
            if payload.version != previous["version"]:
                raise HTTPException(409, "预览已变化，请核对最新预览")
            stored = json.loads(stage.payload_json)
            documents = stored["documents"] if isinstance(stored, dict) else stored
            current = build_plan(
                db,
                documents,
                stored.get("accounts") if isinstance(stored, dict) else None,
                stored.get("decisions") if isinstance(stored, dict) else None,
            )
            if current["version"] != previous["version"]:
                raise HTTPException(409, "账本已变化，请刷新预览后确认")
            if not current["can_confirm"]:
                raise HTTPException(422, "请先处理预览中标出的错误或歧义")
            result = commit_plan(db, current)
            stage.result_json = dump(result)
            stage.payload_json = "[]"
            stage.plan_json = dump(
                {"version": current["version"], "counts": current["counts"]}
            )
            db.commit()
            return result
        except (IntegrityError, OperationalError) as error:
            db.rollback()
            raise HTTPException(409, "账本正在写入，请重试；本次未部分导入") from error
        except Exception:
            db.rollback()
            raise

    @app.get("/api/intake/batches/{batch_id}/rows")
    def rows(batch_id: int, db=Depends(get_db)):
        return [
            {
                "id": r.id,
                "bill_id": r.bill_id,
                "disposition": r.disposition,
                "record": json.loads(r.record_json),
            }
            for r in db.scalars(
                select(ImportEvidence)
                .where(ImportEvidence.import_batch_id == batch_id)
                .order_by(ImportEvidence.row_number)
            ).all()
        ]
