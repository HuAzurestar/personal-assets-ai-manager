"""Verify explicit same/cross-source evidence pairs before publication.

Only immutable parser sources and accepted origin proofs are inputs. Ledger
cards, labels, amounts alone and user-selected account identities are not proof.
The returned ROW locators are command state, not guessed or persistent IDs.
"""
from collections import defaultdict
import re

from backend.core.import_identity import SOURCE_CODES, fact_values, same_fact
from backend.error import TargetIntakeError
from backend.schema.identifier import SQLITE_ID_MAX


def reject(code, status=422):
    raise TargetIntakeError(status, code, code=code)


def complete_source_code(source_type, account_code):
    """A masked string is evidence, never a complete source identity.

    Bank identities use the same full-digit boundary as parser-owned reliable
    identities. Wallet profiles retain their existing origin contract, but
    known redaction markers cannot grant manual linking/exclusion authority.
    AUTO source keys and persisted Facts are not rewritten by this check.
    """
    if not account_code or any(marker in account_code for marker in ("*","＊","•","…")):
        return False
    if source_type in {SOURCE_CODES[name] for name in ("abc","ccb","cmb")}:
        return re.fullmatch(r"[0-9]{10,30}",account_code) is not None
    return True


def target_locator(target):
    """Strict discriminated target, also usable by internal planning callers."""
    if not isinstance(target, dict):
        reject("INVALID_EVIDENCE_TARGET")
    if target.get("kind") == "FACT" and set(target) == {"kind", "transaction_id"}:
        value = target["transaction_id"]
        if type(value) is int and 0 < value <= SQLITE_ID_MAX:
            return "FACT", value
    if target.get("kind") == "ROW" and set(target) == {"kind", "file_id", "source_row_number"}:
        values = target["file_id"], target["source_row_number"]
        if all(type(value) is int and 0 < value <= SQLITE_ID_MAX for value in values):
            return "ROW", values
    reject("INVALID_EVIDENCE_TARGET")


def parsed_source(row, file):
    source_type = SOURCE_CODES.get(row.get("source_type"), 0)
    if not source_type or source_type != file["source_type"]:
        reject("SOURCE_IDENTITY_REQUIRED")
    try:
        values = fact_values(row, file["sha256"])
    except (ValueError, TypeError, KeyError):
        reject("INVALID_EVIDENCE_TARGET")
    if not complete_source_code(source_type,values["account_code"]):
        reject("SOURCE_IDENTITY_REQUIRED")
    return dict(values=values, source_type=source_type, origin_file_ids=[file["id"]])


def plan_same_source_links(rows, files, candidates, choices, targets):
    """Resolve selected pairs, never broadcast one target or split dependencies.

    ``targets`` are batch-loaded, proven accepted origins supplied by the
    Mapper. Every ROW anchor must be in this exact selected scope and be a new
    real AUTO/NEW row. Its canonical key deduplicates reliable local anchors.
    No SQL, mutation, financial output or consent is performed here.
    """
    result, per_file = {}, defaultdict(list)
    # Reliable keys can make several selected real rows one planned Fact.
    # Include every such file when checking the "another file" requirement.
    anchor_files = defaultdict(set)
    for key, candidate in candidates.items():
        choice = choices.get(key, {})
        if (choice.get("decision") == "ACCEPT" and choice.get("resolution", "AUTO") in {"AUTO", "NEW"}
                and candidate.get("classification") == "NEW" and not candidate.get("issue") and
                not candidate.get("fact_id") and candidate.get("values")):
            anchor_files[candidate["values"]["fact_key"]].add(key[0])
    for key, choice in choices.items():
        if choice.get("resolution", "AUTO") != "LINK_EXISTING":
            continue
        candidate = candidates.get(key)
        if key not in rows or candidate is None or choice.get("decision") != "ACCEPT":
            reject("INVALID_EVIDENCE_TARGET")
        if candidate.get("stored") and candidate["stored"]["row_status"] == 1:
            # Accepted source pointers are re-read, not re-decided by this plan.
            reject("ROWS_ALREADY_PROCESSED", 409)
        if choice.get("account_ref_id") is not None or rows[key].get("reference") or candidate.get("fact_id"):
            reject("INVALID_EVIDENCE_TARGET")
        if candidate.get("issue"):
            reject(candidate["issue"])
        current = parsed_source(rows[key], files[key[0]])
        kind, locator = target_locator(choice.get("target"))
        if kind == "FACT":
            target = targets.get(locator)
            if target is None:
                reject("INVALID_EVIDENCE_TARGET")
            if target.get("issue"):
                reject(target["issue"])
            identity = ("FACT", locator)
            premise = target["premise"]
            if key[0] not in target["origin_file_ids"] and any(
                    proof["file_id"] == key[0] for proof in premise["evidence"]):
                # A previously committed pair in this file is still ambiguity,
                # even if the next identical row is submitted in another batch.
                reject("IDENTITY_AMBIGUOUS")
        else:
            anchor, anchor_choice = candidates.get(locator), choices.get(locator, {})
            if (locator == key or locator not in rows or anchor is None or
                    anchor_choice.get("decision") != "ACCEPT" or
                    anchor_choice.get("resolution", "AUTO") not in {"AUTO", "NEW"} or
                    anchor_choice.get("target") is not None or anchor.get("issue") or
                    anchor.get("classification") != "NEW" or anchor.get("fact_id") or
                    anchor.get("stored") and anchor["stored"]["row_status"] == 1):
                reject("INVALID_EVIDENCE_TARGET")
            target = parsed_source(rows[locator], files[locator[0]])
            target["origin_file_ids"] = sorted(anchor_files[target["values"]["fact_key"]])
            identity = ("NEW", target["values"]["fact_key"])
            premise = dict(row=list(locator), source=target, choice=anchor_choice,
                           candidate_premise=anchor["premise"])
        if key[0] in target["origin_file_ids"]:
            reject("INVALID_EVIDENCE_TARGET")
        if current["source_type"] != target["source_type"]:
            reject("FACT_CONFLICT")
        if not same_fact(current["values"], target["values"]):
            reject("FACT_CONFLICT")
        per_file[(key[0], identity)].append(key)
        result[key] = dict(kind=kind, locator=locator, identity=identity, premise=premise)
    if any(len(keys) > 1 for keys in per_file.values()):
        reject("IDENTITY_AMBIGUOUS")
    return result


