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
There are no application endpoints yet; requesting `/` returns 404.

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

The minimal app still starts without contacting PostgreSQL. Database settings
are loaded when the database module or dependency is imported; at that point,
`DATABASE_URL` must be configured.

To verify the configured connection without accessing or changing tables, run
this from the repository root in the activated virtual environment:

```sh
python -c "from sqlalchemy import text; from app.core.database import engine; connection = engine.connect(); print(connection.scalar(text('SELECT 1'))); connection.close(); engine.dispose()"
```

A successful connection prints `1`. Connection errors may contain connection
details; do not publish their raw output or your `.env` file.

This setup includes no domain models, repositories, services, application routes,
authentication, or migrations. It does not create or modify database tables.
