import os
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

# Unit tests do not require credentials or contact a database.
if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")

from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import engine
from app.dependencies.database import get_db
from app.main import app
from app.models.ministry_member import MinistryMember
from app.models.user import User
from app.repositories.user import UserRepository


def registration_input():
    token = uuid4().hex
    return {
        "email": f"registration-{token}@example.com",
        "username": f"registration_{token}",
        "password": "Example-password-123!",
        "first_name": "Registration",
        "last_name": "Test",
    }


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock(spec=Session)
        self.repository_patch = patch("app.services.registration.UserRepository", autospec=True)
        self.repository = self.repository_patch.start().return_value
        self.addCleanup(self.repository_patch.stop)
        self.repository.email_exists.return_value = False
        self.repository.username_exists.return_value = False
        self.created = []

        def add(user):
            self.created.append(user)
            # Simulate server defaults returned by a successful flush.
            user.id = 123
            user.status = "ACTIVE"
            user.created_at = user.updated_at = datetime.now(timezone.utc)
            user.deleted_at = None

        self.repository.add.side_effect = add

        def override_db():
            yield self.db

        app.dependency_overrides[get_db] = override_db
        self.addCleanup(app.dependency_overrides.clear)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.client = stack.enter_context(TestClient(app))
        self.payload = registration_input()

    def test_registration_hash_and_safe_response(self):
        self.payload.update(phone="+571234567890", birth_date="2000-01-02")
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["email"], self.payload["email"])
        self.assertEqual(body["birth_date"], "2000-01-02")
        self.assertEqual(body["phone"], self.payload["phone"])
        self.assertEqual(body["status"], "ACTIVE")
        self.assertNotIn("password", body)
        self.assertNotIn("password_hash", body)
        stored_hash = self.created[0].password_hash
        self.assertTrue(stored_hash.startswith("$argon2id$"))
        self.assertTrue(PasswordHash.recommended().verify(self.payload["password"], stored_hash))
        self.assertNotIn(stored_hash, response.text)
        self.assertNotIn(self.payload["password"], response.text)
        self.db.commit.assert_called_once()
        self.db.rollback.assert_not_called()

    def test_duplicate_email(self):
        self.repository.email_exists.return_value = True
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Email is already registered.")
        self.repository.add.assert_not_called()
        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_duplicate_username(self):
        self.repository.username_exists.return_value = True
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Username is already registered.")
        self.repository.add.assert_not_called()
        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_insert_time_unique_violation(self):
        self.repository.add.side_effect = IntegrityError(
            "private SQL", {"password_hash": "private hash"}, SimpleNamespace(sqlstate="23505")
        )
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private", response.text)
        self.assertNotIn("password_hash", response.text)
        self.db.rollback.assert_called_once()
        self.db.commit.assert_not_called()

    def test_other_integrity_error_is_not_a_conflict(self):
        self.repository.add.side_effect = IntegrityError(
            "private SQL", {}, SimpleNamespace(sqlstate="23502")
        )
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.db.rollback.assert_called_once()

    def test_commit_failure_rolls_back_without_leaking_details(self):
        self.db.commit.side_effect = SQLAlchemyError("private connection details")
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.db.rollback.assert_called_once()

    def test_lookup_failure_is_sanitized(self):
        self.repository.email_exists.side_effect = SQLAlchemyError("private connection details")
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private", response.text)
        self.db.rollback.assert_called_once()

    def test_request_validation_does_not_echo_secrets(self):
        for field, value in (
            ("email", "invalid"), ("username", "   "), ("first_name", ""),
            ("last_name", ""), ("birth_date", "not-a-date"),
            ("password", "s3cr!"), ("password", "x" * 129),
            ("status", "ACTIVE"),
        ):
            with self.subTest(field=field):
                payload = {**self.payload, field: value}
                response = self.client.post("/users/register", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(payload["password"], response.text)
                for error in response.json()["detail"]:
                    self.assertNotIn("input", error)
        self.repository.add.assert_not_called()

    def test_application_startup_and_response_contract(self):
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        properties = schema["components"]["schemas"]["UserResponse"]["properties"]
        self.assertNotIn("password", properties)
        self.assertNotIn("password_hash", properties)


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "opt-in PostgreSQL tests")
class PostgreSQLRegistrationTests(unittest.TestCase):
    def setUp(self):
        # Requests commit savepoints; the outer transaction is always rolled back.
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)

        def override_db():
            with Session(
                bind=self.connection, autoflush=False, join_transaction_mode="create_savepoint"
            ) as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        self.addCleanup(app.dependency_overrides.clear)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.client = stack.enter_context(TestClient(app))
        self.payload = registration_input()

    def test_registration_and_both_duplicates(self):
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertNotIn("password", body)
        self.assertNotIn("password_hash", body)
        self.assertEqual(body["status"], "ACTIVE")
        self.assertIsNone(body["phone"])
        self.assertIsNone(body["birth_date"])
        self.assertIsNone(body["deleted_at"])
        self.assertIsNotNone(body["created_at"])
        self.assertIsNotNone(body["updated_at"])
        stored_hash = self.connection.scalar(
            select(User.password_hash).where(User.id == body["id"])
        )
        self.assertTrue(stored_hash.startswith("$argon2id$"))
        self.assertTrue(PasswordHash.recommended().verify(self.payload["password"], stored_hash))
        self.assertNotIn(stored_hash, response.text)

        self.assertIsNone(self.connection.scalar(
            select(MinistryMember.id).where(MinistryMember.user_id == body["id"])
        ))

        duplicate_email = {**self.payload, "username": f"other_{uuid4().hex}"}
        response = self.client.post("/users/register", json=duplicate_email)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Email is already registered.")
        duplicate_username = {**self.payload, "email": f"other-{uuid4().hex}@example.com"}
        response = self.client.post("/users/register", json=duplicate_username)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Username is already registered.")

    def test_database_conflicts_after_prechecks_and_session_recovery(self):
        self.assertEqual(self.client.post("/users/register", json=self.payload).status_code, 201)
        with patch.object(UserRepository, "email_exists", return_value=False), patch.object(
            UserRepository, "username_exists", return_value=False
        ):
            # Force the insert-time conflict path for each real unique constraint.
            for duplicate in (
                {**self.payload, "username": f"other_{uuid4().hex}"},
                {**self.payload, "email": f"other-{uuid4().hex}@example.com"},
            ):
                response = self.client.post("/users/register", json=duplicate)
                self.assertEqual(response.status_code, 409)
                self.assertNotIn("password_hash", response.text)
        self.assertEqual(self.client.post("/users/register", json=registration_input()).status_code, 201)


if __name__ == "__main__":
    unittest.main()
