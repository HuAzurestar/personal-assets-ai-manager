"""Shared SQL pagination/seek primitives; never SQL LIKE or accumulated query caps."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import monotonic
from urllib.parse import quote
import hashlib
import json

from sqlalchemy import and_, or_, not_, select, func
from sqlalchemy.exc import OperationalError
from backend.error import ListQueryError, TargetEconomicError
from backend.schema.list_query import FilterFieldExpression
from backend.schema.bounded_search import normal_text
from backend.entity.base import UTCISO8601DateTime

_query_progress = ContextVar("bounded_query_progress", default=None)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=lambda item: item.isoformat())


def cursor_error():
    raise ListQueryError("invalid cursor or changed scan conditions", code="LIST_CURSOR_INVALID")


@contextmanager
def query_budget(db, seconds=30, code="QUERY_BUSY"):
    connection = db.connection()
    driver = connection.connection.driver_connection
    start = monotonic()
    previous = _query_progress.get()
    def progress():
        expired = monotonic() - start > seconds
        return int(expired or (previous is not None and previous[0] is driver and previous[1]()))
    context_token = _query_progress.set((driver, progress))
    driver.set_progress_handler(progress, 1000)
    try:
        yield
        if monotonic() - start > seconds:
            raise TargetEconomicError(503, "read budget exceeded", code=code)
    except OperationalError as error:
        raise TargetEconomicError(503, "read could not finish within budget", code=code) from error
    finally:
        _query_progress.reset(context_token)
        driver.set_progress_handler(previous[1] if previous and previous[0] is driver else None,
                                    1000 if previous and previous[0] is driver else 0)


def filter_predicate(expression, columns):
    if expression is None:
        return True
    if isinstance(expression, FilterFieldExpression):
        column = columns[expression.key]
        value = expression.val
        if expression.op == "between":
            return and_(column >= value["start"], column < value["end"])
        return {"=": lambda: column == value, "!=": lambda: column != value,
                ">": lambda: column > value, ">=": lambda: column >= value,
                "<": lambda: column < value, "<=": lambda: column <= value}[expression.op]()
    children = [filter_predicate(child, columns) for child in expression.expression]
    return {"AND": and_, "OR": or_, "NOT": lambda child: not_(child)}[expression.op](*children)


def effective_sort(request, columns, default=(("id", "asc"),)):
    sorts = [(item.key, item.direction) for item in request.sorter] or list(default)
    if len({key for key, _ in sorts}) != len(sorts):
        raise ListQueryError("duplicate sort key", code="LIST_SORTER_INVALID")
    if not any(key == "id" for key, _ in sorts):
        sorts.append(("id", "asc"))
    return sorts, [getattr(columns[key], direction)() for key, direction in sorts]


def page_rows(db, statement, request, columns, *, condition=True, default=(("id", "asc"),)):
    predicate = and_(condition, filter_predicate(request.filter, columns))
    sorts, order = effective_sort(request, columns, default)
    bounded = statement.where(predicate)
    total = db.scalar(select(func.count()).select_from(bounded.order_by(None).subquery()))
    rows = db.execute(bounded.order_by(*order).offset((request.page_index - 1) * request.page_size)
                      .limit(request.page_size)).mappings().all()
    return [dict(row) for row in rows], total


def scan_rows(db, statement, request, columns, *, scope, condition=True, default=(("id", "asc"),)):
    start = monotonic()
    sorts, order = effective_sort(request, columns, default)
    condition_hash = hashlib.sha256(canonical(dict(scope=scope,
        query=[item.model_dump() for item in request.query], filter=request.filter.model_dump() if request.filter else None,
        sorter=sorts, page_size=request.page_size)).encode()).hexdigest()
    predicate = and_(condition, filter_predicate(request.filter, columns))
    if request.cursor is not None:
        try:
            if len(quote(request.cursor, safe="").encode()) > 4096:
                cursor_error()
            cursor = json.loads(request.cursor, parse_constant=lambda _: cursor_error())
            if (not isinstance(cursor, dict) or set(cursor) != {"sort_values", "last_id", "condition_hash"}
                or cursor["condition_hash"] != condition_hash or type(cursor["last_id"]) is not int
                or not 0 < cursor["last_id"] <= 2**63 - 1 or not isinstance(cursor["sort_values"], list)
                or len(cursor["sort_values"]) != len(sorts)):
                cursor_error()
            values = []
            for (key, _), value in zip(sorts, cursor["sort_values"]):
                expected = datetime if isinstance(columns[key].type, UTCISO8601DateTime) else columns[key].type.python_type
                if expected is datetime:
                    if not isinstance(value, str):
                        cursor_error()
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if value.tzinfo is None or value.utcoffset() is None:
                        cursor_error()
                    value = value.astimezone(timezone.utc)
                elif expected is int:
                    if type(value) is not int or not -(2**63) <= value < 2**63:
                        cursor_error()
                elif type(value) is not expected:
                    cursor_error()
                if key == "id" and value != cursor["last_id"]:
                    cursor_error()
                values.append(value)
            seeks = []
            for index, (key, direction) in enumerate(sorts):
                equality = [columns[prior_key] == values[prior] for prior, (prior_key, _) in enumerate(sorts[:index])]
                seeks.append(and_(*equality, columns[key] > values[index] if direction == "asc" else columns[key] < values[index]))
            predicate = and_(predicate, or_(*seeks))
        except (ValueError, TypeError, KeyError, OverflowError):
            cursor_error()
    candidates = [dict(row) for row in db.execute(statement.where(predicate).order_by(*order)
                                                .limit(request.page_size)).mappings()]
    items = [row for row in candidates if all(expression.word in normal_text(str(row.get(expression.key) or ""))
             for expression in request.query)]
    has_more = len(candidates) == request.page_size
    next_cursor = None
    if has_more:
        last = candidates[-1]
        next_cursor = canonical(dict(sort_values=[last[key] for key, _ in sorts], last_id=last["id"], condition_hash=condition_hash))
        if len(quote(next_cursor, safe="").encode()) > 4096:
            cursor_error()
    return dict(items=items, total=None, page_size=request.page_size, next_cursor=next_cursor,
                has_more=has_more, scanned_count=len(candidates), elapsed_ms=round((monotonic() - start) * 1000, 3))
