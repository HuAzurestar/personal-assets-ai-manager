"""Shared HTTP parsing only; object capabilities remain in each Service."""
from fastapi import Query, Request
from backend.router.dependency import validate_query_parameter_names
from backend.schema.list_query import parse_list_request
from backend.schema.bounded_search import parse_search_request


def bounded_list_dependency(request_type):
    def parse(http_request: Request, page_index: int = Query(1, ge=1),
              page_size: int = Query(20, ge=1, le=100), filter: str | None = None, sorter: str | None = None):
        validate_query_parameter_names(http_request, {"page_index", "page_size", "filter", "sorter"})
        return parse_list_request(request_type, page_index=page_index, page_size=page_size, filter=filter, sorter=sorter)
    return parse


def bounded_search_dependency(request_type):
    def parse(http_request: Request, page_size: int = Query(20, ge=1, le=100),
              query: str | None = None, filter: str | None = None, sorter: str | None = None, cursor: str | None = None):
        validate_query_parameter_names(http_request, {"page_size", "query", "filter", "sorter", "cursor"})
        return parse_search_request(request_type=request_type, page_size=page_size, query=query,
                                    filter=filter, sorter=sorter, cursor=cursor)
    return parse
