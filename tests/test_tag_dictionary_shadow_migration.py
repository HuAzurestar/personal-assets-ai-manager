from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, TagView, ViewTag
from app.target_database import TargetBase
from app.models.target import TargetTag, TargetTagView
from app.services.tag_dictionary_migration_service import (
    TagDictionaryShadowMigrationService,
)


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'tag-dictionary-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed_dictionary(sessions):
    started = datetime(2026, 9, 12, 12)
    with sessions() as db:
        category = TagView(
            name="Category",
            system_name="category",
            archived=False,
            created_at=started,
        )
        scenario = TagView(
            name="Scenario",
            system_name="scenario",
            archived=True,
            created_at=started + timedelta(minutes=1),
        )
        db.add_all([category, scenario])
        db.flush()
        db.add_all([
            ViewTag(
                view_id=category.id,
                name="Unclassified",
                system_name="unclassified",
                is_unclassified=True,
                archived=False,
            ),
            ViewTag(
                view_id=category.id,
                name="Dining",
                system_name="dining",
                is_unclassified=False,
                archived=False,
            ),
            ViewTag(
                view_id=scenario.id,
                name="Unclassified",
                system_name="unclassified",
                is_unclassified=True,
                archived=False,
            ),
            ViewTag(
                view_id=scenario.id,
                name="Travel",
                system_name="travel",
                is_unclassified=False,
                archived=False,
            ),
        ])
        db.commit()
        return category.id, scenario.id, started


def test_tag_dictionary_shadow_preserves_ids_status_and_source_rows(tmp_path):
    _, sessions = _database(tmp_path, "state")
    category_id, scenario_id, started = _seed_dictionary(sessions)
    with sessions() as db:
        source_counts = (db.query(TagView).count(), db.query(ViewTag).count())
        report = TagDictionaryShadowMigrationService(db).backfill_and_compare()
        assert report.matched, report.blockers
        assert report.tag_view.inserted_count == 2
        assert report.tag.inserted_count == 4

        views = db.scalars(select(TargetTagView).order_by(TargetTagView.id)).all()
        assert [(view.id, view.status) for view in views] == [
            (category_id, "ACTIVE"),
            (scenario_id, "ARCHIVED"),
        ]
        assert views[0].created_time == views[0].updated_time == started
        tags = db.scalars(select(TargetTag).order_by(TargetTag.view_id, TargetTag.id)).all()
        assert [tag.id for tag in tags] == db.scalars(
            select(ViewTag.id).order_by(ViewTag.view_id, ViewTag.id)
        ).all()
        assert [tag.status for tag in tags] == [
            "ACTIVE", "ACTIVE", "ARCHIVED", "ARCHIVED",
        ]
        assert (db.query(TagView).count(), db.query(ViewTag).count()) == source_counts

        repeated = TagDictionaryShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.tag_view.inserted_count == 0
        assert repeated.tag.inserted_count == 0


def _seed_views(sessions, count: int):
    started = datetime(2026, 9, 12, 13)
    with sessions() as db:
        for index in range(count):
            view = TagView(
                name=f"View {index}",
                system_name=f"view_{index}",
                archived=False,
                created_at=started,
            )
            db.add(view)
            db.flush()
            db.add(ViewTag(
                view_id=view.id,
                name="Unclassified",
                system_name="unclassified",
                is_unclassified=True,
                archived=False,
            ))
        db.commit()


def _dictionary_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_views(sessions, count)
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            assert TagDictionaryShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_tag_dictionary_select_count_does_not_grow_with_views(tmp_path):
    assert _dictionary_select_count(tmp_path, 10) == _dictionary_select_count(tmp_path, 100) == 6
