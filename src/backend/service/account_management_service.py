"""Three-level metadata, CAS edits and explicit parent-change confirmation."""
import hashlib
import json
from datetime import datetime
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.error import TargetEconomicError, ListQueryError
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.list_query import iter_filter_fields, validate_list_capabilities


def reject(code, message, status=409):
    raise TargetEconomicError(status, message, code=code)


def mask(value):
    if not value:
        return ""
    # Even short identities are never exposed unmasked by this endpoint.
    return "****" + value[-4:] if len(value) > 4 else "****"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item)).encode()).hexdigest()


class AccountManagementService:
    def __init__(self, db):
        self.mapper = AccountManagementMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _get(self, kind, entity_id):
        if entity_id <= 0:
            reject("ACCOUNT_NOT_FOUND", "metadata object not found", 404)
        row = self.mapper.get(kind, entity_id)
        if row is None:
            reject("ACCOUNT_NOT_FOUND", "metadata object not found", 404)
        return dict(row)

    def _chain(self, account_id, *, active=False):
        if account_id == 0:
            return None, None
        account = self.mapper.get("account", account_id)
        party = self.mapper.get("party", account["party_id"]) if account else None
        if not account or not party:
            reject("ACCOUNT_RELATION_BROKEN", "account ownership chain is broken")
        if active and any(row["status"] != "ACTIVE" for row in (account, party)):
            reject("ACCOUNT_NOT_ACTIVE", "target ownership is closed")
        return dict(account), dict(party)

    def _po(self, kind, row, source_times=None):
        if kind != "ref":
            return dict(row)
        result = dict(row)
        result["reference"] = mask(result["reference"])
        result["source_identity"] = mask(result["source_identity"])
        result["identity_strength"] = {0: "UNKNOWN", 1: "RELIABLE", 2: "WEAK"}[row["identity_strength"]]
        result["latest_source_time"] = (source_times or {}).get(row["id"])
        return result

    def get(self, kind, entity_id):
        self.relations.read_snapshot()
        self.relations.validate()
        row = self._get(kind, entity_id)
        times = self.mapper.source_times([entity_id]) if kind == "ref" else None
        return self._po(kind, row, times)

    def page(self, kind, request):
        fields = {"id": ("=", "!="), "status": ("=", "!=")}
        if kind == "account":
            fields["party_id"] = ("=", "!=")
        if kind == "ref":
            fields["account_id"] = ("=", "!=")
        validate_list_capabilities(request, query_fields=(), filter_operators=fields,
            sorter_fields=("id", "created_time", "updated_time"), logical_operators=("AND", "OR", "NOT"), max_sorters=3)
        for expression in iter_filter_fields(request.filter):
            value = expression.val
            if expression.key == "status":
                valid = value in ("ACTIVE", "CLOSED")
            else:
                valid = type(value) is int and value >= (0 if expression.key == "account_id" else 1)
            if not valid:
                raise ListQueryError("invalid metadata filter value", code="LIST_FILTER_VALUE_INVALID")
        self.relations.read_snapshot()
        self.relations.validate()
        rows, total = self.mapper.page(kind, request)
        times = self.mapper.source_times([row["id"] for row in rows]) if kind == "ref" else None
        return dict(items=[self._po(kind, row, times) for row in rows], total=total,
                    page_index=request.page_index, page_size=request.page_size)

    def _write(self, operation):
        commit_started = False
        try:
            self.mapper.begin_write()
            self.relations.validate()
            kind, row = operation()
            self.relations.validate()
            result = self._po(kind, row, self.mapper.source_times([row["id"]]) if kind == "ref" else None)
            self.mapper.end_write()
            commit_started = True
            self.mapper.db.commit()
            return result
        except Exception as error:
            self.mapper.end_write()
            self.mapper.db.rollback()
            if commit_started:
                reject("RESULT_UNKNOWN", "commit outcome is unknown; query current metadata before another action", 503)
            if isinstance(error, (IntegrityError, OperationalError)):
                reject("WRITE_BUSY", "metadata write could not complete; no partial changes", 409)
            raise

    def create(self, kind, payload):
        def operation():
            values = payload.model_dump()
            if kind == "account":
                party = self._get("party", values["party_id"])
                if party["status"] != "ACTIVE":
                    reject("ACCOUNT_NOT_ACTIVE", "cannot add a group to a closed person")
            elif kind == "ref":
                self._chain(values["account_id"], active=True)
            return kind, self.mapper.create(kind, values)
        return self._write(operation)

    def update(self, kind, entity_id, payload):
        def operation():
            row = self._get(kind, entity_id)
            if row["updated_time"] != payload.expected_updated_time:
                reject("ENTITY_CHANGED", "metadata changed; reload before editing")
            values = payload.model_dump(exclude={"expected_updated_time"})
            if kind == "ref":
                self._chain(row["account_id"])
                if values.pop("account_id") != row["account_id"]:
                    reject("ACCOUNT_MOVE_REQUIRES_PREVIEW", "ownership change requires move preview/command")
                # Sending the displayed mask back does not erase the original registration.
                if values["reference"] == mask(row["reference"]):
                    values.pop("reference")
            return kind, self.mapper.edit(kind, row, values)
        return self._write(operation)

    def _move(self, ref_id, payload):
        row = self._get("ref", ref_id)
        if row["updated_time"] != payload.expected_updated_time:
            reject("ENTITY_CHANGED", "source ref changed; reload before moving")
        old_account, old_party = self._chain(row["account_id"])
        new_account, new_party = self._chain(payload.account_id, active=True)
        effect = dict(self.mapper.ledger_effect(ref_id))
        result = dict(ref_id=ref_id, from_account_id=row["account_id"], to_account_id=payload.account_id,
            from_party_id=old_party["id"] if old_party else 0, to_party_id=new_party["id"] if new_party else 0,
            cross_party=bool(old_party and new_party and old_party["id"] != new_party["id"]),
            affected_ledger_count=effect["count"])
        result["preview_digest"] = digest(dict(change=result, ref=row, old_account=old_account,
             old_party=old_party, new_account=new_account, new_party=new_party, ledger_effect=effect))
        return result, row

    def move_preview(self, ref_id, payload):
        self.relations.read_snapshot()
        self.relations.validate()
        return self._move(ref_id, payload)[0]

    def move_command(self, ref_id, payload):
        def operation():
            preview, row = self._move(ref_id, payload)
            if preview["preview_digest"] != payload.preview_digest:
                reject("ENTITY_CHANGED", "move impact changed; review a fresh preview")
            return "ref", self.mapper.edit("ref", row, dict(account_id=payload.account_id))
        return self._write(operation)
