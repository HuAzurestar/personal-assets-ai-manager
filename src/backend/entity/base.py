from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCISO8601DateTime(TypeDecorator[datetime]):
    """Persist UTC timestamps as ISO-8601 text while exposing datetime values."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC timestamp must include timezone information")
        value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def process_result_value(self, value: str | datetime | None, dialect):
        del dialect
        if value is None:
            return value
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Stored UTC timestamp is missing timezone information")
        return parsed.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TargetTable:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_time: Mapped[datetime] = mapped_column(
        UTCISO8601DateTime(),
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )
    updated_time: Mapped[datetime] = mapped_column(
        UTCISO8601DateTime(),
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )
