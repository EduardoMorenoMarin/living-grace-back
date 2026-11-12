# Living Grace Backend

## Rehearsal creation AFTER LSP

Only `POST /rehearsals` is added. The existing ORM schema uses `name`,
`start_at`, `end_at`, and `tolerance_minutes`; there are no `title`,
`rehearsal_date`, or `location` columns. No database schema changes are needed.

Log in using `POST /auth/login` with the email address in `identifier` and `password`, then
send the returned access token as `Authorization: Bearer <access_token>`. In `/docs`, use
Authorize to enter that token and execute `POST /rehearsals` with an existing
ministry ID:

```json
{
  "ministry_id": 1,
  "name": "Worship Team Rehearsal",
  "description": "Weekly rehearsal for Sunday service",
  "start_at": "2026-10-03T19:00:00-05:00",
  "end_at": "2026-10-03T21:00:00-05:00",
  "tolerance_minutes": 0
}
```

Example 201 response (IDs/timestamps are illustrative; PostgreSQL may normalize
the timezone offset):

```json
{
  "id": 1,
  "ministry_id": 1,
  "created_by": 1,
  "name": "Worship Team Rehearsal",
  "description": "Weekly rehearsal for Sunday service",
  "start_at": "2026-10-04T00:00:00Z",
  "end_at": "2026-10-04T02:00:00Z",
  "tolerance_minutes": 0,
  "created_at": "2026-10-01T15:00:00Z",
  "updated_at": "2026-10-01T15:00:00Z"
}
```

The authentication dependency verifies the existing JWT signature, algorithm,
expiry and subject, then loads the user. The schema rejects unknown fields,
including client-supplied `id`, `created_by`, `created_at`, and `updated_at`.
Names must be nonblank, dates must include a timezone, `end_at > start_at`, and
tolerance must be a nonnegative PostgreSQL INTEGER (default 0). Description is
optional. Ministry IDs use the existing strict positive BIGINT convention.

The route passes the authenticated user's ID separately to `RehearsalCreator`.
The creator reuses `MembershipRepository.get_ministry` to check existence, builds
the existing ORM entity, and inserts through `RehearsalRepository`. The repository
flushes/refreshes database-generated fields, the service builds the response, and
the transaction commits before returning 201. Failures roll back. Authentication
failures return 401, missing ministries 404, invalid requests 422, and database
failures a sanitized 500. No existing ministry permission/membership authorization
check was found; registration selection validation is not an authorization policy.
Consequently, any authenticated user can create for an existing ministry.

The LSP refactor lives in `app/services/rehearsal_creation.py`:

- `RehearsalCreator` is the concrete base implementation.
  Its contract accepts schema-valid data with an optional description for an
  existing ministry and trusted user, returning the created rehearsal when
  storage succeeds.
- BEFORE: `RestrictedRehearsalCreator` required a nonblank description, rejecting
  valid base inputs. It strengthened a precondition and violated substitution.
- AFTER: `LoggingRehearsalCreator(RehearsalCreator)` delegates to the base and
  logs the created ID after a successful commit. It accepts exactly the same
  inputs, preserves the response and persistence errors, and does not mutate
  the request. Logging handler failures do not invalidate a committed creation.
  The artificial description restriction and its exception are removed.
- Production wiring supplies `LoggingRehearsalCreator` through the base-typed
  dependency. The route needs no subtype checks or special error handling.
  Omitting description, sending null, or sending blank text remains valid with
  either implementation. No fallback description is invented.

`test_lsp_contract_accepts_optional_description` verifies 201 and unchanged
description values for omitted, null, empty, whitespace, and populated descriptions.
The entire shared endpoint contract runs with both the base and production subtype:
actual persistence, authenticated ownership, invalid ministries, request validation,
token failures, foreign-key failure, and commit rollback/retry. Additional tests
verify that only successful creation is logged and logging failures preserve 201.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_rehearsals.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

By default rehearsal tests use disposable SQLite with foreign keys enabled.
To run the same contract against configured PostgreSQL, set
`$env:RUN_DATABASE_TESTS = "1"` before the focused command and remove it afterward
with `Remove-Item Env:RUN_DATABASE_TESTS`. PostgreSQL fixtures use outer transaction
rollback and savepoints without creating/changing tables; identity sequences may
advance. No attendance behavior or other rehearsal endpoints are implemented.

Initial Python and FastAPI setup for a university church ministry management project.

## Run locally

Use Python 3.10 or newer. From the repository root, create a virtual environment:

```sh
python -m venv .venv
```

Activate it in Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```sh
source .venv/bin/activate
```

Install dependencies and start the development server:

```sh
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

FastAPI's built-in documentation is available at http://127.0.0.1:8000/docs.
Registration is available at `POST /users/register`; requesting `/` returns 404.

## Structure

| Location | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI application entry point |
| `app/api/routes/` | HTTP route definitions |
| `app/core/` | Application and database configuration |
| `app/models/` | SQLAlchemy database models |
| `app/schemas/` | Pydantic request and response schemas |
| `app/repositories/` | Database access |
| `app/services/` | Business logic |
| `app/dependencies/` | Shared FastAPI dependencies |

The project uses a simple layered architecture. Future
routes should handle HTTP concerns, services should handle business logic, and
repositories should handle database access.

## Configuration

Create a local `.env` from `.env.example` if you do not already have one, and
replace the placeholders with your Supabase PostgreSQL connection details.
Use the `postgresql+psycopg://` scheme and URL-encode special characters in the
username and password. The template includes `sslmode=require` for TLS.
Keep `.env` private; it is ignored by Git.

