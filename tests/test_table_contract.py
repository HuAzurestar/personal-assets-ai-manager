from app.database import (
    Base,
    LEGACY_LEDGER_TABLE_NAMES,
    POST_MERGE_COMPATIBILITY_TABLE_NAMES,
    SEPARATE_MODULE_TABLE_NAMES,
    TARGET_TABLE_NAMES,
    init_target_db,
)
from sqlalchemy import create_engine, inspect


def test_every_physical_table_has_an_explicit_pirc9_disposition():
    target = set(TARGET_TABLE_NAMES)
    legacy = set(LEGACY_LEDGER_TABLE_NAMES)
    post_merge = set(POST_MERGE_COMPATIBILITY_TABLE_NAMES)
    separate = set(SEPARATE_MODULE_TABLE_NAMES)

    assert len(target) == 11
    assert len(legacy) == 22
    assert len(post_merge) == 5
    assert len(separate) == 1
    assert not target & legacy
    assert not target & post_merge
    assert not target & separate
    assert not legacy & post_merge
    assert not legacy & separate
    assert not post_merge & separate
    assert set(Base.metadata.tables) == target | legacy | post_merge | separate


def test_empty_target_database_creates_only_the_11_pirc9_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-only.db'}")
    try:
        init_target_db(bind=engine)
        assert set(inspect(engine).get_table_names()) == set(TARGET_TABLE_NAMES)
    finally:
        engine.dispose()
