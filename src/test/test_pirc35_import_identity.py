import copy
import hashlib
import json
import pytest
from pydantic import ValidationError
from backend.core.import_identity import fact_key, fact_values, same_fact, raw_evidence
from backend.schema.import_command import ImportConfirmInput, ImportReviseInput


def row(**changes):
    return dict(source_type="ccb", account=dict(number="0000000000123456"), reference="Mock source transaction",
                row_number=5, profile="Mock name", occurred_at="2024-01-01T00:00:00Z", amount_minor=-40000,
                currency="CNY", merchant="Mock merchant", note="Mock source", disposition="posted",
                raw={"Mock raw": "entirely fictional"}) | changes


def test_exact_documented_identity_json_preserves_leading_zeroes_and_source_scope():
    current = row()
    expected = dict(namespace="source-reference-v1", source_type=201, account_code="0000000000123456",
                    source_reference="Mock source transaction")
    encoded = json.dumps(expected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    assert fact_key(current, "mock-file") == hashlib.sha256(encoded).hexdigest()
    assert fact_key(current, "mock-other-file") == fact_key(current, "mock-file")
    changed = copy.deepcopy(current)
    changed["account"]["number"] = "0000000000654321"
    assert fact_key(changed, "mock-file") != fact_key(current, "mock-file")
    assert fact_key(row(source_type="abc"), "mock-file") != fact_key(current, "mock-file")


def test_keyless_identical_source_rows_remain_distinct_and_same_file_row_stable():
    current = row(reference="")
    assert fact_key(current, "mock-file") != fact_key(row(reference="", row_number=6), "mock-file")
    assert fact_key(current, "mock-file") != fact_key(current, "mock-other-file")
    assert fact_key(current, "mock-file") == fact_key(copy.deepcopy(current), "mock-file")


def test_core_compare_is_exact_time_currency_direction_amount_and_own_source():
    accepted = fact_values(row(), "mock-file")
    assert accepted["account_code"] == "0000000000123456"
    assert same_fact(accepted, accepted | dict(fact_key="unchanged legacy key"))
    for key, value in [("amount", 39999), ("cash_direction", 1), ("currency_code", "CNY_2"),
                       ("account_code", "0000000000654321"),
                       ("occurred_time", fact_values(row(occurred_at="2024-01-01T00:00:01Z"), "mock-file")["occurred_time"])]:
        assert not same_fact(accepted, accepted | {key: value})


def test_raw_evidence_has_no_choice_or_receipt_and_one_mib_limit():
    raw_hash, payload = raw_evidence(row(choice="ACCEPT", preview_digest="mock receipt"), dict(parser_version=1, source_timezone="UTC"))
    assert len(raw_hash) == 64
    assert "selected_rows" not in payload and "preview_digest" not in payload and "choice" not in payload
    with pytest.raises(ValueError, match="DETAIL_LIMIT"):
        raw_evidence(row(raw={"Mock raw": "x" * (1024 * 1024)}), {})


def test_bounded_choices_aware_tokens_and_no_legacy_version():
    payload = dict(expected_updated_time="2024-01-01T00:00:00Z", preview_digest="0" * 64,
                   selected_rows=[dict(file_id=1, source_row_number=5)])
    assert ImportConfirmInput(**payload).selected_rows[0].file_id == 1
    for extra in [dict(version="old"), dict(selected_rows=payload["selected_rows"] * 1001),
                  dict(selected_rows=[dict(file_id=True, source_row_number=5)]),
                  dict(expected_updated_time="2024-01-01T00:00:00")]:
        with pytest.raises(ValidationError):
            ImportConfirmInput(**(payload | extra))
    with pytest.raises(ValidationError):
        ImportReviseInput(expected_updated_time="2024-01-01T00:00:00Z", choices=[
            dict(file_id=1, source_row_number=5, decision="ACCEPT"), dict(file_id=1, source_row_number=5, decision="SKIP")])