`app/core/config.py` loads the required `DATABASE_URL` using `pydantic-settings`.
Environment variables override values in the repository-root `.env`, which is
located independently of the working directory. The URL uses `SecretStr` to mask
it in settings representations.

`app/core/database.py` provides the shared SQLAlchemy `engine` and `SessionLocal`
factory. Connections are opened on first use, with a 10-second connection timeout
and a connection health check when checked out of the pool.

`app/dependencies/database.py` provides `get_db` for future FastAPI dependencies.
It yields a session and closes it even when request processing raises an error.
There is no automatic commit; future write operations must explicitly commit.
Closing a session rolls back any uncommitted transaction.

The app starts without contacting PostgreSQL, but `DATABASE_URL` must be
configured because the registration route imports the database dependency.

To verify the configured connection without accessing or changing tables, run
this from the repository root in the activated virtual environment:

```sh
python -c "from sqlalchemy import text; from app.core.database import engine; connection = engine.connect(); print(connection.scalar(text('SELECT 1'))); connection.close(); engine.dispose()"
```

A successful connection prints `1`. Connection errors may contain connection
details; do not publish their raw output or your `.env` file.

## Registration (after SRP)

`POST /users/register` accepts:

```json
{
  "email": "member@example.com",
  "password": "Example-password-123!",
  "first_name": "Example",
  "last_name": "Member",
  "phone": null,
  "birth_date": null
}
```

Email must be valid. First and last names must contain non-whitespace characters;
surrounding whitespace is trimmed. Passwords must contain 8–128 characters and
are not trimmed. Optional `birth_date` uses `YYYY-MM-DD`. Optional `memberships` adds ministry and
function selections using existing IDs. Unknown fields are
rejected, so callers cannot set status, IDs, timestamps, or a password hash.
Uniqueness checks use exact equality against the existing database columns;
email normalization is provided by Pydantic's `EmailStr`.

Success returns `201` and the user's public fields, including database-generated
ID, status, and timestamps. Neither the password nor its Argon2id hash is returned.
Duplicate email returns `409`, including insert-time unique conflicts.
Invalid requests return `422`; persistence failures return a generic `500` and
roll back the transaction. Validation errors omit submitted values to avoid
echoing passwords.

Before SRP, `RegistrationService` owned uniqueness rules, password hashing,
`User` construction, transaction handling, database-error translation, and the
registration flow. Those responsibilities had independent reasons to change.

After SRP, responsibilities are separated as follows:

| Component | Responsibility / reason to change |
| --- | --- |
| `RegistrationService` | Sequence the registration use case and return its response |
| `RegistrationValidator` | Enforce registration uniqueness rules |
| `PasswordHasher` | Own the password hashing algorithm and configuration |
| `UserMapper` | Map registration fields and a hash into the existing `User` model |
| `UserRepository` | Own database queries, insertion, transactions, and persistence-error translation |
| `get_registration_service` | Assemble collaborators using the request's database session |
| Registration route | Map HTTP requests, responses, and errors |
| Pydantic schemas | Define request validation and the safe response contract |

The service receives concrete collaborators through constructor injection; no
interfaces or generic service framework are needed. The repository transaction
wraps the entire flow, including uniqueness checks, and commits only after the
safe response has been constructed. Existing commit/rollback ordering, error
messages, database defaults, and API behavior are preserved.

Existing user registration and login tests are retained. Membership tests cover
real relational persistence, selection validation, and atomic rollback.

