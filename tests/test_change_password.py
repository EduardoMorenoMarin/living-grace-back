"""Password changes through production routes, JWTs, hashing and real SQL."""
import unittest
from unittest.mock import patch

import jwt
import test_rehearsal_read as fixtures
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.models import User
from app.repositories.user import UserRepository
from app.schemas.auth import ChangePasswordRequest
from app.schemas.user import UserCreate
from app.services.password_hasher import PasswordHasher


class ChangePasswordTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        self.old_password = "OldPassword123!"
        self.new_password = "NewPassword456!"
        self.hasher = PasswordHasher()
        self.original_hash = self.hasher.hash(self.old_password)
        self.change(User, 102, password_hash=self.original_hash)
        self.other_hash = self.db.get(User, 101).password_hash
        self.login(102)

    def post(self, **overrides):
        body = {"current_password": self.old_password, "new_password": self.new_password}
        body.update(overrides)
        return self.client.post("/auth/change-password", headers=self.headers, json=body)

    def assert_sensitive_response(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        for secret in (self.old_password, self.new_password, self.original_hash, "password_hash"):
            self.assertNotIn(secret, response.text)

    def assert_original_password(self):
        self.db.expire_all()
        self.assertEqual(self.db.get(User, 102).password_hash, self.original_hash)
        self.assertEqual(self.db.get(User, 101).password_hash, self.other_hash)

    def test_success_changes_only_authenticated_user_and_login_credentials(self):
        # Forged identity/role claims cannot select another account.
        token = jwt.encode({"sub": "102", "exp": 9999999999, "user_id": 101, "role": "ADMIN"},
                           settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        self.headers = {"Authorization": f"Bearer {token}"}
        with self.assertNoLogs(level="WARNING"):
            response = self.post()
        self.assert_sensitive_response(response, 204)
        self.assertEqual(response.content, b"")
        self.db.expire_all()
        updated = self.db.get(User, 102)
        self.assertNotEqual(updated.password_hash, self.original_hash)
        self.assertTrue(updated.password_hash.startswith("$argon2id$"))
        self.assertTrue(self.hasher.verify(self.new_password, updated.password_hash))
        self.assertEqual(self.db.get(User, 101).password_hash, self.other_hash)
        for password, status in ((self.new_password, 200), (self.old_password, 401)):
            result = self.client.post("/auth/login", json={"identifier": updated.email, "password": password})
            self.assertEqual(result.status_code, status, result.text)
        self.assertEqual(updated.first_name, "Regular")

    def test_incorrect_current_password(self):
        with patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback:
            self.assert_sensitive_response(self.post(current_password="WrongPassword123!"), 400)
            rollback.assert_called_once()
        self.assert_original_password()

    def test_missing_invalid_expired_and_unknown_user_jwt(self):
        key = settings.JWT_SECRET_KEY.get_secret_value()
        tokens = [None, "invalid", jwt.encode({"sub": "102", "exp": 1}, key, algorithm=settings.JWT_ALGORITHM),
                  jwt.encode({"sub": "99999", "exp": 9999999999}, key, algorithm=settings.JWT_ALGORITHM)]
        for token in tokens:
            with self.subTest(token=token), patch.object(UserRepository, "set_password") as write:
                self.headers = {} if token is None else {"Authorization": f"Bearer {token}"}
                self.assert_sensitive_response(self.post(), 401)
                write.assert_not_called()
        self.assert_original_password()

    def test_identical_password(self):
        self.assert_sensitive_response(self.post(new_password=self.old_password), 422)
        self.assert_original_password()

    def test_validation_uses_registration_password_rules(self):
        for value in ("", "short", "x" * 129, None, 123):
            with self.subTest(value=value):
                self.assert_sensitive_response(self.post(new_password=value), 422)
        # Both schemas share the same boundary rules, with no extra complexity rule.
        for value in ("x" * 8, "x" * 128):
            data = ChangePasswordRequest(current_password=self.old_password, new_password=value)
            registration = UserCreate(email="boundary@example.com", password=value, first_name="A", last_name="B")
            self.assertEqual(data.new_password, registration.password)
        self.assert_original_password()

    def test_missing_fields_extra_identity_and_malformed_body_are_safe(self):
        for body in ({}, {"current_password": self.old_password},
                     {"new_password": self.new_password},
                     {"current_password": self.old_password, "new_password": self.new_password, "user_id": 101}):
            self.assert_sensitive_response(self.client.post("/auth/change-password", headers=self.headers, json=body), 422)
        malformed = self.client.post(
            "/auth/change-password", headers={**self.headers, "Content-Type": "application/json"},
            content='{"current_password":',
        )
        self.assert_sensitive_response(malformed, 422)
        self.assert_original_password()

    def test_database_failures_roll_back_without_logging_secrets(self):
        for method in ("get_for_password_change", "set_password"):
            with self.subTest(method=method), patch.object(UserRepository, method,
                    side_effect=SQLAlchemyError(self.new_password + self.original_hash)), \
                    patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback, \
                    self.assertNoLogs(level="WARNING"):
                self.assert_sensitive_response(self.post(), 500)
                rollback.assert_called_once()
            self.assert_original_password()
        # Flush writes the new hash before a failed commit; rollback must undo it.
        for error in (SQLAlchemyError("private parameters"),
                      IntegrityError("UPDATE users", {"password_hash": self.original_hash}, Exception("private"))):
            with self.subTest(error=type(error).__name__), patch.object(self.db, "commit", side_effect=error), \
                    patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback, \
                    self.assertNoLogs(level="WARNING"):
                self.assert_sensitive_response(self.post(), 500)
                rollback.assert_called_once()
            self.assert_original_password()

    def test_authentication_database_failure_is_no_store(self):
        with patch.object(self.db, "get", side_effect=SQLAlchemyError("private")):
            self.assert_sensitive_response(self.post(), 500)
        self.assert_original_password()

    def test_malformed_stored_hash_is_sanitized(self):
        self.change(User, 102, password_hash="invalid-private-hash")
        with self.assertNoLogs(level="WARNING"):
            result = self.post()
        self.assert_sensitive_response(result, 500)
        self.assertNotIn("invalid-private-hash", result.text)
