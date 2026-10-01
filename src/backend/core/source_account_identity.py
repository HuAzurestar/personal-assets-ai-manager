"""Parser-owned source identity policy, independent of orders or UI guesses."""
import hashlib
import json
import re

ALLOWED_NAMESPACES = {"abc:statement-v1", "ccb:statement-v1", "cmb:statement-v1"}


def reliable_source(row):
    source = row.get("source_account", {})
    namespace, identity = source.get("source_namespace", ""), source.get("source_identity", "")
    if (source.get("identity_strength") == "RELIABLE" and namespace in ALLOWED_NAMESPACES
        and namespace == f"{row.get('source_type')}:statement-v1"
        and re.fullmatch(r"[0-9]{10,30}", identity)):
        return namespace, identity
    return None


def binding_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=lambda item: item.isoformat()).encode()).hexdigest()
