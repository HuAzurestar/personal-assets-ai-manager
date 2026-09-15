from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.mapper.target_tag_assignment_mapper import (
    ExistingTagCase,
    TargetTagAssignmentMapper,
)
from backend.entity import ReviewHistory
from backend.schema.target_tag import TargetTagAssignmentRead, TargetTagAssignmentRequest
from backend.service.target_tag_projection_service import TargetTagProjectionService
from backend.service.target_tag_service import TargetTagError


class TargetTagAssignmentService:
    """Apply one ledger-level selection as identical per-Fact TAG reviews."""

    def __init__(self, db: Session):
        self.mapper = TargetTagAssignmentMapper(db)
        self.projection = TargetTagProjectionService(db)

    def assign(
        self,
        ledger_id: int,
        payload: TargetTagAssignmentRequest,
    ) -> TargetTagAssignmentRead:
        request_json = self._canonical({
            "operation": "ASSIGN",
            "ledger_id": ledger_id,
            **payload.model_dump(mode="json"),
        })
        try:
            self.mapper.begin_write()
            replay = self.mapper.idempotency(payload.idempotency_key)
            target = self.mapper.target(ledger_id)
            if target is None:
                raise TargetTagError(404, "ledger entry not found")
            existing = self.mapper.cases([fact.id for fact in target.facts])
            if replay is not None:
                if replay.operation != "ASSIGN" or replay.request_json != request_json:
                    raise TargetTagError(
                        409, "idempotency key was already used by another command"
                    )
                self.mapper.commit()
                return self._response(
                    target.ledger_id,
                    self._version(target.facts, existing),
                    payload.tag_state,
                    existing,
                )
            current_version = self._version(target.facts, existing)
            if current_version != payload.expected_version:
                raise TargetTagError(
                    409, "tag Review changed; reload before assigning tags"
                )
            state = self.projection.validate_complete(payload.tag_state)
            now = datetime.now()
            facts = list(target.facts)
            new_facts = [fact for fact in facts if fact.id not in existing]
            result_json = self._canonical({"tag_state": state})
            title = "Ledger tags"
            case_by_fact = {
                fact_id: case.id for fact_id, case in existing.items()
            }
            case_by_fact.update(self.mapper.create_cases(
                new_facts,
                title=title,
                result_json=result_json,
                version=current_version + 1,
                now=now,
            ))
            self.mapper.update_cases(
                list(existing.values()),
                title=title,
                result_json=result_json,
                expected_version=current_version,
                now=now,
            )
            self.mapper.replace_lines(case_by_fact, facts, now=now)
            self.mapper.add_histories(self._histories(
                facts=facts,
                existing=existing,
                case_by_fact=case_by_fact,
                result_json=result_json,
                request_json=request_json,
                payload=payload,
                now=now,
            ))
            self.projection.sync_economics(
                self.mapper.ledger_facts([fact.id for fact in facts])
            )
            self.mapper.commit()
            return TargetTagAssignmentRead(
                ledger_id=ledger_id,
                version=current_version + 1,
                tag_state=state,
                review_case_ids=[case_by_fact[fact.id] for fact in facts],
            )
        except TargetTagError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetTagError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(
                409, "tag assignment write conflict; retry from the latest projection"
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    def _histories(
        self,
        *,
        facts,
        existing: dict[int, ExistingTagCase],
        case_by_fact: dict[int, int],
        result_json: str,
        request_json: str,
        payload: TargetTagAssignmentRequest,
        now: datetime,
    ) -> list[ReviewHistory]:
        rows = []
        for index, fact in enumerate(facts):
            prior = existing.get(fact.id)
            version = prior.version + 1 if prior else 1
            before_json = self._case_snapshot(prior) if prior else "{}"
            after_json = self._canonical({
                "id": case_by_fact[fact.id],
                "review_type": "TAG",
                "status": "CONFIRMED",
                "allocation_status": "COMPLETE",
                "version": version,
                "title": "Ledger tags",
                "result": json.loads(result_json),
                "lines": [{
                    "bill_id": fact.id,
                    "role": "TAGGED",
                    "party": "",
                    "amount_value": fact.amount_value,
                    "amount_scale": fact.amount_scale,
                    "currency_code": fact.currency_code,
                }],
            })
            rows.append(ReviewHistory(
                case_id=case_by_fact[fact.id],
                version=version,
                operation="ASSIGN",
                schema_version=1,
                request_json=request_json,
                before_json=before_json,
                after_json=after_json,
                snapshot_hash=self._hash(after_json),
                reverses_history_id=0,
                actor=payload.actor,
                reason=payload.reason,
                idempotency_key=(
                    payload.idempotency_key
                    if index == 0
                    else f"tag-{self._hash(f'{payload.idempotency_key}:{fact.id}')[:64]}"
                ),
                created_time=now,
                updated_time=now,
            ))
        return rows

    @classmethod
    def _case_snapshot(cls, case: ExistingTagCase) -> str:
        return cls._canonical({
            "id": case.id,
            "review_type": "TAG",
            "status": case.status,
            "allocation_status": "COMPLETE",
            "version": case.version,
            "title": case.title,
            "result": json.loads(case.result_json),
            "lines": [{
                "bill_id": case.bill_id,
                "role": "TAGGED",
                "party": "",
                "amount_value": case.amount_value,
                "amount_scale": case.amount_scale,
                "currency_code": case.currency_code,
            }],
        })

    @staticmethod
    def _version(facts, existing: dict[int, ExistingTagCase]) -> int:
        versions = {
            existing[fact.id].version if fact.id in existing else 0
            for fact in facts
        }
        if len(versions) != 1:
            raise TargetTagError(
                409, "source facts have different tag Review versions"
            )
        return versions.pop()

    @staticmethod
    def _response(
        ledger_id: int,
        version: int,
        state: dict[str, str],
        existing: dict[int, ExistingTagCase],
    ) -> TargetTagAssignmentRead:
        return TargetTagAssignmentRead(
            ledger_id=ledger_id,
            version=version,
            tag_state={key: state[key] for key in sorted(state)},
            review_case_ids=[existing[key].id for key in sorted(existing)],
        )

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
