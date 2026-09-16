---
name: router-layer
description: Change PAAM FastAPI routers, URL modules or versions, HTTP endpoint names, request dependencies, or domain-error translation.
---

# Router and API layer

## Boundaries

- Router modules are public API capability boundaries. They are not the same
  thing as the Fact, Review, and Ledger architecture layers.
- The top-level Router modules are Import, Ledger, Tag, and System. Do not add a
  top-level module merely to mirror an internal architecture layer.
- Ledger is the primary business API module. Review and Account are Ledger
  objects. Fact Conflict is an Import object.
- Tag Assignment is a Tag-module object keyed by the target Ledger ID. It
  updates the Ledger's effective tag state directly; it is not a Review action.
- Keep `src/backend/router` flat. Do not create version subdirectories.
- Use `module.py` while a module is small. Split a large module into
  `module_object.py` files by cohesive API object, not by individual endpoint.
- Because `import` is a Python keyword, use `import_router.py` for the main
  Import Router and `import_object.py` for its split objects.
- Infrastructure files such as `dependency.py` and `error.py` are not business
  modules.

## Versions

- `v0` is for Demo, Mock, and experimental APIs without a production stability
  commitment.
- `v1` is the current production API. A newer internal implementation remains
  `v1` when it is the production contract.
- `v2` is reserved for a future enhanced contract that must coexist with `v1`.
  Do not use `v2` merely to distinguish new code from legacy code.
- The production module prefixes are `/paam/import/v1`, `/paam/ledger/v1`, and
  `/paam/tag/v1`. System health remains under `/api/health`.
- A Mapper or Entity refactor does not change a production v1 request or
  response contract. Translate physical names and values at the Service/DTO
  boundary. A deliberate incompatible public contract requires an explicit
  API design and version decision.

## Ledger API migration

- The strict Review-Fact-Economic Allocation implementation is the production
  Ledger implementation. Its public API belongs under `/paam/ledger/v1`; do
  not expose it as `/paam/review/v2` or `/paam/economy/v1`.
- Canonical Ledger read objects are `/flow/list`, `/flow/{ledger_id}`, and
  `/flow/summary`.
- Canonical Ledger Review objects use `/review`, `/review/list`,
  `/review/{review_id}`, and `/review/{review_id}/{action}`. Creation uses
  `POST /review`, update uses `PUT /review/{review_id}`, and state transitions
  use actions only after the identifier.
- Ledger-facing Fact candidates use `/fact/list`. They must adopt the shared
  `page` and `page_size` contract before the old limit-only endpoint is
  retired.
- The aggregate `/ledger/v1/entry/*`, non-Allocation `/review/v1/case/*`, and
  Fact-based `/review/v1/account/*` contracts are legacy. Account correction
  remains a separate migration decision until a Ledger-owned replacement is
  designed; do not hide that redesign inside a path rename.

## Caller migration safety

- A route path change and every in-repository frontend path replacement belong
  in the same commit. For a path-only migration, do not redesign page layout,
  navigation, interaction flow, request fields, or response handling.
- Before deleting a compatibility route, search `src/frontend`, `src/script`,
  `src/report`, and `src/test` for its complete old prefix. Delete it only when
  active callers are migrated or intentionally removed in the same commit.
- A legacy detail route stays available until the canonical detail response
  supplies every field used by the UI. Migrate Tag editing and other retained
  controls to the canonical Ledger detail before removing the legacy route.
- Keep API path migration separate from response-envelope normalization and
  business behavior changes. Each gets its own commit and verification.
- After each route migration, verify OpenAPI contains the new route and omits
  the retired route, then run backend tests and the affected UI/runtime tests.

## Router rules

- Follow `Router -> Service -> Data Mapper -> Entity / SQLite`.
- Routers own HTTP parsing, dependency injection, status codes, and DTO
  validation only. They do not own use-case orchestration or SQL.
- Every business Router declares its complete module prefix on `APIRouter`.
  Route decorators contain only object-relative paths. System routes are the
  exception because they do not share one business prefix.
- Request and Response DTOs are optional. Use them when validation, response
  structure, or contract complexity requires them; simple endpoints may use
  plain parameters and return values.
- Do not add a route-local response-envelope helper whose only behavior is
  constructing a response DTO. When a response DTO is useful, construct it
  directly at the Router boundary.
- Use one primary `APIRouter` per file and one primary Service responsibility
  per Router object.
- Format business `APIRouter` declarations consistently with `prefix`, `tags`,
  and `route_class` on separate lines in that order. Omit inapplicable options
  from infrastructure-only Routers such as System.
- Custom Router files use singular nouns. Do not add the migration-era
  `target_` prefix.
- During migration, isolate compatibility routes with a `_legacy` suffix. New
  and legacy routes must not share one Router file. Delete the temporary legacy
  files after every caller has migrated.
- Keep path-only changes separate from behavior changes. Preserve request,
  response, transaction, and error behavior in file-move commits.
