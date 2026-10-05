"""Parser-owned source identity policy, independent of orders or UI guesses."""
import hashlib
import json
import re

ALLOWED_NAMESPACES = {f"{provider}:statement-v1" for provider in ("abc", "ccb", "cmb", "alipay", "wechat")}


def reliable_identity(provider, identity):
    """Shape guard for an identity from the statement's OWN account header.

    Payment methods, nicknames, order IDs and masked tails are not identities.
    Preserve the exact source string; no case/phone/alias fuzzy normalization.
    """
    if not isinstance(identity, str) or len(identity) > 256:
        return False
    if provider in {"abc", "ccb", "cmb"}:
        return re.fullmatch(r"[0-9]{10,30}", identity) is not None
    if provider == "alipay":
        return bool(re.fullmatch(r"1[3-9][0-9]{9}", identity) or
                    re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", identity))
    if provider == "wechat":
        return re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{5,31}", identity) is not None
    return False


def reliable_source(row):
    source = row.get("source_account", {})
    namespace, identity = source.get("source_namespace", ""), source.get("source_identity", "")
    if (source.get("identity_strength") == "RELIABLE" and namespace in ALLOWED_NAMESPACES
        and namespace == f"{row.get('source_type')}:statement-v1"
        and reliable_identity(row.get("source_type"), identity)):
        return namespace, identity
    return None


def binding_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=lambda item: item.isoformat()).encode()).hexdigest()
