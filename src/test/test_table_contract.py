from sqlalchemy import create_engine, inspect, text

from backend.core.target_database import TARGET_TABLE_NAMES, TargetBase, init_target_db
from script.reset_target_database import reset


def test_target_metadata_contains_exactly_the_target_tables():
    assert len(TARGET_TABLE_NAMES) == 10
    assert set(TargetBase.metadata.tables) == set(TARGET_TABLE_NAMES)


def test_empty_target_database_creates_only_the_target_tables(tmp_path):
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
            connection.execute(text(
                "CREATE TABLE legacy_example (id INTEGER PRIMARY KEY)"
            ))
    finally:
        engine.dispose()

    actual = reset(path)
    assert set(actual) == set(TARGET_TABLE_NAMES)