def plan_cross_source_duplicates(rows, files, candidates, choices, targets):
    """Prove new B/real A source pairs; financial final-set validation is shared.

    IDs here are accepted Fact IDs or symbolic NEW identities, never invented
    SQLite IDs. Every local anchor is selected and will retain real cash.
    """
    result, per_file, by_b = {}, defaultdict(set), {}
    for key, choice in choices.items():
        if choice.get("resolution") != "DUPLICATE":
            continue
        candidate = candidates.get(key)
        if candidate is None or key not in rows or choice.get("decision") != "ACCEPT":
            reject("INVALID_EVIDENCE_TARGET")
        if candidate.get("stored") and candidate["stored"]["row_status"] == 1:
            reject("ROWS_ALREADY_PROCESSED", 409)
        if candidate.get("issue"):
            reject(candidate["issue"])
        if candidate.get("fact_id") or candidate.get("classification") != "NEW":
            reject("INVALID_EVIDENCE_TARGET")
        source = parsed_source(rows[key], files[key[0]])
        kind, locator = target_locator(choice.get("target"))
        if kind == "FACT":
            target = targets.get(locator)
            if target is None:
                reject("INVALID_EVIDENCE_TARGET")
            if target.get("issue"):
                reject(target["issue"])
            identity, premise = ("FACT", locator), target["premise"]
        else:
            anchor, anchor_choice = candidates.get(locator), choices.get(locator, {})
            if (locator == key or locator not in rows or anchor is None or
                    anchor_choice.get("decision") != "ACCEPT" or
                    anchor_choice.get("resolution", "AUTO") not in {"AUTO", "NEW"} or
                    anchor_choice.get("target") is not None or anchor.get("issue") or
                    anchor.get("classification") != "NEW" or anchor.get("fact_id") or
                    anchor.get("stored") and anchor["stored"]["row_status"] == 1):
                reject("INVALID_EVIDENCE_TARGET")
            target = parsed_source(rows[locator], files[locator[0]])
            identity = ("NEW", target["values"]["fact_key"])
            premise = dict(row=list(locator), source=target, choice=anchor_choice,
                           candidate_premise=anchor["premise"])
        if (source["source_type"], source["values"]["account_code"]) == (
                target["source_type"], target["values"]["account_code"]):
            reject("INVALID_DUPLICATE")
        if any(source["values"][field] != target["values"][field]
               for field in ("occurred_time", "amount", "currency_code", "cash_direction")):
            reject("FACT_CONFLICT")
        excluded = ("NEW", source["values"]["fact_key"])
        if excluded in by_b and by_b[excluded] != identity:
            reject("IDENTITY_AMBIGUOUS")
        by_b[excluded] = identity
        per_file[(key[0], identity)].add(excluded)
        result[key] = dict(kind=kind, locator=locator, identity=identity, source=source, premise=premise)
    # A reliable canonical B cannot simultaneously be a real local anchor.
    for key, candidate in candidates.items():
        values = candidate.get("values")
        if (values and ("NEW", values["fact_key"]) in by_b and
                choices.get(key, {}).get("decision") == "ACCEPT" and key not in result):
            reject("INVALID_DUPLICATE")
    if any(len(excluded) > 1 for excluded in per_file.values()):
        reject("IDENTITY_AMBIGUOUS")
    return result
