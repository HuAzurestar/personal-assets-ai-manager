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
- Ledger Review candidates use `/review_candidate/list`. They are a creation
  aid, not the Transaction Fact PO list. When paged, they follow the shared
  `page_index/page_size` contract and declare their supported optional query,
  filter, and sorter capabilities explicitly.
- A Ledger-owned account is the nested subresource
  `/flow/{ledger_id}/account`. Reading or updating it starts from the Ledger ID
  and never rewrites the source Transaction Fact account. Updates use the
  Ledger projection version for optimistic concurrency.
- The aggregate `/ledger/v1/entry/*`, non-Allocation `/review/v1/case/*`, and
  Fact-based `/review/v1/account/*` contracts are legacy. Account correction
  must not be restored after callers migrate to the Ledger-owned subresource.

## Import API migration

- Fact Conflict is an Import-owned object even though its persisted workflow
  uses Review records internally. Do not expose it through a generic
  `/paam/review/v1/case/*` API.
- Canonical Fact Conflict reads are `GET /fact_conflict/list` and
  `GET /fact_conflict/{conflict_id}` under `/paam/import/v1`.
- Conflict commands use
  `POST /fact_conflict/{conflict_id}/{resolve|dismiss|reopen}`. Keep the custom
  object name in underscore form; do not restore the retired `fact-conflict`
  spelling.
- Fact Conflict list and detail services must constrain reads to
  `review_type == FACT_CONFLICT`; an Import endpoint must never become a
  generic back door to Account or Ledger Review records.
- Canonical Import File inspection uses `GET /import_file/list`,
  `GET /import_file/{import_file_id}`, and
  `GET /import_file/{import_file_id}/transaction_fact/list`. The nested
  Transaction Fact route is a dedicated relationship collection: it returns
  every related Fact and accepts no pagination, query, filter, or sorter
  parameters. It is not a standard PO list. The optional filtered aggregate is
  `GET /import_file/summary`; keep summary counts out of individual PO rows.
- The former `/batch/list`, `/batch/{batch_id}/row/list`, and Import-owned
  `/account/list` projections are retired. Import history is an Import File PO
  view; accepted row-level relationships are inspected through the
  Transaction Fact subresource. Do not restore raw JSON rows or Fact accounts
  as fields on Import File list items.

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
- Request and Response DTOs are optional for simple non-list endpoints. Every
  standard list endpoint uses an object-specific `ListRequest` subclass and an
  object-specific `ListResponse` subclass so pagination and OpenAPI remain
  uniform and strongly typed.
- Do not add a route-local response-envelope helper whose only behavior is
  constructing a response DTO. When a response DTO is useful, construct it
  directly at the Router boundary.
- Do not add implicit response factories such as `_envelope`, `success`, or
  `make_response`. Reuse protocol structure through DTO inheritance, and have
  the Router explicitly instantiate the concrete response DTO.
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
  HTTP status code and must equal the actual response status. A successful
  response uses `message == "ok"`; callers determine success from `status`, not
  from `message`. `body` carries the returned object, collection, page, or an
  empty object when no result data is needed. A successful response may add an
  optional top-level `warnings` array for stable machine-readable advisory
  messages. Omit `warnings` when it is empty; a warning never changes the
  successful status or the `"ok"` message.
- Creation, update, deletion, and domain-action success all use HTTP `200`.
  Failures use an appropriate non-200 status code under the shared error
  contract.
- Failed business responses use the same envelope. Their integer `status`
  equals the actual non-200 HTTP status, `message` contains the human-readable
  error, and `body` carries a stable machine-readable error code plus optional
  structured details.
- The shared DTO hierarchy is `Response -> SuccessResponse[BodyT] ->
  ListResponse[ItemT]` for successful lists and `Response -> ErrorResponse` for
  failures. `SuccessResponse` fixes `status` to integer `200` and `message` to
  `"ok"`. `ErrorResponse` keeps the actual non-200 integer status, uses the
  error text as `message`, and carries `code` plus optional `details` in its
  body. These fields must also be required in the serialized OpenAPI schema.
- Canonical paged list requests inherit from `ListRequest`. Its only baseline
  fields are `page_index` and `page_size`. `page_index` is a one-based page
  number, never a row offset; it defaults to `1`. `page_size` defaults to `20`
  and is at most `100`.
