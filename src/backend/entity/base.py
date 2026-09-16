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
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="milliseconds") + "Z"

    def process_result_value(self, value: str | datetime | None, dialect):
        del dialect
        if value is None or isinstance(value, datetime):
            return value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed


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
