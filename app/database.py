from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
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


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


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
    status: Mapped[str] = mapped_column(String(20), default="pending")
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
        columns = {item["name"] for item in inspect(engine).get_columns("import_batches")}
        if "batch_token" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE import_batches ADD COLUMN batch_token VARCHAR(64)"))
    with SessionLocal() as session:
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
        session.commit()