- `query`, `filter`, and `sorter` are optional JSON query parameters. Callers
  may omit all three, so `?page_index=1&page_size=20` is a complete request.
  Omission normalizes to an empty query list, no filter expression, and an
  empty sorter list. Never require callers to transmit `query=[]`,
  `filter=null`, or `sorter=[]`, and do not echo these inputs in the response.
- Do not expose the retired `q` or `keyword` parameters. `query` is the sole
  reserved text-search capability. Until an object implements search, a
  non-empty `query` fails with `LIST_QUERY_NOT_SUPPORTED`; do not retain a
  placeholder SQL `LIKE` implementation.
- Every object declares concrete Query, Filter, and Sorter capability DTOs and
  an object-specific list request subclass. A capability may have an empty
  field whitelist. Wire-level JSON is validated into the typed expression DTOs
  before a Mapper sees it.
- `ListResponse[ItemT]` extends `SuccessResponse[ListBody[ItemT]]`. Its body
  contains exactly `items`, `total`, `page_index`, and `page_size`; request
  search and ordering state is not echoed back. Each object declares a concrete
  response subclass, such as
  `TransactionFactListResponse(ListResponse[TransactionFactListItem])`, to keep
  stable and readable OpenAPI component names.
- A dedicated non-paged relationship collection does not inherit
  `ListRequest` or `ListResponse`. It returns `items` and `total`, rejects list
  query parameters with `LIST_PARAMETER_NOT_SUPPORTED`, and never silently
  truncates its result. A relationship that outgrows this contract requires a
  separately designed API rather than hidden pagination.
- Compatibility routes retain their existing request defaults and response
  shapes until their callers migrate.

## List expression protocol

- A Query is an ordered JSON array of `{ "key": field, "word": text }`
  expressions. Query fields are object-specific text fields. Multiple entries
  compose with `AND`. A word is literal text in v1; wildcard syntax such as `*`
  and `?` is not interpreted. IDs use a Filter equality expression rather than
  text search.
- A Filter is either a field expression
  `{ "key": field, "op": operator, "val": value }` or a recursive logical
  expression `{ "op": logical_operator, "expression": [...] }`.
- Comparison operators are `>`, `>=`, `<`, `<=`, `=`, `!=`, and `between`.
  Logical operators are `AND`, `OR`, and `NOT`. `NOT` has exactly one child.
  Each object whitelists the operators and value types accepted by each field.
  Do not add `IN`, `LIKE`, or null operators without a separate protocol
  decision.
- `between` uses `{ "start": value, "end": value }` and is left-closed and
  right-open: `start <= field < end`. Time values are timezone-aware RFC 3339
  datetimes. Filter the real PO field such as `occurred_time`, `created_time`,
  or `updated_time`; never synthesize public `date_from` or `date_to` fields.
- A Sorter is an ordered JSON array of
  `{ "key": field, "direction": "asc" | "desc" }` expressions. Array order
  defines sort priority. An empty sorter uses the object's documented default.
  A Mapper always appends a stable ID tie-break without requiring the caller to
  expose ID as a public sort key. Limit public sorter expressions to three.
- Limit a Filter to three levels and twenty field expressions. Reject excessive
  expressions rather than compiling an unbounded SQL tree.
- Query, Filter, Sorter, and pagination compose in that order by default. An
  unsupported combination fails explicitly; never ignore part of the request.
- Routers parse JSON and validate structure, field/operator whitelists, value
  types, and supported combinations. Mappers translate already validated,
  typed expressions into explicit SQL column maps. Never interpolate a client
  field or direction string into SQL.

## List validation codes

- List protocol failures use HTTP and envelope status `422`. Use stable codes:
  - `LIST_QUERY_INVALID`, `LIST_FILTER_INVALID`, `LIST_SORTER_INVALID` for
    malformed JSON or expression shapes.
  - `LIST_QUERY_NOT_SUPPORTED`, `LIST_FILTER_NOT_SUPPORTED`, and
    `LIST_SORTER_NOT_SUPPORTED` when the object does not implement a supplied
    capability.
  - `LIST_QUERY_FIELD_NOT_SUPPORTED`, `LIST_FILTER_FIELD_NOT_SUPPORTED`, and
    `LIST_SORTER_FIELD_NOT_SUPPORTED` for fields outside an object's whitelist.
  - `LIST_FILTER_OPERATOR_NOT_SUPPORTED` and
    `LIST_SORTER_DIRECTION_NOT_SUPPORTED` for unsupported operations.
  - `LIST_QUERY_WORD_INVALID` and `LIST_FILTER_VALUE_INVALID` for invalid typed
    values.
  - `LIST_COMBINATION_NOT_SUPPORTED` for a valid but unsupported combination.
  - `LIST_EXPRESSION_LIMIT_EXCEEDED` for excessive depth or expression count.
  - `LIST_PARAMETER_NOT_SUPPORTED` when a dedicated relationship collection
    receives standard list parameters that it deliberately does not accept.
