"""Review tests: production login code is intentionally left unchanged.

The missing production router is recorded as an expected failure. Handler tests
mount the existing router in an isolated app, not in the production application.
PostgreSQL tests opt in through RUN_DATABASE_TESTS=1 and roll back their fixtures.
"""

import os
import secrets
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
# Generated only for this test process; never use a real signing secret in tests.
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))

import jwt
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import event, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routes.auth import router
from app.core.config import Settings, settings
from app.core.database import engine
from app.main import app, request_validation_error
from app.models.enums import UserStatus
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.password_hasher import PasswordHasher


def isolated_login_app():
    application = FastAPI()
    application.include_router(router)
    application.add_exception_handler(RequestValidationError, request_validation_error)
    return application


def test_account():
    token = uuid4().hex
    return {
        "email": f"login-{token}@example.com",
        "username": f"login_{token}",
        "password": "Login-test-password-123!",
        "first_name": "Login",
        "last_name": "Test",
    }


class LoginTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.key = secrets.token_urlsafe(48)
        stack.enter_context(patch.object(settings, "JWT_SECRET_KEY", SecretStr(self.key)))
        stack.enter_context(patch.object(settings, "JWT_ALGORITHM", "HS256"))
        stack.enter_context(patch.object(settings, "JWT_EXPIRE_MINUTES", 60))
        self.account = test_account()
        self.user = User(
            id=123, email=self.account["email"], username=self.account["username"],
            first_name="Login", last_name="Test", status=UserStatus.ACTIVE,
            password_hash=PasswordHasher().hash(self.account["password"]),
        )
        self.db = MagicMock(spec=Session)
        self.db.__enter__.return_value = self.db
        self.db.__exit__.return_value = False
        self.db.scalar.return_value = self.user
        stack.enter_context(patch("app.dependencies.database.SessionLocal", return_value=self.db))
        self.client = stack.enter_context(TestClient(isolated_login_app(), raise_server_exceptions=False))

    def login(self, identifier=None, password=None):
        return self.client.post("/auth/login", json={
            "identifier": identifier if identifier is not None else self.account["email"],
            "password": password if password is not None else self.account["password"],
        })

    def assert_session_released_without_writes(self):
        self.db.__exit__.assert_called_once()
        self.db.add.assert_not_called()
        self.db.flush.assert_not_called()
        self.db.commit.assert_not_called()

    def test_success_argon2id_jwt_and_no_password_disclosure(self):
        before = datetime.now(timezone.utc).timestamp()
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"access_token", "token_type"})
        self.assertEqual(response.json()["token_type"], "bearer")
        token = response.json()["access_token"]
        claims = jwt.decode(token, self.key, algorithms=["HS256"])
        self.assertEqual(set(claims), {"sub", "exp"})
        self.assertEqual(claims["sub"], "123")
        self.assertGreaterEqual(claims["exp"], before + 3599)
        self.assertLessEqual(claims["exp"], datetime.now(timezone.utc).timestamp() + 3600)
        self.assertTrue(self.user.password_hash.startswith("$argon2id$"))
        self.assertNotIn("password_hash", response.text)
        self.assertNotIn(self.user.password_hash, response.text)
        self.assertNotIn(self.account["password"], response.text)
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, secrets.token_urlsafe(48), algorithms=["HS256"])
        self.assert_session_released_without_writes()

    def test_username_login_and_identifier_whitespace(self):
        self.assertEqual(self.login(identifier=f"  {self.account['username']}  ").status_code, 200)
        statement = self.db.scalar.call_args.args[0]
        self.assertEqual(set(statement.compile().params.values()), {self.account["username"]})
        self.assert_session_released_without_writes()

    def test_incorrect_password(self):
        response = self.login(password="Incorrect-password!")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid credentials."})
        self.assert_session_released_without_writes()

    def test_nonexistent_user(self):
        self.db.scalar.return_value = None
        with patch.object(PasswordHasher, "verify") as verify:
            response = self.login()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid credentials."})
        # Documents the timing difference: unknown users skip expensive hashing.
        verify.assert_not_called()
        self.assert_session_released_without_writes()

    def test_validation_hides_submitted_password(self):
        for payload in (
            {"identifier": " ", "password": self.account["password"]},
            {"identifier": self.account["email"], "password": ""},
            {"identifier": self.account["email"], "password": "x" * 129},
            {**self.account, "identifier": self.account["email"]},
        ):
            response = self.client.post("/auth/login", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.account["password"], response.text)
            for error in response.json()["detail"]:
                self.assertNotIn("input", error)
        self.db.scalar.assert_not_called()

    def test_current_behavior_inactive_account_is_accepted(self):
        self.user.status = UserStatus.INACTIVE
        self.assertEqual(self.login().status_code, 200)

    def test_current_behavior_deleted_account_is_accepted(self):
        self.user.deleted_at = datetime.now(timezone.utc)
        self.assertEqual(self.login().status_code, 200)

    def test_current_behavior_malformed_hash_returns_500(self):
        self.user.password_hash = "not-a-supported-password-hash"
        response = self.login()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(self.user.password_hash, response.text)
        self.assert_session_released_without_writes()

    def test_database_failure_returns_500_and_releases_session(self):
        self.db.scalar.side_effect = SQLAlchemyError("private database details")
        response = self.login()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private database details", response.text)
        self.assert_session_released_without_writes()

    @unittest.expectedFailure
    def test_production_application_exposes_login(self):
        """Known defect: main.py does not include the auth router (actual HTTP 404)."""
        with TestClient(app) as client:
            response = client.post("/auth/login", json={
                "identifier": self.account["email"], "password": self.account["password"],
            })
        self.assertEqual(response.status_code, 200)

    def test_jwt_environment_template_and_required_secret(self):
        template = Path(__file__).resolve().parents[1] / ".env.example"
        names = {
            line.split("=", 1)[0].strip()
            for line in template.read_text().splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        self.assertTrue({"JWT_SECRET_KEY", "JWT_ALGORITHM", "JWT_EXPIRE_MINUTES"} <= names)
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://test:test@localhost/test"}, clear=True):
            with self.assertRaises(ValidationError) as error:
                Settings(_env_file=None)
        self.assertTrue(any(e["loc"] == ("JWT_SECRET_KEY",) for e in error.exception.errors()))


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "opt-in PostgreSQL tests")
class PostgreSQLLoginTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.key = secrets.token_urlsafe(48)
        self.stack.enter_context(patch.object(settings, "JWT_SECRET_KEY", SecretStr(self.key)))
        self.stack.enter_context(patch.object(settings, "JWT_ALGORITHM", "HS256"))
        self.connection = self.stack.enter_context(engine.connect())
        transaction = self.connection.begin()
        self.stack.callback(transaction.rollback)
        self.sessions = []

        def session_factory():
            db = Session(bind=self.connection, autoflush=False, join_transaction_mode="create_savepoint")
            db.close = Mock(wraps=db.close)
            self.sessions.append(db)
            return db

        self.stack.enter_context(patch("app.dependencies.database.SessionLocal", side_effect=session_factory))
        self.registration_client = self.stack.enter_context(TestClient(app))
        self.client = self.stack.enter_context(TestClient(isolated_login_app()))
        self.account = test_account()
        response = self.registration_client.post("/users/register", json=self.account)
        self.assertEqual(response.status_code, 201)
        self.user_id = response.json()["id"]

    def test_registered_argon2id_account_login_and_session_cleanup(self):
        snapshot = self.connection.execute(
            select(User.password_hash, User.updated_at).where(User.id == self.user_id)
        ).one()
        self.assertTrue(snapshot.password_hash.startswith("$argon2id$"))
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lstrip().split()[0].upper())

        event.listen(self.connection, "before_cursor_execute", capture)
        self.stack.callback(event.remove, self.connection, "before_cursor_execute", capture)
        for identifier, password, expected in (
            (self.account["email"], self.account["password"], 200),
            (self.account["username"], self.account["password"], 200),
            (self.account["email"], "Incorrect-password!", 401),
            (f"missing-{uuid4().hex}", self.account["password"], 401),
            (self.account["email"], self.account["password"], 200),
        ):
            response = self.client.post("/auth/login", json={"identifier": identifier, "password": password})
            self.assertEqual(response.status_code, expected)
            self.assertNotIn("password_hash", response.text)
            self.assertNotIn(snapshot.password_hash, response.text)
            if expected == 200:
                claims = jwt.decode(response.json()["access_token"], self.key, algorithms=["HS256"])
                self.assertEqual(claims["sub"], str(self.user_id))
            else:
                self.assertEqual(response.json(), {"detail": "Invalid credentials."})
            self.sessions[-1].close.assert_called_once()
            self.assertFalse(self.sessions[-1].in_transaction())
            self.assertTrue(self.connection.in_transaction())
        self.assertFalse({"INSERT", "UPDATE", "DELETE", "COMMIT"} & set(statements))
        after = self.connection.execute(
            select(User.password_hash, User.updated_at).where(User.id == self.user_id)
        ).one()
        self.assertEqual(snapshot, after)

    def test_current_identifier_lookup_can_match_two_accounts(self):
        second = test_account()
        second["username"] = self.account["email"]
        response = self.registration_client.post("/users/register", json=second)
        self.assertEqual(response.status_code, 201)
        matches = self.connection.scalars(select(User.id).where(or_(
            User.email == self.account["email"], User.username == self.account["email"]
        ))).all()
        self.assertEqual(set(matches), {self.user_id, response.json()["id"]})
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
            selected = UserRepository(db).get_by_identifier(self.account["email"])
            # scalar() silently picks one account; the identifier is ambiguous.
            self.assertIn(selected.id, matches)


if __name__ == "__main__":
    unittest.main()
