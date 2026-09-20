from sqlalchemy import Integer, String, create_engine, inspect, text
from sqlalchemy.sql.schema import CheckConstraint, UniqueConstraint

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


def test_sql_assets_and_orm_mapping_have_schema_parity(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema-parity.db'}")
    try:
        init_target_db(bind=engine)
        database = inspect(engine)
        for table_name in TARGET_TABLE_NAMES:
            actual = {column["name"]: column for column in database.get_columns(table_name)}
            mapped = TargetBase.metadata.tables[table_name]
            assert set(actual) == {column.name for column in mapped.columns}
            for column in mapped.columns:
                mapped_type = getattr(column.type, "impl", column.type)
                expected_affinity = (
                    "INTEGER" if isinstance(mapped_type, Integer)
                    else "TEXT" if isinstance(mapped_type, String)
                    else None
                )
                assert expected_affinity is not None, (table_name, column.name, column.type)
                assert actual[column.name]["type"].__class__.__name__.upper() in {
                    expected_affinity,
                    "BIGINT" if expected_affinity == "INTEGER" else expected_affinity,
                }
                assert bool(actual[column.name]["primary_key"]) == column.primary_key
                if not column.primary_key:
                    assert actual[column.name]["nullable"] == column.nullable
            assert not any(
                isinstance(constraint, CheckConstraint)
                for constraint in mapped.constraints
            )
            actual_indexes = database.get_indexes(table_name)
            actual_unique = {
                tuple(item["column_names"])
                for item in database.get_unique_constraints(table_name)
            } | {
                tuple(item["column_names"])
                for item in actual_indexes
                if item["unique"]
            }
            mapped_unique = {
                tuple(column.name for column in constraint.columns)
                for constraint in mapped.constraints
                if isinstance(constraint, UniqueConstraint)
            } | {
                tuple(column.name for column in index.columns)
                for index in mapped.indexes
                if index.unique
            }
            assert actual_unique == mapped_unique
            actual_nonunique = {
                item["name"]: tuple(item["column_names"])
                for item in actual_indexes
                if not item["unique"]
            }
            mapped_nonunique = {
                index.name: tuple(column.name for column in index.columns)
                for index in mapped.indexes
                if not index.unique
            }
            assert actual_nonunique == mapped_nonunique
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
