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
- Translate domain errors consistently at the HTTP boundary. Do not duplicate
  route-local wrappers for the same error contract.

## REST contract

- Canonical paths use
  `/paam/{module}/{version}/{object}/{resource_id}`. Object names are singular.
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
- Path parameters identify resources. Filters, sorting, and pagination use
  query parameters. Write input uses a JSON request body when a body is needed.
- Canonical paged list requests use `page` with default `1` and `page_size` with
  default `20` and maximum `100`. Their direct response contains `items`,
  `total`, `page`, and `page_size`. Do not wrap the page in a generic
  `status/message/body` envelope.
- Compatibility routes retain their existing request defaults and response
  shapes until their callers migrate.
