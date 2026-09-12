from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    TagDictionaryShadowReport,
    TargetTagShadowVO,
    TargetTagViewShadowVO,
)
from app.services.review_migration_service import ReviewMatterShadowMigrationService


class TagDictionaryShadowMigrationService:
    """Backfill the small tag dictionary while preserving stable legacy IDs."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> TagDictionaryShadowReport:
        blockers: list[str] = []
        legacy_views = self.mapper.legacy_tag_views()
        legacy_tags = self.mapper.legacy_tag_values()
        expected_views, expected_tags = self._expected(
            legacy_views,
            legacy_tags,
            blockers,
        )
        current_views = self.mapper.target_tag_views()
        current_tags = self.mapper.target_tags()
        view_inserts = self._missing(expected_views, current_views)
        tag_inserts = self._missing(expected_tags, current_tags)
        try:
            self.mapper.insert_target_tag_views([asdict(row) for row in view_inserts])
            self.mapper.insert_target_tags([asdict(row) for row in tag_inserts])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        actual_views = self.mapper.target_tag_views()
        actual_tags = self.mapper.target_tags()
        support = ReviewMatterShadowMigrationService
        view_report = support._table_report(expected_views, actual_views, len(view_inserts))
        tag_report = support._table_report(expected_tags, actual_tags, len(tag_inserts))
        return TagDictionaryShadowReport(
            matched=(
                not blockers
                and not view_report.mismatched_ids
                and not tag_report.mismatched_ids
            ),
            tag_view=view_report,
            tag=tag_report,
            blockers=blockers,
        )

    @staticmethod
    def _expected(views, tags, blockers):
        views_by_id = {}
        system_names: dict[str, int] = {}
        expected_views = []
        for view in views:
            if view.id <= 0 or not view.system_name:
                blockers.append(f"tag view {view.id}: ID and system_name are required")
                continue
            owner = system_names.get(view.system_name)
            if owner is not None:
                blockers.append(
                    f"tag views {owner} and {view.id}: duplicate system_name {view.system_name}"
                )
                continue
            system_names[view.system_name] = view.id
            views_by_id[view.id] = view
            expected_views.append(TargetTagViewShadowVO(
                id=view.id,
                created_time=view.created_at,
                updated_time=view.created_at,
                name=view.name,
                system_name=view.system_name,
                status="ARCHIVED" if view.archived else "ACTIVE",
            ))

        expected_tags = []
        tag_keys: dict[tuple[int, str], int] = {}
        unclassified_by_view: dict[int, list[int]] = {}
        for tag in tags:
            view = views_by_id.get(tag.view_id)
            if not view:
                blockers.append(f"tag {tag.id}: missing valid tag view {tag.view_id}")
                continue
            if tag.id <= 0 or not tag.system_name:
                blockers.append(f"tag {tag.id}: ID and system_name are required")
                continue
            key = (tag.view_id, tag.system_name)
            owner = tag_keys.get(key)
            if owner is not None:
                blockers.append(
                    f"tags {owner} and {tag.id}: duplicate system identity {key}"
                )
                continue
            tag_keys[key] = tag.id
            if tag.is_unclassified:
                unclassified_by_view.setdefault(tag.view_id, []).append(tag.id)
                if tag.system_name != "unclassified":
                    blockers.append(
                        f"tag {tag.id}: unclassified value must use system_name unclassified"
                    )
            expected_tags.append(TargetTagShadowVO(
                id=tag.id,
                created_time=view.created_at,
                updated_time=view.created_at,
                view_id=tag.view_id,
                name=tag.name,
                system_name=tag.system_name,
                status="ARCHIVED" if tag.archived or view.archived else "ACTIVE",
            ))

        for view_id in views_by_id:
            defaults = unclassified_by_view.get(view_id, [])
            if len(defaults) != 1:
                blockers.append(
                    f"tag view {view_id}: expected one unclassified value, found {len(defaults)}"
                )
        return tuple(expected_views), tuple(expected_tags)

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)
