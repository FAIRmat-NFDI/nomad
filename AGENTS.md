# AGENTS

## Domain-driven backend design

- Organize new backend features as bounded contexts with explicit layers: `domain.py` for business rules and invariants, `application.py` for use-case orchestration, `ports.py` for dependency interfaces, infrastructure adapters such as `mongo_repository.py`, and `bootstrap.py` as the composition root.
- Keep the domain layer independent of frameworks and infrastructure. Domain code must not import FastAPI, MongoEngine/Beanie/PyMongo, Temporal clients, or transport-specific schemas.
- Application services depend on ports (`Protocol` interfaces), never concrete database or external-service adapters. Instantiate and wire adapters only in the bounded context's composition root.
- Prefer a single application service class for a cohesive set of use cases, rather than separate sync and async service classes (for example, `ActionService`, not `SyncActionService` and `AsyncActionService`). Use unprefixed synchronous methods such as `get_status()` and `a_`-prefixed asynchronous methods such as `a_get_status()`.
- Add only the execution modes a feature needs: a synchronous-only service such as `PATService` does not need async counterparts. Where both modes exist, inject the appropriate sync/async ports into the same service and share domain rules and preparation logic. Async methods must use non-blocking I/O; do not implement them by directly calling blocking synchronous methods. Wire a single service instance in `bootstrap.py`.
- Apply this naming convention to internal services without changing documented public plugin API names, signatures, or return types; public entry points may delegate to the service.
- Keep API routers thin: authenticate/authorize, validate transport data, call an application service, and translate domain/application errors into HTTP responses. Do not put persistence queries or business invariants in routers.
- Keep persistence models and API Data Transfer Objects (DTOs, such as request/response schemas) at the boundaries; translate them to persistence-independent domain records. Repository ports should expose domain language rather than database concepts.
- Put state transitions and reusable invariants in the domain layer. Use application services for workflows spanning repositories or external systems, and make race-sensitive transitions atomic in an adapter behind a purpose-specific port method.
- Add fast unit tests for domain and application behavior with in-memory/fake ports, plus focused integration tests for each infrastructure adapter. Preserve compatibility facades only during migrations; new code must use the layered interfaces.

## Security awareness

- Proactively flag every security issue you notice while reviewing or modifying the code, even when it is unrelated to the current task. Never silently ignore an out-of-scope security concern.

## Sensitive data handling

- Use Pydantic's `SecretStr` (or `SecretBytes` for binary values) instead of plain `str` for passwords, tokens, API keys, private keys, and other secrets in configuration and data models.
- Keep secrets wrapped for as long as possible. Call `get_secret_value()` only at the integration boundary where the underlying library requires the raw value.
- Do not include raw secrets in logs, exceptions, serialized responses, or diagnostic output.

## Datetime handling

- Prefer `nomad.common.now()` for current timestamps (returns UTC and can be mocked in tests) instead of calling `datetime.now(...)` directly.
- MongoDB does not natively store timezone information. Treat stored datetime values as UTC+0, and re-attach timezone information in the ORM layer. Use `nomad.mongo.fields.UTCDateTimeField` for MongoEngine models so UTC timezone info is consistently restored on reads/writes.
- API responses should be RFC3339 compliant; use the Pydantic field `nomad.models.common.UTCDateTime`.
- Avoid manual timezone handling in application code when ORM/API field types above can enforce it.

## Storage Module

- The existing architecture adopts a two-tier storage approach: the main storage uses a local filesystem; the auxiliary
  storage uses an optional remote filesystem.
- The auxiliary storage `NOMADFileSystem` shall be used as a proxy sitting between user code and the underlying
  filesystem, interactions shall be done via `NOMADFileSystem.target_fs` which may fall back to the local filesystem if
  the auxiliary storage is not configured.
- User code shall be filesystem-agnostic, and should not directly interact with files via for example `os` module.
  Interactions shall be done via `fsspec` abstraction layer.
- `files.FSUtility` provides a set of utility functions for opening files in binary mode, as H5 files, as zip/tar
  archives, etc. Additional utility functions can be added as needed.
- User code (for example, `files.PathObject`) shall handle nominal paths. Absolutely avoid mixing nominal paths and real
  paths in user code.
- The `NOMADFileSystem.real_destination` can be used to resolve the actual path on the corresponding filesystem.