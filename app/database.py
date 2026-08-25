from __future__ import annotations

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
    category: Mapped[str] = mapped_column(String(80), default="未分类")
    tags: Mapped[str] = mapped_column(String(500), default="")
    account_name: Mapped[str] = mapped_column(String(120), default="未提供账户")
    aggregate_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class TagView(Base):
    __tablename__ = "tag_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False))


class ViewTag(Base):
    __tablename__ = "view_tags"
    __table_args__ = (UniqueConstraint("view_id", "name", name="uq_view_tag_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    view_id: Mapped[int] = mapped_column(ForeignKey("tag_views.id"))
    name: Mapped[str] = mapped_column(String(120))
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


class TagAudit(Base):
    __tablename__ = "tag_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"))
    category: Mapped[str] = mapped_column(String(80))
    tags: Mapped[str] = mapped_column(String(500), default="")
    strategy: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(80), default="")
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
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
    transfer_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retained_bill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)
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
            "import_batches": {"batch_token": "VARCHAR(64)"},
            "bills": {
                "account_name": "VARCHAR(120) NOT NULL DEFAULT '未提供账户'",
                "aggregate_excluded": "BOOLEAN NOT NULL DEFAULT 0",
                "transfer_group_id": "VARCHAR(64)",
                "duplicate_of_id": "INTEGER",
            },
            "review_candidates": {
                "transfer_group_id": "VARCHAR(64)",
                "retained_bill_id": "INTEGER",
                "resolved_at": "DATETIME",
            },
        }
        with engine.begin() as connection:
            for table, columns in migrations.items():
                existing = {item["name"] for item in inspect(engine).get_columns(table)}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    with SessionLocal() as session:
        if not session.scalar(select(TagView.id).limit(1)):
            for name, tags in (("消费类别", ("餐饮", "交通", "住宿", "购物")), ("使用场景", ("日常", "计划", "意外"))):
                view = TagView(name=name, created_at=datetime.now())
                session.add(view)
                session.flush()
                session.add(ViewTag(view_id=view.id, name="未分类", is_unclassified=True))
                for tag_name in tags:
                    session.add(ViewTag(view_id=view.id, name=tag_name))
        for bill in session.scalars(select(Bill)).all():
            if not bill.tags or session.scalar(select(BillTag).where(BillTag.bill_id == bill.id)):
                continue
            for name in dict.fromkeys(tag.strip() for tag in bill.tags.split(",") if tag.strip()):
                tag = session.scalar(select(Tag).where(Tag.name == name))
                if not tag:
                    tag = Tag(name=name)
                    session.add(tag)
                    session.flush()
                session.add(BillTag(bill_id=bill.id, tag_id=tag.id))
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
