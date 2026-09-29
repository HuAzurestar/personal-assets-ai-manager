"""Pure validation for persisted PAAM CRON expressions."""

import re
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

APPLICATION_TIMEZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_NAME = r"(?:mon|tue|wed|thu|fri|sat|sun)"
_NAMED_DAY_OF_WEEK = re.compile(
    rf"^(?:\*|{_DAY_NAME}(?:-{_DAY_NAME})?(?:,{_DAY_NAME}(?:-{_DAY_NAME})?)*)$",
    re.IGNORECASE,
)
_DAY_PART = re.compile(
    rf"(?P<start>\*|[0-7]|{_DAY_NAME})(?:-(?P<end>[0-7]|{_DAY_NAME}))?"
    r"(?:/(?P<step>[1-9][0-9]*))?",
    re.IGNORECASE,
)
_SUNDAY_FIRST = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _normalize_weekday(value: str) -> str:
    """Map conventional crontab 0/7=Sunday to unambiguous names for APScheduler 3."""

    if _NAMED_DAY_OF_WEEK.fullmatch(value) and not any(
        part.casefold().startswith("sun-") for part in value.split(",")
    ):
        return value
    selected: list[int] = []
    for part in value.split(","):
        match = _DAY_PART.fullmatch(part)
        if match is None:
            raise ValueError("cron weekday must use 0-7 or mon-sun with lists, ranges, or steps")
        start, end, step = match.group("start", "end", "step")
        if start == "*":
            if end is not None:
                raise ValueError("cron weekday wildcard cannot be a range endpoint")
            first, last = 0, 6
        else:
            first = _weekday_number(start)
            if end is not None:
                last = _weekday_number(end)
                if end.casefold() == "sun" and first > 0:
                    last = 7
            elif step is not None:
                last = 7 if first == 7 else 6
            else:
                last = first
        if first > last:
            raise ValueError("cron weekday range must run forward from Sunday to Saturday")
        for number in range(first, last + 1, int(step or 1)):
            day = number % 7
            if day not in selected:
                selected.append(day)
    return "*" if len(selected) == 7 else ",".join(_SUNDAY_FIRST[day] for day in selected)


def _weekday_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    return _SUNDAY_FIRST.index(value.casefold())


def validate_cron_expression(value: str) -> str:
    """Return a normalized five- or six-field expression, or raise ValueError."""

    normalized = " ".join(value.split())
    parts = normalized.split(" ") if normalized else []
    if len(parts) not in {5, 6}:
        raise ValueError("cron must contain five or six fields")
    parts[-1] = _normalize_weekday(parts[-1])
    normalized = " ".join(parts)
    try:
        cron_trigger(normalized)
    except (TypeError, ValueError) as error:
        raise ValueError("cron expression is invalid") from error
    return normalized


def cron_trigger(value: str) -> CronTrigger:
    """Build the application-timezone trigger for a validated expression."""

    parts = value.split(" ")
    if len(parts) not in {5, 6}:
        raise ValueError("cron must contain five or six fields")
    parts[-1] = _normalize_weekday(parts[-1])
    if len(parts) == 5:
        return CronTrigger.from_crontab(" ".join(parts), timezone=APPLICATION_TIMEZONE)
    second, minute, hour, day, month, weekday = parts
    return CronTrigger(
        second=second,
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=weekday,
        timezone=APPLICATION_TIMEZONE,
    )