Registration can also create ministry memberships and function assignments. Roles
cannot be selected during public registration. No database migration is required;
see the [registration baseline](#registration-before-ocp) for the verified
live table names and discrepancies in the supplied SQL export.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The default suite exercises routes, services, validation, password hashing, and
real membership persistence in disposable SQLite databases, alongside existing
mocked repository/session tests. PostgreSQL tests are opt-in and use the configured
existing database without schema changes. In PowerShell:

```powershell
$env:RUN_DATABASE_TESTS = "1"
python -m unittest discover -s tests -v
Remove-Item Env:RUN_DATABASE_TESTS
```

Database tests wrap requests in savepoints inside an outer transaction and roll
back all test accounts afterward. Identity sequences can advance despite rollback.
No tables are created, modified, or dropped by the test setup.


## Registration BEFORE OCP

The BEFORE baseline on `feat/register/before-ocp` uses existing ministry/function
IDs without new columns, a migration, or a frontend. The shared API below also
applies to AFTER; see [Registration AFTER OCP](#registration-after-ocp) for the
refactor on this branch.

### Verified database schema

Before changing registration, the configured PostgreSQL database was inspected in
read-only transactions using `information_schema.columns`, table listings,
`pg_constraint`, and `pg_indexes`. Its active schema is `public`. The supplied SQL
export differs from this live database:

| Issue | Supplied SQL | Verified live database and ORM mapping |
| --- | --- | --- |
| Function assignment table | `member_functions` | `ministry_member_functions` |
| Membership foreign key | Defines `memberId`, but indexes reference nonexistent `membershipId` | `ministry_member_id BIGINT` references `ministry_members.id` |
| Function foreign key | `functionId INT` | `function_id BIGINT` references `functions.id` |
| Role assignment table | `ministry_members_roles` | `ministry_member_roles` |
| Role foreign key | `roleId TEXT` references `roles.id INT` (incompatible types) | `role_id BIGINT` references `roles.id BIGINT`, with a valid FK |
| Generated IDs | Most tables show INT without identity/default generation | Relevant IDs are BIGINT identities |
| Column/enum names | Camel-case names and enums such as `membershipstatus` | Snake-case columns and enums such as `membership_status` |
| Update timestamp | `updatedAt NOT NULL` without a default | `updated_at NOT NULL DEFAULT now()` |
| Birth date | `birthDate TIMESTAMPTZ` | `birth_date DATE` |
| Assignment uniqueness | Unconditional unique indexes, including the invalid membership column reference | Partial unique indexes where `revoked_at IS NULL` |

The export's function indexes cannot be created as written because `membershipId`
is missing; its TEXT-to-INT role foreign key is also invalid as written. Neither
problem exists in the configured live database. Rehearsal-related differences are
outside registration and have not been modified.

The implementation keeps the verified live mappings rather than renaming tables or
executing the supplied DDL. If that export describes a different intended deployment,
verify that deployment separately before using it with this backend.

The database membership default remains `PENDING`; registration explicitly assigns
`PENDING` to every new membership without changing existing records. Neither `main_instrument` nor
`technical_area` exists in the membership table. The proposed columns and migration
have been removed. No database schema changes are required or were applied.

### Request and response

Personal fields include email, password, first/last name,
optional phone and birth date. Add an optional `memberships` list with existing
`ministry_id` and `function_ids` values:

```json
{
  "email": "member@example.com",
  "password": "Example-password-123!",
  "first_name": "Example",
  "last_name": "Member",
  "memberships": [
    {"ministry_id": 1, "function_ids": [10, 11]},
    {"ministry_id": 2, "function_ids": [20]}
  ]
}
```

IDs above are illustrative. Obtain real IDs from
`GET /ministries/registration-fields`: `function_options` contains ID/name pairs
for each active, non-deleted ministry's active, non-deleted functions. The existing
`functions` name list is retained. Unsupported `extra_fields` descriptors have
been removed.

Each membership is saved in `ministry_members`; each selected function is saved in
`ministry_member_functions` using the generated membership ID and existing function
ID. The HTTP 201 response includes the existing safe user fields and a `memberships`
array containing objects such as:

```json
{"id": 100, "ministry_id": 1, "status": "PENDING", "function_ids": [10, 11]}
```

Requests without memberships remain supported and return `"memberships": []`.
Function selection is optional, consistent with existing relationship constraints.
Roles cannot be selected by clients. Registration resolves the active, non-deleted
GLOBAL USER role by name and assigns it to every new membership in the same
transaction. Missing default roles or failed assignments roll back registration.
Assignment IDs are explicit random positive BIGINTs because the verified
`ministry_member_roles.id` column has no database default. Primary-key collisions
fail registration safely; no sequence or schema change is required.
IDs, statuses, timestamps, rehearsals, and attendance are not editable
registration fields.

Validation rejects duplicate selections, malformed/nonpositive/out-of-range IDs,
inactive or deleted references, and functions belonging to another ministry.
Unknown fields, including `main_instrument`, `technical_area`, roles, and lifecycle
fields, return 422. IDs must be strict integers within BIGINT range.

### Transaction and BEFORE OCP design

`RegistrationService` coordinates uniqueness checks, membership validation, user
creation, and membership/function inserts within one transaction.
`MembershipRepository` uses the same session and only flushes; it never commits.
The safe response is assembled before the single final commit. A validation,
foreign-key, persistence, or commit failure rolls back already-flushed users,
memberships, and assignments. Existing 409 conflicts and sanitized 500 errors
remain supported.

In the BEFORE baseline, `MembershipValidator` uses direct conditional checks for
availability and function ownership. There are no supported ministry-name-specific rules remaining, so no
artificial ministry branches were added. The former text-field rules, provider
registry, auto-discovery, and OCP abstractions are removed. Validation, persistence,
user mapping, and orchestration remain separate (SRP). `UserMapper` is the existing
plain personal-field mapper, previously named `UserFactory`, without an extensible
factory mechanism.

A new ministry-specific requirement in BEFORE would require editing that validator.
The AFTER implementation below preserves the same API, validations, relational
persistence, role restrictions, and transaction behavior while adding an extension
point for additional requirements.

### Relevant files and verification

The changes are in the user/membership/ministry schemas, registration and membership
services, repositories, dependency wiring, route error handling, and catalog mounting.
The membership model is restored to its original columns; function/role ORM mappings
remain unchanged. `tests/test_ministries.py` and `tests/test_membership_registration.py`
cover the catalog, relational validation, persistence, and rollback.

The membership contract runs against disposable SQLite databases with foreign keys
enabled by default and against PostgreSQL when `RUN_DATABASE_TESTS=1`. Coverage
includes actual foreign-key failure during assignment, late persistence failure,
commit failure, retry after rollback, and rejected role/extra-field submissions.
PostgreSQL tests use the existing schema and roll back fixtures using savepoints
within an outer transaction; identity sequences may advance despite rollback.


## Registration AFTER OCP

This branch (`feat/register/after-ocp`) makes additional membership validation
extensible without changing `RegistrationService` or any database mappings.

### Analysis and scope

The BEFORE code has no ministry-name dispatch or ministry-specific registration
requirements. An `if` statement is not inherently an OCP violation. The availability,
function ownership, duplicate-selection, role rejection, and uniqueness checks are
mandatory shared invariants. Replacing each with a Strategy would add unnecessary
complexity and could make those invariants accidentally optional.

The actual extension limitation is in `MembershipValidator`: another membership
requirement previously required editing its validation method. AFTER adds a small
rule interface and an injected sequence for these variable requirements. Existing
invariant checks remain mandatory and keep their original order and error messages.
The catalog, HTTP schemas/endpoints, password handling, persistence services, and
transaction boundaries need no refactoring for this extension point.

### Design

| Component | Responsibility |
| --- | --- |
| `MembershipRule` in `app/services/membership_rules.py` | Protocol with `validate(ministry, functions)` for one additional requirement |
| `MembershipValidator` | Load and validate references, then invoke each injected rule for each membership |
| `get_membership_rules()` in `app/dependencies/registration.py` | Composition point for optional rule implementations; returns an empty tuple in production |
| `RegistrationService` | Existing orchestration, unchanged and unaware of concrete rules |

Rules receive the existing ministry and selected function objects after availability
and ownership checks pass. They inspect these objects without mutating them, writing
to the database, or committing. To reject a selection, a rule raises
`RegistrationValidationError` with a user-safe message. All rules run in injection
order, stopping at the first rejection. The existing transaction catches this error,
rolls back, and the endpoint returns 422. Rule validation still precedes password
hashing and all registration inserts.

To add a real requirement, implement `MembershipRule` and supply the implementation
through `get_membership_rules()` (or the application's dependency configuration).
Only composition wiring changes; the core service, validator loop, and existing
rules remain unchanged. There is no provider registry, dynamic discovery, factory,
new schema field, or ministry switch in the workflow. The extension is deliberately
for additive validation, not alternative persistence or registration workflows.

### Test-only example

`RequireFunctionForMinistry` in `tests/test_membership_rules.py` models a hypothetical
requirement that one selected ministry must have at least one selected function:

```python
class RequireFunctionForMinistry:
    def __init__(self, ministry_id):
        self.ministry_id = ministry_id

    def validate(self, ministry, functions):
        if ministry.id == self.ministry_id and not functions:
            raise RegistrationValidationError(
                "Select at least one function for this ministry."
            )
```

Tests inject it for their Alabanza fixture through FastAPI's dependency override:

```python
app.dependency_overrides[get_membership_rules] = lambda: (
    RequireFunctionForMinistry(alabanza_id),
)
```

The fixture restores the override after each test. This rule is not enabled in
production: omitting functions remains valid exactly as in BEFORE. The example
adds no columns or real business requirement. Its local conditional belongs to the
new rule implementation; adding it requires no edits to `RegistrationService` or
`MembershipValidator`.

### Behavior and verification

Existing registration contract tests are retained unchanged. The additional rule
contract runs using the same disposable SQLite and rollback-protected PostgreSQL
fixtures. It verifies acceptance/rejection for the selected ministry, no effect on
other ministries, no bypass of mandatory function validation, and no partial user
when a later membership fails the new rule. Unit tests verify rule order, early
exit, validated inputs, and no redundant database lookups.

User creation, optional memberships/functions, `PENDING` membership status, and
public role rejection are preserved. The existing foreign-key, persistence-failure,
and commit-failure rollback tests still apply. No migration is required. Live
function assignments retain the verified `ministry_member_functions` mapping noted
in the schema audit above.

The OpenAPI schema is identical before and after the refactor. Run the suite using
the commands in the Tests section; PostgreSQL coverage remains opt-in and uses
rollback-protected fixtures without schema changes.


### Ministry membership status management

Both endpoints require `Authorization: Bearer <access_token>` and the existing
GLOBAL ADMIN dependency, which checks current database privileges. Registration
creates PENDING memberships with automatic GLOBAL USER role assignment and ACTIVE
user accounts. Registration rejects membership status overrides. Existing memberships
are unchanged. Approval makes the existing USER assignment effective without inserting
another assignment; JWT claims and login responses are unchanged.

`GET /ministries/1/members?status=PENDING` lists pending members. Supported filters
are `PENDING`, `ACTIVE`, and `INACTIVE`; omitting the filter returns only `ACTIVE`.
There is no unfiltered mode. The existing response gains a membership `status`:

```json
{
  "ministry_id": 1,
  "members": [{
    "ministry_member_id": 42,
    "user_id": 12,
    "first_name": "Ana",
    "last_name": "Garcia",
    "status": "PENDING",
    "functions": ["Guitar"]
  }]
}
```

Approve using `PATCH /ministries/1/members/42/status` with only:

```json
{"status": "ACTIVE"}
```

The successful response is:

```json
{"ministry_id": 1, "ministry_member_id": 42, "status": "ACTIVE"}
```

All transitions between PENDING, ACTIVE, and INACTIVE are supported, including
reactivation and returning a membership to PENDING. Send the desired status in the
same request body. Same-status updates succeed without changing updated_at; actual
changes update updated_at. Removing the last effective GLOBAL ADMIN privilege returns
409. Administrators cannot activate their own non-ACTIVE memberships (409).
Status changes serialize on the global ADMIN role rows to protect concurrent
demotions. PENDING and INACTIVE memberships grant no ministry access or global roles.
Invalid statuses, extra body fields, and invalid path IDs return 422. Missing,
wrong-ministry, or deleted memberships and unavailable users/ministries return 404.
Users and ministries must be ACTIVE and not deleted; listings apply the same
eligibility checks. Authentication failures return 401, and authenticated users
without GLOBAL ADMIN privileges receive 403. Database failures return a generic 500
and roll back the transaction. Approval locks eligibility rows through commit and
preserves membership identity, function assignments, role assignments, and attendance.

Frontend: use the returned `ministry_member_id` in the approval URL, send the existing
bearer token, and refresh both PENDING and ACTIVE lists after a successful approval.
Handle 409 by preserving an effective global administrator; never infer backend permission from a
frontend role check. No schema migration or Supabase change is required.

Local workflow coverage:

```bash
.venv/bin/python -m unittest discover -s tests -p test_ministry_members.py -v
.venv/bin/python -m unittest discover -s tests -v
```

These commands use isolated local fixtures by default. PostgreSQL integration tests
remain opt-in; do not enable them against Supabase automatically. PostgreSQL row-lock
behavior is not exercised by the SQLite membership tests.

### Create a rehearsal attendance QR session

`POST /rehearsals/{rehearsal_id}/qr-sessions` accepts only `opens_at` and
`expires_at`, both timezone-aware ISO timestamps. It returns 201 with `id`,
`rehearsal_id`, `check_in_url`, and the two timestamps normalized to UTC.
`FRONTEND_ORIGIN` configures the frontend base URL; the link appends
`/check-in?token=...`. The frontend renders the QR image.

Authorization follows existing rehearsal administration: a current GLOBAL ADMIN
role plus active membership in the rehearsal's active ministry. Being the creator
or having only a ministry-scoped role grants no additional access. Unauthorized
roles receive 403; missing or inaccessible rehearsals receive 404. Invalid windows
receive 422, existing non-revoked sessions receive 409, and persistence failures
receive a sanitized 500. An already-open window is allowed if its expiration is
still in the future; no additional relationship to rehearsal start/end is imposed.

The implementation maps the existing `rehearsal_qr_sessions` table and runs no
DDL or migrations. A rehearsal row lock serializes creation, and the existing
partial unique index remains the final guard against concurrent writers. Expired
but non-revoked rows still block creation, matching that index.

Tokens use `secrets.token_urlsafe(32)` (256 random bits). Only their SHA-256 hashes
are persisted. The successful creation response is the only opportunity to obtain
the original token, and is marked `Cache-Control: no-store`. Application code does
not log it; deployment response-body logging should also exclude this response.

The GET endpoint returns session metadata only, omitting the check-in URL. The
administrator's frontend must retain the creation URL for redisplay. If it is lost,
explicitly revoke the session and create a replacement; there is no way to
reconstruct the token from its hash. No plaintext column or encryption is used.

Run the isolated QR contracts with:
` .venv/bin/python -m unittest discover -s tests -p 'test_rehearsal_qr_session.py' `.
The tests exercise the real unique index after a simulated lost precheck race;
SQLite does not verify PostgreSQL row-lock scheduling.

### QR attendance check-in

`POST /attendance/check-in` requires a JWT and a body containing only
`{"token": "QR_TOKEN"}`. Identity comes from the JWT, and timestamps/statuses
are determined by the server. No administrative role is required: the current
user and rehearsal ministry must be active and not deleted, and the user must
have active, non-deleted membership in that ministry.

The response contains `rehearsal_id`, `attendance_status`, and UTC `arrival_at`.
New attendance rows return 201; updates to existing NULL or ABSENT placeholders
return 200. A recorded arrival or an ON_TIME, LATE, or JUSTIFIED status returns
409 without modifying the row. ON_TIME includes the exact boundary
`start_at + tolerance_minutes`; later arrivals before `end_at` are LATE.
Check-in rejects `now >= end_at` even if the QR session remains open. Both
expiration and rehearsal completion are revalidated after locks and after the
write; crossing either boundary rolls back the transaction.

The endpoint hashes the submitted token with SHA-256. Unknown, revoked,
not-yet-open, expired, or missing-rehearsal sessions return the same generic 400.
The validity window is `opens_at <= server_now < expires_at`. Ineligible users
or ministries return 403, invalid/missing JWTs return 401, malformed bodies
return 422, and database failures return a sanitized 500. Responses do not echo
tokens, and the request schema uses a secret value to redact representations.
Deployments must exclude request bodies from access/trace logging for this route.

Transactions lock the rehearsal before the QR row, then the ministry, user,
membership and attendance. These locks refresh database state after waits and
prevent revocation or eligibility changes during recording. Time is sampled
after all lock waits and checked again after the write, with rollback if the
session expired during recording. Rehearsal locking serializes check-ins for a
rehearsal; the existing attendance pair unique constraint also guards competing
inserts by other writers. No database schema changes are needed.

Run ` .venv/bin/python -m unittest discover -s tests -p 'test_attendance_check_in.py' `.
The existing opt-in `REHEARSAL_CONCURRENCY_DATABASE_URL` scratch PostgreSQL
configuration enables real concurrent insert, ABSENT-update, revocation-wait,
and expiration-wait tests. Without it those tests are skipped; SQLite contracts
cover validation, SQL constraint fallback and rollback across expiration but do
not demonstrate PostgreSQL lock scheduling.


### Retrieve or revoke the rehearsal QR session

Both endpoints require the same current GLOBAL ADMIN role and active membership
in the rehearsal's active ministry as QR creation. Rehearsal ownership alone is
insufficient. Missing/invalid JWTs return 401, insufficient global privileges
return 403, and missing or inaccessible rehearsals return 404.

`GET /rehearsals/{rehearsal_id}/qr-sessions/active` returns 200 with only `id`,
`rehearsal_id`, `opens_at`, `expires_at`, and `created_at`. Timestamps are UTC.
The session must be non-revoked and satisfy `opens_at <= server_now < expires_at`;
missing, scheduled, expired and revoked sessions return 404. GET performs only
reads, does not lock rows or update records, and uses `Cache-Control: no-store`.
Metadata reflects committed state at the time of the read; a concurrent revoke
may commit immediately afterward. The check-in endpoint always revalidates.

`DELETE /rehearsals/{rehearsal_id}/qr-sessions/active` sets the non-revoked
session's `revoked_at` to current UTC and returns 204 with no body. It also allows
revoking scheduled or expired sessions, freeing the unique non-revoked slot for
replacement creation. It preserves the row and returns 404 if no revocable
session exists, including repeated revocation with no intervening replacement.
Read or persistence failures return sanitized 500 responses.

Revocation locks the rehearsal then its non-revoked QR row, refreshing state
after waits and holding locks until commit. This matches creation/check-in lock
ordering. Two revocations of the same session yield one success and one 404;
creation after a completed revoke can issue a new token. A check-in that waits
behind revocation sees the committed revocation and rejects the old token. A
check-in that acquires the locks first may complete before revocation takes
effect. DELETE targets the non-revoked session when it acquires the locks; a
replacement created before that point can therefore be the session revoked.

No token or token hash appears in GET responses, and the metadata query does not
select the hash. Raw URLs remain available only in successful creation responses.
No schema changes or token reconstruction are introduced.

Run ` .venv/bin/python -m unittest discover -s tests -p 'test_rehearsal_qr_management.py' `.
The existing opt-in scratch PostgreSQL setting enables tests for concurrent
revocations, replacement creation waiting behind revocation, check-in waiting
behind revocation, and metadata reads during revocation. These tests are skipped
without `REHEARSAL_CONCURRENCY_DATABASE_URL`.

### Admin-assisted password reset

`POST /users/{user_id}/reset-password` requires the existing global ADMIN role.
It returns HTTP 200 with `temporary_password` and `must_change_password: true`.
The temporary password is generated with `secrets.token_urlsafe(24)` and returned
only after the transaction commits; only its Argon2 hash is stored. Deleted or
missing accounts return 404, inactive accounts return 409, and database failures
return a sanitized 500 after rollback. The response uses `Cache-Control: no-store`.

The User model maps the existing `users.must_change_password` boolean column.
No migration is needed or supplied. Login now includes `must_change_password`
alongside `access_token` and `token_type`. A user with this flag can log in and
call `POST /auth/change-password`, using the temporary password as
`current_password`. Other protected endpoints return 403 (`Password change
required.`). A successful password change clears the flag and replaces the hash
in the same transaction. Log in again afterward to obtain a new access token.
Login and password-change responses also use `Cache-Control: no-store`.

Access tokens now require a `pwdv` claim: an HMAC of the user ID and current
password hash, keyed with the JWT signing secret and a credential-specific
prefix. The database hash itself is never included in the token. Every
JWT-authenticated request checks this claim against the current database value.
Resetting or changing a password invalidates previously issued tokens, including
temporary-password tokens after the final change. Tokens issued before this
claim was introduced are rejected; deployment requires users to log in again.
This replaces the previous behavior where tokens remained usable until expiry.

There is no independent per-token revocation store. The password-bound claim
revokes all tokens when the password hash changes. Requests that already passed
authentication before a reset commits cannot be retroactively cancelled.
Temporary passwords have no separate expiry; another reset replaces them.

`tests/test_reset_password.py` covers reset authorization and eligibility,
temporary login, forced-change restrictions, token invalidation, and transaction
rollback. Run `.venv/bin/python -m unittest discover -s tests` for regressions.

### Role-based dashboard

`GET /dashboard/me` uses the existing JWT authentication and global USER/ADMIN
eligibility checks. Mandatory password changes block access with 403. Role selection
uses only the server-side `RehearsalViewer.is_admin` permission result.

Optional `ministry_id` limits attendance and rehearsal sections to an active,
nondeleted membership in an active, nondeleted ministry. An unavailable or
unrelated ministry returns 404; malformed IDs return 422. The profile continues
to list all eligible active memberships and their active, unrevoked functions.

The regular-member response contains `selected_ministry_id`, `profile`, `monthly_attendance`,
`recent_attendance`, `next_rehearsal`, and `upcoming_rehearsals`, unchanged. Admins
receive distinct `ministry_attendance` and `recent_rehearsals` fields in place of
`monthly_attendance` and `recent_attendance`. Admin fields are absent for members. See
`app/schemas/dashboard.py` for the full response contract.

Configure `APP_TIMEZONE` with an IANA timezone name (default `America/Lima`);
invalid names fail settings validation. The summary always covers the current
calendar month in that timezone, based on rehearsal `start_at`. Boundaries are
converted to UTC for database comparisons. Completed means `end_at <= now`.
The total includes all completed assigned rehearsals in the month, including
historical JUSTIFIED records. Statuses use the shared effective calculation in
`app/core/attendance.py`; a completed assignment with NULL status and no arrival
is reported ABSENT without updating its database row.

Attendance rate is `(ON_TIME + LATE) / (ON_TIME + LATE + ABSENT) * 100`.
Punctuality rate is `ON_TIME / (ON_TIME + LATE) * 100`. Historical JUSTIFIED
and pending results contribute to neither denominator. Rates are rounded to two decimals
and return null for an empty denominator.

Recent activity contains at most five completed assigned rehearsals across all
dates, including effective absences and historical JUSTIFIED records, ordered
by start time and ID descending.
`rehearsal_date` is the rehearsal start date in `APP_TIMEZONE`. Upcoming contains
at most five rehearsals with `start_at >= now`, ordered by start time and ID
ascending. Ongoing rehearsals are excluded. `next_rehearsal` equals the first
upcoming card, or null; lists are empty when no results qualify. Members see only
their assignments; admins see all rehearsals in their visible ministries.

The repository reuses role-based rehearsal visibility and SQL metadata queries,
uses SQL aggregation for counts, and bounds both history and upcoming queries.
The normal request uses six SELECTs including JWT user lookup and authorization;
query count does not grow with attendance history. No database schema changes
are needed. Attendance rows remain the existing representation of participant
assignments, including rows created by QR check-in; no separate preassignment
provenance is available. Member history excludes inactive memberships; all dashboard
sections exclude ministries unavailable to the viewer.

For admins, `ministry_attendance` reports:

- `month`, `timezone`: the same current local calendar month used for members.
- `total_rehearsals`: rehearsals starting in that month, including future and ongoing ones.
- `completed_rehearsals`: the subset with `end_at <= now`.
- `total_members`: distinct users with active, nondeleted memberships in the scoped
  ministries, independent of assignments and month. A person in several ministries
  counts once when no ministry filter is supplied.
- `total_attendance_records`: participant assignment rows for completed rehearsals
  starting in that month. Counts never multiply by ministry functions or roles.
- `on_time`, `late`, `absent`: effective status counts over those records.
  A member with no assignment row contributes no record and is never inferred
  absent. Rehearsals with zero assignments still count as rehearsals, with zero
  status counts. Historical JUSTIFIED records remain in participant/record totals
  but not these three status counts or rate denominators.
- `attendance_rate`: `(on_time + late) / (on_time + late + absent) * 100`.
- `punctuality_rate`: `on_time / (on_time + late) * 100`.

Both rates exclude historical JUSTIFIED and pending records from their denominators,
round to two decimals, and return null when the denominator is zero. Monthly
rates use pooled record counts, rather than averaging rehearsal percentages.
Existing assignment records remain historical evidence even if the participant's
membership later becomes inactive or deleted, matching admin rehearsal detail
reads. Records must belong to a member of the rehearsal's ministry.

`recent_rehearsals` contains at most five completed rehearsals across all dates,
ordered by `start_at DESC, id DESC`, including zero-participant rehearsals and
those with missing check-ins reported as ABSENT. Each item includes `rehearsal_id`, `name`,
`ministry`, local `rehearsal_date`, `total_participants`, the three status counts,
and `attendance_rate` using the same formula for that rehearsal's records.

Every admin query applies the existing active ministry membership visibility
rule before aggregation or limits. With `ministry_id`, only that ministry is
counted; without it, only the admin's visible ministries are combined. The
profile continues to list all eligible memberships regardless of the filter.

Run `.venv/bin/python -m unittest discover -s tests -p test_dashboard.py -v`
for dashboard contracts and `.venv/bin/python -m unittest discover -s tests`
for the regression suite.


### Effective attendance and API compatibility

All individual attendance reads (member rehearsal lists/details, admin participant
lists, participant details returned by rehearsal updates, and dashboard recent
attendance) and dashboard SQL summaries share `app/core/attendance.py`:

| Stored status | Recorded arrival | Effective response |
| --- | --- | --- |
| Explicit ON_TIME, LATE, ABSENT | Any | Preserve recorded status |
| Historical JUSTIFIED | Any | Preserve JUSTIFIED as a legacy-only read value |
| NULL | At or before start + tolerance | ON_TIME |
| NULL | After start + tolerance | LATE |
| NULL | None, before end_at | null (pending) |
| NULL | None, at or after end_at | ABSENT |
| No assignment row | None | No participant; never synthesize an absence |

Recorded arrivals with NULL statuses are classified by tolerance even for legacy
arrivals after completion; this preserves evidence of arrival without rewriting
history. New QR check-ins cannot occur at or after `end_at`. A NULL pending value
is not an enum member. No UNRESOLVED enum exists. Creation and new assignments
still store NULL status and arrival until check-in. Rehearsal completion is a
read-time comparison, `end_at <= now`, not a stored state or scheduled write.

Breaking summary contract change: `justified` and `unresolved` fields have been
removed from `monthly_attendance`, `ministry_attendance`, and `recent_rehearsals`.
Totals still include every eligible assignment record; historical JUSTIFIED means
the three displayed status counts can sum to less than the total. Individual
responses retain the existing JUSTIFIED literal solely for historical compatibility.
Do not relabel it as pending, ABSENT, or ON_TIME. Clients must tolerate that legacy
read value until a separate data migration policy is agreed. There is no public
status-writing API; check-in only writes ON_TIME or LATE and its response schema
allows only those values. The database enum remains unchanged.

Example individual attendance fragments:

```json
{"attendance_status": null, "arrival_at": null}
```

```json
{"attendance_status": "ABSENT", "arrival_at": null}
```

```json
{"attendance_status": "JUSTIFIED", "arrival_at": null}
```

Example personal summary (one on-time check-in and one inferred absence):

```json
{
  "month": "2026-11",
  "timezone": "America/Lima",
  "total_completed_assigned_rehearsals": 2,
  "on_time": 1,
  "late": 0,
  "absent": 1,
  "attendance_rate": 50.0,
  "punctuality_rate": 100.0
}
```

Reads never persist effective statuses. No migrations, backfills, schedulers, or
new endpoints are required. Existing ministry visibility, membership checks,
participant totals, and assignment/deletion protections remain unchanged.

### Notifications: list endpoint

`GET /notifications` requires the existing JWT `get_current_user` dependency,
including stale-token checks and the mandatory-password-change restriction.
It needs no global role: even admins can read only their own notifications.
The server derives ownership from the authenticated user; `user_id` is not a
supported query parameter and cannot select another user's records.

Query parameters:

- `limit`: default 20, range 1–100.
- `offset`: default 0, nonnegative BIGINT range.
- `is_read`: optional boolean; omit for all notifications.

Items are ordered by `created_at DESC, id DESC`. `total` counts all matching
notifications for the authenticated user and optional filter, before pagination.
An offset beyond the results returns an empty `items` list with the same total.
The response omits the internal owner ID and normalizes timestamps to UTC.
Listing does not mark items as read; responses use `Cache-Control: no-store`.

Example `GET /notifications?limit=20&offset=0&is_read=false`:

```json
{
  "total": 1,
  "limit": 20,
  "offset": 0,
  "items": [
    {
      "id": 42,
      "type": "REHEARSAL_ASSIGNED",
      "title": "Rehearsal assignment",
      "message": "You have been assigned to a rehearsal.",
      "resource_type": "rehearsal",
      "resource_id": 301,
      "is_read": false,
      "created_at": "2026-11-15T17:00:00Z"
    }
  ]
}
```

Invalid pagination/filter values return 422; authentication failures return 401;
mandatory password change returns 403; notification query failures return a
sanitized 500 (`Unable to retrieve notifications.`). Two notification SELECTs
(count and page) keep query count constant. Offset pagination is deterministic
for unchanged data; new inserts can shift later pages.

#### Notifications database migration

This repository has no Alembic configuration or migration runner. Apply the
versioned PostgreSQL SQL migration `migrations/001_create_notifications.sql`
once to the existing schema before enabling this endpoint. With a PostgreSQL
connection URI in `PGDATABASE`, use:

```bash
psql -v ON_ERROR_STOP=1 -f migrations/001_create_notifications.sql
```

Use a PostgreSQL URI (`postgresql://...`), not SQLAlchemy's
`postgresql+psycopg://...` URI, with psql. The migration is transactional, targets
the connection's current schema (normally `public`), requires the existing
`users(id BIGINT)` table, and intentionally fails if `notifications` already
exists. It does not alter existing tables. No DDL runs at application startup.

`notifications` uses a BIGINT identity primary key, a required `user_id` foreign
key to `users(id)` with `ON DELETE CASCADE`, required text `type`, `title`, and
`message`, nullable text `resource_type`, nullable BIGINT `resource_id`, required
`is_read BOOLEAN DEFAULT false`, and required `created_at TIMESTAMPTZ DEFAULT
now()`. Resources are optional polymorphic references, not foreign keys; future
resource endpoints must apply their own authorization. Type strings remain
extensible without introducing a PostgreSQL enum. Composite indexes
`ix_notifications_user_created_at_id` and
`ix_notifications_user_is_read_created_at_id` support ownership, filtering,
and newest-first pagination.

Only listing is implemented in this step. There are no event producers, unread
count endpoint, mark-read endpoints, or WebSocket connections. Tests create rows
directly; no production fixtures or notifications are inserted automatically.

Run `.venv/bin/python -m unittest discover -s tests -p test_notifications.py`.
To additionally execute the SQL migration and verify defaults, indexes, foreign
keys, and ORM queries on PostgreSQL, set `NOTIFICATIONS_TEST_DATABASE_URL` to a
scratch SQLAlchemy PostgreSQL URL. That opt-in test creates and removes only a
unique test schema; it never falls back to the application's `DATABASE_URL`.