- Define backend domain and application Error classes under `src/backend/error`.
  Service, Mapper, Core, and Router files do not declare their own public Error
  classes. Router error code only translates those Errors to HTTP responses.
- Translate domain errors consistently at the HTTP boundary. Do not duplicate
  route-local wrappers for the same error contract.

## REST contract

- Canonical paths use
  `/paam/{module}/{version}/{object}/{resource_id}`. Object names are singular.
- Public field names describe domain resources rather than physical tables or
  Mapper rows. Compatibility aliases may read persisted historical payloads,
  but OpenAPI and serialized responses expose only the canonical field names.
- A nested object uses
  `/paam/{module}/{version}/{object}/{resource_id}/{subobject}/{subresource_id}`.
  Omit the trailing identifier when addressing the nested collection.
- Prefer HTTP CRUD semantics before adding action paths:
  - `POST /{object}` creates a resource.
  - `GET /{object}/{resource_id}` reads one resource.
  - `PUT /{object}/{resource_id}` updates or replaces one resource.
  - `DELETE /{object}/{resource_id}` deletes one resource when hard deletion is
    valid for that domain.
- `GET /{object}/list` is the standard collection query. `list` is the explicit
  collection endpoint name; do not add `create`, `detail`, `update`, or `set`
  when the HTTP method and resource path already express the operation.
- Use `POST /{object}/{resource_id}/{action}` only when a domain command cannot
  be represented truthfully as CRUD. Review transitions such as `confirm`,
  `revoke`, and `restore` follow this form.
- A command that targets several resources uses
  `POST /{object}/{batch_action}`. Its JSON body carries the identifiers in a
  resource-specific `*_ids` field, such as `review_ids: [1, 2, 3]`. Do not put
  several identifiers in the path or encode them as a comma-separated query
  parameter.
- Replacing one Ledger's complete tag state uses
  `PUT /paam/tag/v1/assignment/{ledger_id}`. Do not add `set` to the path or
  address the assignment through a Fact or Review identifier.
- Batch identifier lists are non-empty and contain at most 100 unique IDs by
  default. A specific object may impose a lower limit. Workloads that genuinely
  need more than 100 IDs require a separately designed chunked, asynchronous,
  or dedicated batch contract instead of silently raising the shared limit.
- Path parameters identify resources. Filters, sorting, and pagination use
  query parameters. Write input uses a JSON request body when a body is needed.
- Every successful business API response uses the common
  `status/message/body` envelope and HTTP status `200`. `status` is the integer
  HTTP status code and must equal the actual response status. `message`
  describes the result, and `body` carries the returned object, collection,
  page, or an empty object when no result data is needed.
- Creation, update, deletion, and domain-action success all use HTTP `200`.
  Failures use an appropriate non-200 status code under the shared error
  contract.
- Failed business responses use the same envelope. Their integer `status`
  equals the actual non-200 HTTP status, `message` contains the human-readable
  error, and `body` carries a stable machine-readable error code plus optional
  structured details.
- Canonical paged list requests use `page` with default `1` and `page_size` with
  default `20` and maximum `100`. The response `body` contains `items`, `total`,
  `page`, and `page_size`.
- Compatibility routes retain their existing request defaults and response
  shapes until their callers migrate.

## Detail workspace contract

- The Details workspace is a permanent, read-oriented inspection surface for
  core persisted objects. Do not remove it as redundant with task/workbench
  screens.
- A Details list represents exactly one primary persisted object. The standard
  objects are Transaction Fact, Import File, Ledger, Review, and Tag. Do not
  substitute a command candidate set, workflow projection, or cross-object
  aggregate for a PO list.
- A list row contains fields owned by that PO. A deliberately small derived
  count may be exposed only when labeled as a summary; related object data
  belongs in detail.
- Every standard `GET /{object}/list` supports server-side search, filtering,
  sorting, and pagination. Use the shared `q`, `filter`, `sorter`, `page`, and
  `page_size` contract; never fetch an arbitrary large page and implement the
  canonical list query only in the browser.
- `filter` and `sorter` are JSON query objects. Each object API must whitelist
  supported filter fields, sorter fields, value types, and sort directions at
  its boundary. Unknown fields or malformed objects fail with `422`; never
  interpolate client-supplied field names into SQL.
- A PO detail response contains the complete public PO record plus only the
  relationships needed to understand it. Relationship sections are read-only
  unless the owning domain explicitly permits a command.
- Relationship navigation from one detail drawer into another is not part of
  the current contract. A related subtable is optional and must be justified by
  a real relationship and a concrete inspection need.
- Detail permissions are object-specific: Transaction Fact relations are
  read-only; Import File relations are read-only; Ledger relations are
  read-only while Tag assignment is editable; Review relations are read-only
  while creating a Review remains an explicit page action; Tag is an editable
  management view and does not require a standard detail drawer.
- Prefer reusable UI structures for list surfaces, filters, sort controls,
  pagination, drawer shells, field groups, and relationship sections. Object
  modules still define their own labels, formatting, fields, and allowed
  actions; do not auto-render arbitrary JSON as a business UI.