- Error `details` identify `component`, expression `path`, requested field or
  operator, and the supported values. Error messages are human-readable; code
  and details are the machine contract.

## Money list ordering

- `amount_value` is the public amount sorter key. Do not invent a virtual
  `amount` sorter and do not expose `amount_scale` as an independent general
  sorter.
- While persisted Money contains `amount_value`, `amount_scale`, and
  `currency_code`, an `amount_value` sorter expands to
  `currency_code ASC`, `amount_scale ASC`, requested `amount_value` direction,
  then the stable ID tie-break. This is deterministic currency-and-scale
  grouping, not cross-currency value comparison.
- If Filter equality constrains both `currency_code` and `amount_scale`, order
  directly by `amount_value` and the stable ID tie-break. Do not scan all rows
  or calculate scaled decimal values merely to sort a list.
- An unconstrained or partially constrained amount sort succeeds with warning
  code `LIST_AMOUNT_SORT_GROUPED`. Its details report the effective sort order.
  Do not reject it with `LIST_COMBINATION_NOT_SUPPORTED`.
- Removing persisted `amount_scale` is an Entity/data-migration decision, not a
  Router or Mapper-only cleanup. It is valid only if every supported currency
  has one authoritative scale and existing values are normalized first. If
  that migration occurs, amount ordering becomes `currency_code ASC`, requested
  `amount_value` direction, then ID; a single-currency equality Filter removes
  the currency grouping.

## Standard list capabilities

- Transaction Fact supports Filters for `id`, `occurred_time`,
  `cash_direction`, `currency_code`, `amount_scale`, and `account_code`. Its
  ordinary Sorter is `occurred_time`; `amount_value` follows the Money ordering
  rules. Default to `occurred_time DESC` plus stable ID.
- Ledger supports Filters for `id`, `occurred_time`, `economic_type`,
  `cash_direction`, `currency_code`, `amount_scale`, and `account_code`. Its
  ordinary Sorter is `occurred_time`; `amount_value` follows the Money ordering
  rules. Do not expose `projection_version` or ID as public Sorters. Default to
  `occurred_time DESC` plus stable ID.
- Review supports Filters for `id`, `status`, `created_time`, and
  `updated_time`, and Sorters for `created_time` and `updated_time`. Exclude
  DEFAULT Reviews by the endpoint's fixed domain scope; never expose
  `exclude_behavior_code`. Default to `updated_time DESC` plus stable ID.
- Import File supports Filters for `id`, `source_type`, `file_format`, `status`,
  `created_time`, and `updated_time`, and Sorters for `id`, `created_time`, and
  `updated_time`. Do not advertise `institution_code` until it exists as a real
  supported field. Default to `id DESC`.
- Tag View supports Filters for `id`, `status`, `created_time`, and
  `updated_time`, and Sorters for `id`, `created_time`, and `updated_time`.
  Remove `include_archived`, name sorting, and system-name sorting. A PO
  inspection list does not silently hide archived records; Filter by status
  when desired. Default to `id DESC`.
- The standard Transaction Fact, Ledger, Review, Import File, and Tag View
  lists currently expose no Query fields. A supplied non-empty Query therefore
  returns `LIST_QUERY_NOT_SUPPORTED` until explicit search behavior is added.

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
- Every standard `GET /{object}/list` supports server-side pagination. Query,
  Filter, and Sorter are optional capabilities with object-specific
  whitelists; unsupported supplied capabilities fail with their dedicated
  codes. Never fetch an arbitrary large page and implement the canonical list
  query only in the browser.
- `query`, `filter`, and `sorter` are optional JSON query parameters. Each
  object API must whitelist supported fields, operators, value types, and sort
  directions at its boundary. Unknown or malformed expressions fail with
  `422`; never interpolate client-supplied names into SQL.
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
