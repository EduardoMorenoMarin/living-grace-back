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
are not trimmed. Optional `birth_date` uses `YYYY-MM-DD`. Unknown fields are
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
| `UserFactory` | Map registration fields and a hash into the existing `User` model |
| `UserRepository` | Own database queries, insertion, transactions, and persistence-error translation |
| `get_registration_service` | Assemble collaborators using the request's database session |
| Registration route | Map HTTP requests, responses, and errors |
| Pydantic schemas | Define request validation and the safe response contract |

The service receives concrete collaborators through constructor injection; no
interfaces or generic service framework are needed. The repository transaction
wraps the entire flow, including uniqueness checks, and commits only after the
safe response has been constructed. Existing commit/rollback ordering, error
messages, database defaults, and API behavior are preserved.

All original test cases and assertions are retained. Only the unit-test setup
changes to patch the repository at its new dependency-wiring location and use
its real transaction handling with the mocked session.

Registration creates only a user. There is no ministry/role/function assignment,
login, token generation, or database schema modification.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The default suite uses a mocked repository/session and exercises the real route,
service, validation, and password hashing. PostgreSQL tests are opt-in and use
the configured existing database. In PowerShell:

```powershell
$env:RUN_DATABASE_TESTS = "1"
python -m unittest discover -s tests -v
Remove-Item Env:RUN_DATABASE_TESTS
```

Database tests wrap requests in savepoints inside an outer transaction and roll
back all test accounts afterward. Identity sequences can advance despite rollback.
No tables are created, modified, or dropped by the test setup.
