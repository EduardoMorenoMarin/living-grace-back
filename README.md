# Living Grace Backend

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
  "username": "member",
  "password": "Example-password-123!",
  "first_name": "Example",
  "last_name": "Member",
  "phone": null,
  "birth_date": null
}
```

Email must be valid. Username and names must contain non-whitespace characters;
surrounding whitespace is trimmed. Passwords must contain 8–128 characters and
are not trimmed. Optional `birth_date` uses `YYYY-MM-DD`. Optional `memberships` adds ministry and
function selections using existing IDs. Unknown fields are
rejected, so callers cannot set status, IDs, timestamps, or a password hash.
Uniqueness checks use exact equality against the existing database columns;
email normalization is provided by Pydantic's `EmailStr`.

Success returns `201` and the user's public fields, including database-generated
ID, status, and timestamps. Neither the password nor its Argon2id hash is returned.
Duplicate email or username returns `409`, including insert-time unique conflicts.
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

Registration uses existing ministry/function IDs. No new columns, migration,
frontend, or AFTER OCP implementation are included.

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

Live membership status defaults to `PENDING`. Neither `main_instrument` nor
`technical_area` exists in the membership table. The proposed columns and migration
have been removed. No database schema changes are required or were applied.

### Request and response

Personal fields remain unchanged: email, username, password, first/last name,
optional phone and birth date. Add an optional `memberships` list with existing
`ministry_id` and `function_ids` values:

```json
{
  "email": "member@example.com",
  "username": "member",
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
Roles cannot be selected or assigned through public registration; no default role
is invented. IDs, statuses, timestamps, rehearsals, and attendance are not editable
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

`MembershipValidator` uses direct conditional checks for availability and function
ownership. There are no supported ministry-name-specific rules remaining, so no
artificial ministry branches were added. The former text-field rules, provider
registry, auto-discovery, and OCP abstractions are removed. Validation, persistence,
user mapping, and orchestration remain separate (SRP). `UserMapper` is the existing
plain personal-field mapper, previously named `UserFactory`, without an extensible
factory mechanism.

Any later real ministry-specific rule should use straightforward conditions in
this BEFORE branch. A future AFTER refactor must preserve the same API, validations,
relational persistence, role restrictions, and transaction behavior.

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
