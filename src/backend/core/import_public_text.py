"""Public source projections, separate from immutable private evidence."""
import re


def masked_reference(value):
    return "" if not value else "****" + value[-4:] if len(value) > 4 else "****"


def masked_summary(value):
    # An email can only start at the beginning of its local-part character
    # run. Retrying the same greedy run at every character is quadratic for
    # long non-email source text and cannot be interrupted by SQLite's budget.
    value = re.sub(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[\w-]+", "[已脱敏]", value)
    return re.sub(r"[0-9]{7,}", lambda match: masked_reference(match.group()), value)


def public_issue(code):
    # Legacy parser messages may embed arbitrary source data. The public error
    # projection is a stable code, not a copy of those private exception texts.
    return code or ""
