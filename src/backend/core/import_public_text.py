"""Public source projections, separate from immutable private evidence."""
import re


def masked_reference(value):
    return "" if not value else "****" + value[-4:] if len(value) > 4 else "****"


def masked_summary(value):
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[\w-]+", "[已脱敏]", value)
    return re.sub(r"[0-9]{7,}", lambda match: masked_reference(match.group()), value)


def public_issue(code):
    # Legacy parser messages may embed arbitrary source data. The public error
    # projection is a stable code, not a copy of those private exception texts.
    return code or ""
