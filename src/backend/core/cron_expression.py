"""Pure validation for persisted PAAM CRON expressions."""

import re
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger


APPLICATION_TIMEZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_NAME = r"(?:mon|tue|wed|thu|fri|sat|sun)"
_DAY_OF_WEEK = re.compile(
    rf"^(?:\*|{_DAY_NAME}(?:-{_DAY_NAME})?(?:,{_DAY_NAME}(?:-{_DAY_NAME})?)*)$",
    re.IGNORECASE,
)


def validate_cron_expression(value: str) -> str:
    """Return a normalized five- or six-field expression, or raise ValueError."""

    normalized = " ".join(value.split())
    parts = normalized.split(" ") if normalized else []
    if len(parts) not in {5, 6}:
        raise ValueError("cron must contain five or six fields")
    day_of_week = parts[-1]
    if _DAY_OF_WEEK.fullmatch(day_of_week) is None:
        raise ValueError("cron day-of-week must use English names or *")
    try:
        if len(parts) == 5:
            CronTrigger.from_crontab(normalized, timezone=APPLICATION_TIMEZONE)
        else:
            second, minute, hour, day, month, weekday = parts
            CronTrigger(
                second=second,
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=weekday,
                timezone=APPLICATION_TIMEZONE,
            )
    except (TypeError, ValueError) as error:
        raise ValueError("cron expression is invalid") from error
    return normalized
