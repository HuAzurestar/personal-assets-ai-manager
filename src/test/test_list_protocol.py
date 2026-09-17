import json

import pytest
from pydantic import ValidationError

from backend.error import ListQueryError
from backend.schema.list_query import (
    FilterFieldExpression,
    FilterLogicalExpression,
    ListRequest,
    parse_list_request,
)
from backend.schema.response import (
    ErrorBody,
    ErrorResponse,
    ListBody,
    ListResponse,
    ResponseWarning,
)


def test_minimal_list_request_defaults_optional_capabilities():
    request = parse_list_request(
        ListRequest,
        page_index=2,
        page_size=50,
    )

    assert request.page_index == 2
    assert request.page_size == 50
    assert request.query == []
    assert request.filter is None
    assert request.sorter == []


def test_list_request_parses_recursive_filter_and_ordered_sorter():
    request = parse_list_request(
        ListRequest,
        filter=json.dumps({
            "op": "AND",
            "expression": [
                {"key": "occurred_time", "op": ">=", "val": "2026-09-01T00:00:00+08:00"},
                {"key": "occurred_time", "op": "<", "val": "2026-10-01T00:00:00+08:00"},
            ],
        }),
        sorter=json.dumps([
            {"key": "occurred_time", "direction": "desc"},
            {"key": "id", "direction": "asc"},
        ]),
    )

    assert isinstance(request.filter, FilterLogicalExpression)
    assert all(
        isinstance(expression, FilterFieldExpression)
        for expression in request.filter.expression
    )
    assert [item.key for item in request.sorter] == ["occurred_time", "id"]


@pytest.mark.parametrize(
    ("parameter", "value", "code"),
    [
        ("query", "{", "LIST_QUERY_INVALID"),
        ("filter", "[]", "LIST_FILTER_INVALID"),
        ("sorter", "{}", "LIST_SORTER_INVALID"),
    ],
)
def test_list_request_reports_component_parse_codes(parameter, value, code):
    with pytest.raises(ListQueryError) as raised:
        parse_list_request(ListRequest, **{parameter: value})

    assert raised.value.status_code == 422
    assert raised.value.code == code
    assert raised.value.details["component"] == parameter


def test_list_request_reports_operator_direction_and_value_codes():
    with pytest.raises(ListQueryError) as operator:
        parse_list_request(
            ListRequest,
            filter='{"key":"id","op":"LIKE","val":"1"}',
        )
    assert operator.value.code == "LIST_FILTER_OPERATOR_NOT_SUPPORTED"

    with pytest.raises(ListQueryError) as direction:
        parse_list_request(
            ListRequest,
            sorter='[{"key":"id","direction":"sideways"}]',
        )
    assert direction.value.code == "LIST_SORTER_DIRECTION_NOT_SUPPORTED"

    with pytest.raises(ListQueryError) as between:
        parse_list_request(
            ListRequest,
            filter='{"key":"occurred_time","op":"between","val":{"start":1}}',
        )
    assert between.value.code == "LIST_FILTER_VALUE_INVALID"


def test_response_hierarchy_omits_empty_warnings_and_error_details():
    success = ListResponse[int](
        status=200,
        message="ok",
        body=ListBody[int](
            items=[1, 2],
            total=2,
            page_index=1,
            page_size=20,
        ),
    )
    assert success.model_dump() == {
        "status": 200,
        "message": "ok",
        "body": {
            "items": [1, 2],
            "total": 2,
            "page_index": 1,
            "page_size": 20,
        },
    }

    warned = success.model_copy(update={
        "warnings": [ResponseWarning(
            code="LIST_AMOUNT_SORT_GROUPED",
            message="Amounts are grouped before sorting",
            details={"order": ["currency_code asc", "amount_value desc"]},
        )],
    })
    assert warned.model_dump()["warnings"][0]["code"] == "LIST_AMOUNT_SORT_GROUPED"

    error = ErrorResponse(
        status=422,
        message="Invalid filter",
        body=ErrorBody(code="LIST_FILTER_INVALID"),
    )
    assert error.model_dump() == {
        "status": 422,
        "message": "Invalid filter",
        "body": {"code": "LIST_FILTER_INVALID"},
    }
    with pytest.raises(ValidationError):
        ErrorResponse(
            status=200,
            message="not an error",
            body=ErrorBody(code="INVALID"),
        )
