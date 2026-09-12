from app.database import (
    Base,
    LEGACY_LEDGER_TABLE_NAMES,
    POST_MERGE_COMPATIBILITY_TABLE_NAMES,
    SEPARATE_MODULE_TABLE_NAMES,
)
from app.target_database import TargetBase, TARGET_TABLE_NAMES, init_target_db
from sqlalchemy import create_engine, inspect, text

from scripts.reset_target_database import reset


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
    assert set(Base.metadata.tables) == legacy | post_merge | separate
    assert set(TargetBase.metadata.tables) == target


def test_empty_target_database_creates_only_the_11_pirc9_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-only.db'}")
    try:
        init_target_db(bind=engine)
        assert set(inspect(engine).get_table_names()) == set(TARGET_TABLE_NAMES)
    finally:
        engine.dispose()


def test_reset_target_database_drops_all_non_target_tables(tmp_path):
    path = tmp_path / "mixed.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE legacy_example (id INTEGER PRIMARY KEY)"))
    finally:
        engine.dispose()

    actual = reset(path)
    assert set(actual) == set(TARGET_TABLE_NAMES)
