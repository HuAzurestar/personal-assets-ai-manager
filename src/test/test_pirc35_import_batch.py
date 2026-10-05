"""Fictional source data only; selected batches against isolated SQLite."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from sqlalchemy import func, select, update, insert
from backend.core.import_identity import fact_values

from backend.core import target_database
from backend.core.import_identity import raw_evidence
from backend.entity import (TransactionFact, TransactionImportRow, TransactionImportFile,
                            ReviewCase, LedgerEntry, ReviewAllocation)
from backend.error import TargetIntakeError
from backend.mapper.import_batch_mapper import ImportBatchMapper


@pytest.fixture
def mapper():
    target_database.ensure_target_schema()
    with target_database.SessionLocal() as db:
        yield ImportBatchMapper(db)


def row(number=1, reference="Mock reference", **changes):
    return dict(source_type="ccb", account=dict(number="0000000000123456"), reference=reference,
        row_number=number, profile="Mock owner", occurred_at="2024-01-01T00:00:00Z", amount_minor=-40000,
        currency="CNY", merchant="Mock merchant", note="fictional", disposition="posted",
        raw={"mock original": str(number)}, source_account=dict(source_namespace="ccb:statement-v1",
        source_identity="0000000000123456", identity_strength="RELIABLE")) | changes


def prepare(mapper, rows, sha="a" * 64):
    mapper.begin_write()
    files = mapper.prepare_files("first-upload", [dict(sha256=sha, filename="mock.csv")])
    file = files[sha]
    mapper.persist_parse([dict(file_id=file["id"], sha256=sha, source_type="ccb", format="csv", rows=rows)])
    mapper.db.commit()
    mapper.end_write()
    return {(file["id"], value["row_number"]): deepcopy(value) for value in rows}


def accept(mapper, rows, choices=None, *, fault=None):
    choices = choices or {key: dict(decision="ACCEPT") for key in rows}
    mapper.begin_write()
    try:
        candidates = mapper.match(rows, choices)
        result = mapper.write_batch(candidates, choices, list(rows), fault=fault)
        mapper.db.commit()
        return result
    except Exception:
        mapper.db.rollback()
        raise
    finally:
        mapper.end_write()


def count(mapper, entity):
    return mapper.db.scalar(select(func.count()).select_from(entity))


def test_one_source_identity_many_evidence_rows_one_initial_chain_no_replay(mapper):
    rows = prepare(mapper, [row(1), row(2)])
    result = accept(mapper, rows)
    assert result["new_fact_count"] == 1
    assert len({item["transaction_id"] for item in result["processed_rows"]}) == 1
    assert len({item["created_review_id"] for item in result["processed_rows"]}) == 1
    assert count(mapper, TransactionFact) == count(mapper, ReviewCase) == count(mapper, LedgerEntry) == 1
    assert count(mapper, TransactionImportRow) == 2
    assert result["files"][0]["remaining"] == 0 and result["files"][0]["status"] == 1
    before = mapper.db.execute(select(TransactionImportRow.created_time).order_by(TransactionImportRow.id)).all()
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        accept(mapper, rows)
    assert mapper.db.execute(select(TransactionImportRow.created_time).order_by(TransactionImportRow.id)).all() == before


def test_1001_rows_two_batches_fault_keeps_first_and_no_cash_multiplication(mapper):
    rows = prepare(mapper, [row(n, reference="") for n in range(1, 1002)])
    first = dict(list(rows.items())[:1000])
    result = accept(mapper, first)
    assert result["new_fact_count"] == 1000
    assert result["files"][0]["status"] == 2 and result["remaining_count"] == 1
    second = dict(list(rows.items())[1000:])
    def fault(stage):
        if stage == "defaults":
            raise RuntimeError("mock fault")
    with pytest.raises(RuntimeError, match="mock fault"):
        accept(mapper, second, fault=fault)
    for entity in (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow):
        assert count(mapper, entity) == 1000
    assert mapper.progress([next(iter(rows))[0]])[0]["remaining"] == 1
    assert accept(mapper, second)["remaining_count"] == 0


def test_same_amount_keyless_same_text_and_other_sources_not_merged(mapper):
    original = row(1, reference="")
    other = original | dict(row_number=2)
    result = accept(mapper, prepare(mapper, [original, other]))
    assert result["new_fact_count"] == 2
    result = accept(mapper, prepare(mapper, [original], sha="b" * 64))
    assert result["new_fact_count"] == 1


def test_existing_fact_supplement_does_not_restore_or_create_default(mapper):
    first = accept(mapper, prepare(mapper, [row()]))
    review_id = first["processed_rows"][0]["created_review_id"]
    mapper.db.execute(update(ReviewCase).where(ReviewCase.id == review_id).values(status=1))
    mapper.db.commit()
    second = accept(mapper, prepare(mapper, [row(8)], sha="b" * 64))
    assert second["new_fact_count"] == 0 and second["linked_existing_count"] == 1
    assert second["processed_rows"][0]["created_review_id"] == 0
    assert count(mapper, ReviewCase) == count(mapper, LedgerEntry) == 1
    assert mapper.db.scalar(select(ReviewCase.status)) == 1


@pytest.mark.parametrize("changes", [dict(amount_minor=-30000), dict(occurred_at="2024-01-01T00:00:01Z"),
    dict(amount_minor=40000), dict(currency="KRW")])
def test_exact_core_conflict_is_invalid_never_supplemented(mapper, changes):
    accept(mapper, prepare(mapper, [row()]))
    second = prepare(mapper, [row(2, **changes)], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="FACT_CONFLICT"):
        accept(mapper, second)
    skipped = accept(mapper, second, {key: dict(decision="SKIP") for key in second})
    assert skipped["invalid_count"] == 1 and skipped["files"][0]["status"] == 3
    assert count(mapper, TransactionFact) == 1


def test_same_file_skip_recheck_keeps_raw_and_first_group(mapper):
    rows = prepare(mapper, [row()])
    skipped = accept(mapper, rows, {key: dict(decision="SKIP") for key in rows})
    assert skipped["skipped_count"] == 1 and skipped["files"][0]["status"] == 1
    original = dict(mapper.db.execute(select(TransactionImportRow.__table__)).mappings().one())
    with pytest.raises(TargetIntakeError, match="ROW_RECHECK_REQUIRED"):
        accept(mapper, rows)
    result = accept(mapper, rows, {key: dict(decision="ACCEPT", recheck=True) for key in rows})
    accepted = dict(mapper.db.execute(select(TransactionImportRow.__table__)).mappings().one())
    assert result["new_fact_count"] == 1 and accepted["row_status"] == 1
    for key in ("id", "created_time", "raw_hash", "raw_payload", "source_reference"):
        assert original[key] == accepted[key]
    assert original["updated_time"] < accepted["updated_time"]
    files = prepare(mapper, [row()])
    assert next(iter(files))[0] == original["transaction_import_file_id"]
    assert mapper.db.scalar(select(TransactionImportFile.batch_code)) == "first-upload"
    assert mapper.db.scalar(select(TransactionImportFile.success_count)) == 1


def test_invalid_original_cannot_be_edited_to_create_a_fact(mapper):
    rows = prepare(mapper, [row(error="Mock parse error")])
    accept(mapper, rows, {key: dict(decision="SKIP") for key in rows})
    changed = {key: value | dict(amount_minor=-30000) for key, value in rows.items()}
    with pytest.raises(TargetIntakeError, match="IDENTITY_CHANGED"):
        accept(mapper, changed, {key: dict(decision="ACCEPT", recheck=True) for key in rows})
    assert count(mapper, TransactionFact) == 0


def test_same_reference_different_own_account_is_not_same_source_identity(mapper):
    accept(mapper, prepare(mapper, [row()]))
    changed = row(account=dict(number="0000000000654321"), source_account=dict(source_namespace="ccb:statement-v1",
        source_identity="0000000000654321", identity_strength="RELIABLE"))
    assert accept(mapper, prepare(mapper, [changed], sha="b" * 64))["new_fact_count"] == 1


def test_legacy_fact_key_and_source_digest_preserved_when_supplemented(mapper):
    original = row()
    original["account"]["identity"] = "old-source-digest"
    result = accept(mapper, prepare(mapper, [original]))
    fact_id = result["processed_rows"][0]["transaction_id"]
    mapper.db.execute(update(TransactionFact).where(TransactionFact.id == fact_id).values(
        fact_key="old-immutable-key", account_code="old-source-digest"))
    mapper.db.commit()
    assert accept(mapper, prepare(mapper, [original | dict(row_number=2)], sha="b" * 64))["new_fact_count"] == 0
    fact = mapper.db.execute(select(TransactionFact.__table__)).mappings().one()
    assert fact["fact_key"] == "old-immutable-key" and fact["account_code"] == "old-source-digest"


def test_same_identity_conflicting_explicit_refs_or_core_not_chosen_arbitrarily(mapper):
    rows = prepare(mapper, [row(1), row(2)])
    choices = {key: dict(decision="ACCEPT", account_ref_id=0) if key[1] == 1 else dict(decision="ACCEPT") for key in rows}
    with pytest.raises(TargetIntakeError, match="ACCOUNT_BINDING_CONFLICT"):
        accept(mapper, rows, choices)
    assert count(mapper, TransactionFact) == 0
    changed = {key: value | dict(amount_minor=-30000) if key[1] == 2 else value for key, value in rows.items()}
    with pytest.raises(TargetIntakeError, match="FACT_CONFLICT"):
        accept(mapper, changed)


@pytest.mark.parametrize("stage", ["facts", "defaults", "tags", "source_rows", "file_counts"])
def test_every_write_stage_failure_rolls_back_whole_batch(mapper, stage):
    rows = prepare(mapper, [row()])
    def fault(current):
        if current == stage:
            raise RuntimeError("mock failure")
    with pytest.raises(RuntimeError):
        accept(mapper, rows, fault=fault)
    for entity in (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow):
        assert count(mapper, entity) == 0
    assert mapper.progress([next(iter(rows))[0]])[0]["remaining"] == 1


def seed_existing_candidates(mapper, number):
    original = row()
    raw_hash, payload = raw_evidence(original, {})
    file = TransactionImportFile(filename="fictional-legacy.csv", sha256="a" * 64, source_type=201,
        file_format=1, status=1, total_count=number, success_count=number)
    fact = TransactionFact(**(fact_values(original, "a" * 64) | dict(fact_key="legacy-first")))
    mapper.db.add_all([file, fact])
    mapper.db.flush()
    mapper.db.execute(insert(TransactionImportRow), [dict(transaction_import_file_id=file.id, source_row_number=index,
        source_reference=original["reference"], transaction_fact_id=fact.id, row_status=1, raw_hash=raw_hash,
        raw_payload=payload) for index in range(1, number + 1)])
    mapper.db.commit()
    return original, file.id, fact.id


def test_ambiguous_legacy_source_identity_never_chooses_latest(mapper):
    original, file_id, _fact_id = seed_existing_candidates(mapper, 1)
    second = TransactionFact(**(fact_values(original, "a" * 64) | dict(fact_key="legacy-second")))
    mapper.db.add(second)
    mapper.db.flush()
    raw_hash, payload = raw_evidence(original, {})
    mapper.db.add(TransactionImportRow(transaction_import_file_id=file_id, source_row_number=2,
        source_reference=original["reference"], transaction_fact_id=second.id, row_status=1,
        raw_hash=raw_hash, raw_payload=payload))
    mapper.db.execute(update(TransactionImportFile).where(TransactionImportFile.id == file_id).values(total_count=2, success_count=2))
    mapper.db.commit()
    rows = prepare(mapper, [original], sha="b" * 64)
    candidates = mapper.match(rows, {})
    candidate = next(iter(candidates.values()))
    assert (candidate["classification"], candidate["issue"], candidate["fact_id"]) == ("AMBIGUOUS", "IDENTITY_AMBIGUOUS", 0)
    with pytest.raises(TargetIntakeError, match="IDENTITY_AMBIGUOUS"):
        accept(mapper, rows)
    assert count(mapper, TransactionFact) == 2 and count(mapper, ReviewCase) == 0


def test_more_than_50000_source_candidates_fails_not_truncated_match(mapper):
    original, _file_id, _fact_id = seed_existing_candidates(mapper, 50001)
    rows = prepare(mapper, [original], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="IMPORT_MATCH_LIMIT"):
        mapper.match(rows, {})
    assert count(mapper, TransactionFact) == 1 and count(mapper, ReviewCase) == 0


@pytest.mark.parametrize("disposition", ["non_posted", "neutral_evidence"])
def test_non_cash_source_evidence_explicit_skip_is_not_a_zero_fact(mapper, disposition):
    rows = prepare(mapper, [row(disposition=disposition)])
    with pytest.raises(TargetIntakeError, match="ROW_INVALID"):
        accept(mapper, rows)
    result = accept(mapper, rows, {key: dict(decision="SKIP") for key in rows})
    assert result["new_fact_count"] == 0 and result["skipped_count"] == 1
    assert result["files"][0]["status"] == 1
    assert count(mapper, TransactionFact) == count(mapper, ReviewCase) == 0
