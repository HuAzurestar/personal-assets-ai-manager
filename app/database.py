from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATABASE_URL, ensure_data_dir

ensure_data_dir()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[str] = mapped_column(DateTime(timezone=False))
    merchant: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    category: Mapped[str] = mapped_column(String(80), default="未分类")
    tags: Mapped[str] = mapped_column(String(500), default="")
    account_name: Mapped[str] = mapped_column(String(120), default="未提供账户")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    time_precision: Mapped[str] = mapped_column(String(12), default="second")
    import_nature: Mapped[str] = mapped_column(String(24), default="ordinary")
    aggregate_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tag_state_json: Mapped[str] = mapped_column(Text, default="{}")


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity: Mapped[str] = mapped_column(String(160), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(120))
    number: Mapped[str] = mapped_column(String(64), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")


class AccountBinding(Base):
    __tablename__ = "account_bindings"
    detected_identity: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    basis: Mapped[str] = mapped_column(String(120))


class ImportEvidence(Base):
    __tablename__ = "import_evidence"
    __table_args__ = (UniqueConstraint("import_batch_id", "row_number"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int | None] = mapped_column(ForeignKey("bills.id"), nullable=True, index=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    row_number: Mapped[int] = mapped_column(Integer)
    record_json: Mapped[str] = mapped_column(Text)
    disposition: Mapped[str] = mapped_column(String(32))


class ImportIdentity(Base):
    __tablename__ = "import_identities"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)


class ImportPreview(Base):
    __tablename__ = "import_previews"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class TagView(Base):
    __tablename__ = "tag_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    system_name: Mapped[str] = mapped_column(String(64), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class ViewTag(Base):
    __tablename__ = "view_tags"
    __table_args__ = (
        UniqueConstraint("view_id", "name", name="uq_view_tag_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    view_id: Mapped[int] = mapped_column(ForeignKey("tag_views.id"))
    name: Mapped[str] = mapped_column(String(120))
    system_name: Mapped[str] = mapped_column(String(64), default="")
    is_unclassified: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class BillViewTag(Base):
    __tablename__ = "bill_view_tags"
    __table_args__ = (UniqueConstraint("bill_id", "view_id", name="uq_bill_view_assignment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    view_id: Mapped[int] = mapped_column(ForeignKey("tag_views.id"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("view_tags.id"))
    strategy: Mapped[str] = mapped_column(String(60), default="manual")
    confidence: Mapped[float] = mapped_column(Float, default=0.95)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class TagChangeLog(Base):
    __tablename__ = "tag_change_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    view_id: Mapped[int] = mapped_column(ForeignKey("tag_views.id"))
    tag_id: Mapped[int | None] = mapped_column(ForeignKey("view_tags.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class BillTag(Base):
    __tablename__ = "bill_tags"

    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[str] = mapped_column(DateTime(timezone=False))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    batch_token: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ImportArtifact(Base):
    __tablename__ = "import_artifacts"
    __table_args__ = (UniqueConstraint("source_type", "sha256", name="uq_import_artifact_source_sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    source_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(12))
    archive_entry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))


class LedgerOrigin(Base):
    __tablename__ = "ledger_origins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), unique=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_reference: Mapped[str] = mapped_column(String(160), default="")
    raw_payload: Mapped[str] = mapped_column(Text, default="")
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    source_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ImportRowIssue(Base):
    __tablename__ = "import_row_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    source_row_number: Mapped[int] = mapped_column(Integer)
    raw_payload: Mapped[str] = mapped_column(Text)
    error: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str] = mapped_column(Text, default="")
    bill_id: Mapped[int | None] = mapped_column(ForeignKey("bills.id"), nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)


class ImportIssueAction(Base):
    __tablename__ = "import_issue_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("import_row_issues.id"))
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class TagAudit(Base):
    __tablename__ = "tag_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    category: Mapped[str] = mapped_column(String(80))
    tags: Mapped[str] = mapped_column(String(500), default="")
    tag_state_json: Mapped[str] = mapped_column(Text, default="{}")
    strategy: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(80), default="")
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    action: Mapped[str] = mapped_column(String(40), default="confirm")
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    reason: Mapped[str] = mapped_column(Text, default="")
    before_state_json: Mapped[str] = mapped_column(Text, default="{}")
    before_category: Mapped[str] = mapped_column(String(80), default="未分类")
    reverses_audit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    undone_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    request_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class ReviewCandidate(Base):
    __tablename__ = "review_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_type: Mapped[str] = mapped_column(String(30))
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    related_bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    member_bill_ids: Mapped[str] = mapped_column(Text, default="")
    group_fingerprint: Mapped[str] = mapped_column(String(256), default="")
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfer_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transfer_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retained_bill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class CandidateActionLog(Base):
    __tablename__ = "candidate_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("review_candidates.id"))
    action: Mapped[str] = mapped_column(String(40))
    before_state: Mapped[str] = mapped_column(Text)
    after_state: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    reason: Mapped[str] = mapped_column(Text, default="")
    reverses_action_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    request_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    undone_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)


class AccountRevision(Base):
    __tablename__ = "account_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    before_account: Mapped[str] = mapped_column(String(120))
    after_account: Mapped[str] = mapped_column(String(120))
    before_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(40), default="confirm")
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    reason: Mapped[str] = mapped_column(Text, default="")
    reverses_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    undone_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    request_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class RefundAllocation(Base):
    __tablename__ = "refund_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refund_bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    expense_bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    request_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))
    revoked_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)


class RefundAllocationAudit(Base):
    __tablename__ = "refund_allocation_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("refund_allocations.id"))
    action: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    reason: Mapped[str] = mapped_column(Text, default="")
    before_state: Mapped[str] = mapped_column(Text)
    after_state: Mapped[str] = mapped_column(Text)
    reverses_audit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class RefundDesignation(Base):
    """Refund nature survives undoing an allocation; never becomes income."""
    __tablename__ = "refund_designations"
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), primary_key=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class RefundNatureAudit(Base):
    __tablename__ = "refund_nature_audits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class ReviewMatter(Base):
    __tablename__ = "review_matters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class ReviewMatterRevision(Base):
    """Immutable confirmed snapshots; current pointer lives on ReviewMatter."""
    __tablename__ = "review_matter_revisions"
    __table_args__ = (UniqueConstraint("matter_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matter_id: Mapped[int] = mapped_column(ForeignKey("review_matters.id"))
    version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80), default="local-user")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    request_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class AssetSnapshot(Base):
    __tablename__ = "asset_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_name: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(80))
    balance: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[str] = mapped_column(DateTime(timezone=False))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    if DATABASE_URL.startswith("sqlite"):
        migrations = {
            "account_revisions": {"before_account_id": "INTEGER", "after_account_id": "INTEGER"},
            "import_batches": {"batch_token": "VARCHAR(64)"},
            "ledger_origins": {"source_row_number": "INTEGER"},
            "bills": {
                "account_id": "INTEGER REFERENCES accounts(id)",
                "time_precision": "VARCHAR(12) NOT NULL DEFAULT 'second'",
                "import_nature": "VARCHAR(24) NOT NULL DEFAULT 'ordinary'",
                "currency": "VARCHAR(3) NOT NULL DEFAULT 'CNY'",
                "account_name": "VARCHAR(120) NOT NULL DEFAULT '未提供账户'",
                "aggregate_excluded": "BOOLEAN NOT NULL DEFAULT 0",
                "transfer_group_id": "VARCHAR(64)",
                "duplicate_of_id": "INTEGER",
                "tag_state_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "tag_views": {"system_name": "VARCHAR(64) NOT NULL DEFAULT ''"},
            "view_tags": {"system_name": "VARCHAR(64) NOT NULL DEFAULT ''"},
            "tag_audits": {"tag_state_json": "TEXT NOT NULL DEFAULT '{}'"},
            "review_candidates": {
                "transfer_group_id": "VARCHAR(64)",
                "member_bill_ids": "TEXT NOT NULL DEFAULT ''",
                "group_fingerprint": "VARCHAR(256) NOT NULL DEFAULT ''",
                "superseded_by_id": "INTEGER",
                "transfer_kind": "VARCHAR(32)",
                "retained_bill_id": "INTEGER",
                "resolved_at": "DATETIME",
            },
            "candidate_action_logs": {
                "after_state": "TEXT NOT NULL DEFAULT ''",
                "actor": "VARCHAR(80) NOT NULL DEFAULT 'local-user'",
                "reason": "TEXT NOT NULL DEFAULT ''",
                "reverses_action_id": "INTEGER",
                "idempotency_key": "VARCHAR(120)",
                "request_payload": "TEXT NOT NULL DEFAULT ''",
            },
        }
        migrations["tag_audits"].update({
            "action": "VARCHAR(40) NOT NULL DEFAULT 'confirm'",
            "actor": "VARCHAR(80) NOT NULL DEFAULT 'local-user'",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "before_state_json": "TEXT NOT NULL DEFAULT '{}'",
            "before_category": "VARCHAR(80) NOT NULL DEFAULT '未分类'",
            "reverses_audit_id": "INTEGER",
            "undone": "BOOLEAN NOT NULL DEFAULT 0",
            "undone_at": "DATETIME",
            "idempotency_key": "VARCHAR(120)",
            "request_payload": "TEXT NOT NULL DEFAULT ''",
        })
        with engine.begin() as connection:
            for table, columns in migrations.items():
                existing = {item["name"] for item in inspect(engine).get_columns(table)}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_tag_views_system_name ON tag_views(system_name) WHERE system_name <> ''"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_import_artifacts_source_sha256 ON import_artifacts(source_type, sha256)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_tag_audits_idempotency_key ON tag_audits(idempotency_key) WHERE idempotency_key IS NOT NULL"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_candidate_actions_idempotency_key ON candidate_action_logs(idempotency_key) WHERE idempotency_key IS NOT NULL"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_view_tags_view_system_name ON view_tags(view_id, system_name) WHERE system_name <> ''"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_bills_tag_state_category_page ON bills(json_extract(tag_state_json, '$.category'), occurred_at DESC, id DESC)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_bills_tag_state_scenario_page ON bills(json_extract(tag_state_json, '$.scenario'), occurred_at DESC, id DESC)"))
    with SessionLocal() as session:
        # Preserve evidence of refund nature even when old allocations were revoked.
        for bill_id in session.scalars(select(RefundAllocation.refund_bill_id).distinct()).all():
            if session.scalar(select(RefundNatureAudit.id).where(RefundNatureAudit.bill_id == bill_id).limit(1)):
                continue
            if not session.get(RefundDesignation, bill_id):
                session.add(RefundDesignation(bill_id=bill_id, created_at=datetime.now()))
                session.add(RefundNatureAudit(bill_id=bill_id, action="refund", reason="迁移已有退款性质", actor="migration", created_at=datetime.now()))
        if not session.scalar(select(TagView.id).limit(1)):
            for name, tags in (("消费类别", ("餐饮", "交通", "住宿", "购物")), ("使用场景", ("日常", "计划", "意外"))):
                view = TagView(name=name, created_at=datetime.now())
                session.add(view)
                session.flush()
                session.add(ViewTag(view_id=view.id, name="未分类", is_unclassified=True))
                for tag_name in tags:
                    session.add(ViewTag(view_id=view.id, name=tag_name))
            session.flush()
        default_view_names = ("category", "scenario")
        default_tag_names = {
            "category": ("food", "transport", "lodging", "shopping"),
            "scenario": ("daily", "planned", "unexpected"),
        }
        for position, view in enumerate(session.scalars(select(TagView).order_by(TagView.id)).all()):
            if not view.system_name:
                view.system_name = default_view_names[position] if position < len(default_view_names) else f"view_{view.id}"
            numbered = 0
            tags_for_view = session.scalars(select(ViewTag).where(ViewTag.view_id == view.id).order_by(ViewTag.is_unclassified.desc(), ViewTag.id)).all()
            for view_tag in tags_for_view:
                if view_tag.is_unclassified:
                    view_tag.system_name = "unclassified"
                elif not view_tag.system_name:
                    defaults = default_tag_names.get(view.system_name, ())
                    view_tag.system_name = defaults[numbered] if numbered < len(defaults) else f"tag_{view_tag.id}"
                    numbered += 1
        session.flush()
        # Legacy `tags` / `bill_tags` are retained for compatibility only.  The
        # JSON-state migration deliberately never backfills or mutates them.
        active_views = session.scalars(select(TagView).where(TagView.archived.is_(False)).order_by(TagView.id)).all()
        default_state = {
            view.system_name: session.scalar(
                select(ViewTag.system_name).where(ViewTag.view_id == view.id, ViewTag.is_unclassified.is_(True))
            )
            for view in active_views
        }
        default_state = {key: value for key, value in default_state.items() if value}
        for bill in session.scalars(select(Bill)).all():
            try:
                state = json.loads(bill.tag_state_json or "{}")
            except (json.JSONDecodeError, TypeError):
                state = None
            if not isinstance(state, dict) or not state:
                had_legacy_assignment = bool(bill.tags) or bool(
                    session.scalar(select(BillViewTag.id).where(BillViewTag.bill_id == bill.id).limit(1))
                )
                bill.tag_state_json = json.dumps(default_state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if had_legacy_assignment:
                    session.add(TagAudit(
                        bill_id=bill.id,
                        category=bill.category,
                        tags="",
                        tag_state_json=bill.tag_state_json,
                        strategy="migration",
                        confidence=1.0,
                        provider="legacy_tag_state",
                        superseded=False,
                        created_at=datetime.now(),
                    ))
        pending_transfers = session.scalars(select(ReviewCandidate).where(ReviewCandidate.candidate_type == "transfer", ReviewCandidate.status == "pending")).all()
        for candidate in pending_transfers:
            first, second = session.get(Bill, candidate.bill_id), session.get(Bill, candidate.related_bill_id)
            if not first or not second or first.account_name == "未提供账户" or second.account_name == "未提供账户" or first.account_name == second.account_name:
                candidate.status = "evidence_insufficient"
                candidate.resolved_at = datetime.now()
        legacy_confirmed = session.scalars(select(ReviewCandidate).where(ReviewCandidate.status == "confirmed")).all()
        for candidate in legacy_confirmed:
            if candidate.candidate_type == "transfer":
                candidate.status = "legacy_transfer_excluded"
                candidate.transfer_group_id = f"legacy-transfer-{candidate.id}"
                for bill_id in (candidate.bill_id, candidate.related_bill_id):
                    bill = session.get(Bill, bill_id)
                    if bill:
                        bill.transfer_group_id = candidate.transfer_group_id
                        bill.aggregate_excluded = True
            else:
                candidate.status = "legacy_duplicate_needs_review"
            candidate.resolved_at = candidate.resolved_at or datetime.now()
        session.commit()
