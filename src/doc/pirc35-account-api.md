# PIRC-35 account metadata

The versioned `/paam/ledger/v1/account-party`, `/account`, `/account-ref` resources manage person → management set → concrete own-source identity. POST creates metadata; GET `/{id}` reads it; PUT `/{id}/metadata` requires the displayed `expected_updated_time`. GET `/list` uses the existing JSON filter/sorter URL parameters and the exact four-key list body, default 20 / maximum 100.

Names are labels, not identities. Manual refs never silently merge by name, institution or account suffix. Only parser-certified complete bank own-account numbers in `abc:statement-v1`, `ccb:statement-v1`, `cmb:statement-v1` may create/reuse RELIABLE refs automatically. Payment methods, order numbers, counterparties, masked accounts and unknown identities cannot. The public metadata read always masks `reference` and `source_identity`; there is no unmasked metadata endpoint. Source namespace/identity/strength are immutable.

`Ledger.account_ref_id=0` means unknown/unbound identity. A known ref with `account_id=0` means identified but ungrouped. Initial ref/default creation and new Fact/source acceptance share the importer transaction. Reused Facts are not rebound or restored by evidence intake.

Changing a ref's `account_id` requires POST `/{id}/move-preview`, then `/{id}/move-command` with `preview_digest` and the original expected time. Preview discloses old/new person/set and affected Ledger count; lock-time recomputation protects against intervening ownership or Ledger changes. Cross-person movement is explicit. No metadata operation updates Fact or Ledger contents. A group remains attached to its original person; moving a concrete card uses the ref operation.

Status ACTIVE/CLOSED is maintenance metadata, not a money deletion filter. Existing cash of closed cards remains counted. Statement/snapshot intervals are 0 (manual) by default; this resource does not create new jobs or balance snapshots. Latest accepted source intake time is read-only and derived from actual source rows.

Account scopes resolve ref IDs through indexed SQL subqueries within the same read snapshot, avoiding thousands of literal IN parameters. They are filters, not sort keys. Position quantities and PIRC-36 balance snapshots remain separate from these metadata records.

All writes serialize before their controlling reads. Stale edits/move impacts report ENTITY_CHANGED; broken positive ownership chains report ACCOUNT_RELATION_BROKEN. Uncertain commits report RESULT_UNKNOWN: query current metadata/list and do not automatically repeat a create or move.
