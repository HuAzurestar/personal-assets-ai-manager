"""CRON strings must have one unambiguous weekday meaning across API and scheduler."""

from datetime import datetime

import pytest

from backend.core.cron_expression import (
    APPLICATION_TIMEZONE,
    cron_trigger,
    validate_cron_expression,
)


@pytest.mark.parametrize(("expression", "normalized"), [
    ("  0   9 * * 1-5  ", "0 9 * * mon,tue,wed,thu,fri"),
    ("0 9 * * 0", "0 9 * * sun"),
    ("0 9 * * 7", "0 9 * * sun"),
    ("0 9 * * 7/2", "0 9 * * sun"),
    ("0 9 * * sun-mon", "0 9 * * sun,mon"),
    ("0 9 * * 0,2,4", "0 9 * * sun,tue,thu"),
    ("0 9 * * */2", "0 9 * * sun,tue,thu,sat"),
    ("0 9 * * 1-5/2", "0 9 * * mon,wed,fri"),
    ("0 9 * * mon-fri/2", "0 9 * * mon,wed,fri"),
    ("0 9 * * mon-fri", "0 9 * * mon-fri"),
    ("15 30 9 * * 1", "15 30 9 * * mon"),
])
def test_weekday_syntax_normalizes_to_unambiguous_names(expression, normalized):
    assert validate_cron_expression(expression) == normalized


@pytest.mark.parametrize(("expression", "expected"), [
    ("0 9 * * 0", (2026, 10, 4, 9, 0, 0)),
    ("0 9 * * 7", (2026, 10, 4, 9, 0, 0)),
    ("0 9 * * 1", (2026, 9, 28, 9, 0, 0)),
    ("0 9 * * 1-5", (2026, 9, 28, 9, 0, 0)),
    ("0 9 * * */2", (2026, 9, 29, 9, 0, 0)),
    ("15 30 9 * * 1-5", (2026, 9, 28, 9, 30, 15)),
])
def test_next_run_uses_traditional_weekday_numbers(expression, expected):
    now = datetime(2026, 9, 28, 0, 0, tzinfo=APPLICATION_TIMEZONE)
    next_run = cron_trigger(expression).get_next_fire_time(None, now)
    assert next_run is not None
    assert (*next_run.timetuple()[:6],) == expected
    assert next_run.utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("expression", [
    "0 9 * * 8", "0 9 * * ?", "0 9 * * 5-1", "0 9 * * */0",
    "0 9 * * 1-5/0", "0 9 * * mon#2", "@daily", "0 9 * * * * *",
])
def test_invalid_weekday_or_field_count_is_rejected(expression):
    with pytest.raises(ValueError):
        validate_cron_expression(expression)


def test_day_and_weekday_constraints_must_both_match():
    now = datetime(2026, 9, 28, 0, 0, tzinfo=APPLICATION_TIMEZONE)
    next_run = cron_trigger("0 9 1 * 1").get_next_fire_time(None, now)
    assert next_run is not None
    assert next_run.day == 1 and next_run.weekday() == 0
