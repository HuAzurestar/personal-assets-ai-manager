"""Cross-request candidate scans: literal text, strict seek cursor, unknown total."""
import unicodedata
import json
from pydantic import Field, model_validator
from backend.schema.list_query import ListRequest, parse_list_request
from backend.error import ListQueryError


def normal_text(value):
    return unicodedata.normalize("NFC", value).casefold()


class SearchRequest(ListRequest):
    cursor: str | None = None

    @model_validator(mode="after")
    def bounded_words(self):
        if len(self.query) > 8:
            raise ListQueryError("at most eight text terms", code="LIST_QUERY_INVALID")
        for expression in self.query:
            expression.word = normal_text(expression.word)
            if not expression.word.strip() or len(expression.word) > 128:
                raise ListQueryError("normalized text term must contain 1..128 characters", code="LIST_QUERY_INVALID")
        return self


def parse_search_request(*, page_size=20, query=None, filter=None, sorter=None, cursor=None):
    if query is not None:
        try:
            if len(query.encode()) > 16384:
                raise ValueError("query too large")
            terms = json.loads(query)
            if not isinstance(terms, list) or len(terms) > 8:
                raise ValueError("invalid terms")
            for term in terms:
                if not isinstance(term, dict) or not isinstance(term.get("word"), str):
                    raise ValueError("invalid term")
                term["word"] = normal_text(term["word"])
            query = json.dumps(terms, ensure_ascii=False)
        except (TypeError, ValueError):
            raise ListQueryError("invalid text query", code="LIST_QUERY_INVALID")
    parsed = parse_list_request(SearchRequest, page_size=page_size, query=query, filter=filter, sorter=sorter)
    parsed.cursor = cursor
    return parsed
