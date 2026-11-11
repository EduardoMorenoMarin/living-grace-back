"""Admin reset and mandatory password change through real SQL and production routes."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import jwt
import test_rehearsal_read as fixtures
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.models import MinistryMemberRole, User
from app.models.enums import UserStatus
from app.repositories.user import UserRepository
from app.services.password_hasher import PasswordHasher
from app.services.token import TokenService


class ResetPasswordTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        self.hasher = PasswordHasher()
        self.old_password = "OldPassword123!"
        self.original_hash = self.hasher.hash(self.old_password)
        self.change(User, 102, password_hash=self.original_hash)
        self.admin_hash = self.db.get(User, 101).password_hash
        self.login(102)
        self.old_headers = dict(self.headers)
        self.login(101)
        self.admin_headers = dict(self.headers)

    def reset(self, user_id=102):
        return self.client.post(f"/users/{user_id}/reset-password", headers=self.headers)

    def credentials_login(self, password):
        return self.client.post("/auth/login", json={"identifier": "user102@example.com", "password": password})

    def change_password(self, current, new="NewPassword456!"):
        return self.client.post("/auth/change-password", headers=self.headers,
                                json={"current_password": current, "new_password": new})

    def assert_no_store(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(self.original_hash, response.text)
        self.assertNotIn("password_hash", response.text)

    def temporary_login(self):
        response = self.reset()
        self.assert_no_store(response, 200)
        temporary = response.json()["temporary_password"]
        result = self.credentials_login(temporary)
        self.assert_no_store(result, 200)
        self.assertTrue(result.json()["must_change_password"])
        self.headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
        return temporary

    def test_secure_generation_hashing_and_only_successful_response_contains_password(self):
        with patch("app.services.reset_password.secrets.token_urlsafe", return_value="secure-temporary-password") as generate, \
                self.assertNoLogs(level="WARNING"):
            result = self.reset()
        self.assert_no_store(result, 200)
        generate.assert_called_once_with(24)
        self.assertEqual(result.json(), {"temporary_password": "secure-temporary-password", "must_change_password": True})
        self.db.expire_all()
        user = self.db.get(User, 102)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.password_hash.startswith("$argon2id$"))
        self.assertTrue(self.hasher.verify("secure-temporary-password", user.password_hash))
        stored = self.db.execute(select(User.__table__).where(User.id == 102)).mappings().one()
        self.assertNotIn("secure-temporary-password", str(dict(stored)))
        self.assertEqual(self.db.get(User, 101).password_hash, self.admin_hash)
        self.assertFalse(self.db.get(User, 101).must_change_password)
        self.assert_no_store(self.credentials_login(self.old_password), 401)

    def test_repeated_resets_generate_distinct_credentials_and_revoke_intermediate_token(self):
        first = self.reset().json()["temporary_password"]
        first_login = self.credentials_login(first).json()
        second = self.reset().json()["temporary_password"]
        self.assertNotEqual(first, second)
        self.assert_no_store(self.credentials_login(first), 401)
        self.assert_no_store(self.credentials_login(second), 200)
        self.headers = {"Authorization": f"Bearer {first_login['access_token']}"}
        self.assert_no_store(self.change_password(first), 401)

    def test_missing_and_invalid_authentication(self):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            self.headers = headers
            with patch.object(UserRepository, "set_password") as write:
                self.assert_no_store(self.reset(), 401)
                write.assert_not_called()

    def test_non_admin_ministry_scoped_and_revoked_admin_are_denied(self):
        self.headers = self.old_headers
        self.assert_no_store(self.reset(), 403)
        self.headers = self.admin_headers
        self.change(MinistryMemberRole, 501, role_id=19)
        self.assert_no_store(self.reset(), 403)
        self.change(MinistryMemberRole, 501, role_id=17)
        self.change(MinistryMemberRole, 501, revoked_at=datetime.now(timezone.utc))
        self.assert_no_store(self.reset(), 403)
        self.assertEqual(self.db.get(User, 102).password_hash, self.original_hash)

    def test_missing_deleted_and_inactive_targets(self):
        self.assert_no_store(self.reset(99999), 404)
        self.change(User, 102, status=UserStatus.INACTIVE)
        self.assert_no_store(self.reset(), 409)
        self.change(User, 102, status=UserStatus.ACTIVE, deleted_at=datetime.now(timezone.utc))
        self.assert_no_store(self.reset(), 404)
        self.assertEqual(self.db.get(User, 102).password_hash, self.original_hash)
        self.assertFalse(self.db.get(User, 102).must_change_password)

    def test_forced_change_blocks_business_operations_and_clears_flag_atomically(self):
        temporary = self.temporary_login()
        temporary_headers = dict(self.headers)
        for method, path, body in (
            ("get", "/users/me", None), ("get", "/rehearsals", None),
            ("get", "/rehearsals/301", None), ("get", "/ministries/11/members", None),
            ("post", "/attendance/check-in", {"token": "irrelevant"}),
            ("post", "/users/101/reset-password", None),
        ):
            response = self.client.request(method, path, headers=self.headers, json=body)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["detail"], "Password change required.")
        self.assert_no_store(self.change_password(temporary), 204)
        self.db.expire_all()
        self.assertFalse(self.db.get(User, 102).must_change_password)
        self.assert_no_store(self.credentials_login(temporary), 401)
        result = self.credentials_login("NewPassword456!")
        self.assert_no_store(result, 200)
        self.assertFalse(result.json()["must_change_password"])
        self.headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
        self.assertEqual(self.client.get("/rehearsals", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/users/me", headers=temporary_headers).status_code, 401)
        self.assertEqual(self.client.get("/users/me", headers=self.old_headers).status_code, 401)

    def test_incorrect_temporary_password_preserves_flag_and_hash(self):
        temporary = self.temporary_login()
        before = self.db.get(User, 102).password_hash
        self.assert_no_store(self.change_password("IncorrectTemporary123!"), 400)
        self.assertTrue(self.db.get(User, 102).must_change_password)
        self.assertEqual(self.db.get(User, 102).password_hash, before)
        self.assert_no_store(self.change_password(temporary, temporary), 422)
        self.assert_no_store(self.change_password(temporary, "short"), 422)
        self.assertTrue(self.db.get(User, 102).must_change_password)

    def test_pre_reset_tokens_cannot_change_password_or_access_business_endpoints(self):
        self.reset()
        self.headers = self.old_headers
        self.assertEqual(self.client.get("/rehearsals", headers=self.headers).status_code, 401)
        self.assert_no_store(self.change_password(self.old_password), 401)
        self.assertTrue(self.db.get(User, 102).must_change_password)

    def test_legacy_and_malformed_credential_claims_are_rejected(self):
        for claim in (None, 123, "wrong", "é"):
            payload = {"sub": "102", "exp": 9999999999}
            if claim is not None:
                payload["pwdv"] = claim
            token = jwt.encode(payload, settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
            self.assertEqual(self.client.get("/users/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)
        valid = TokenService(settings).create_access_token(102, self.original_hash)
        self.assertNotIn(self.original_hash, jwt.decode(valid, settings.JWT_SECRET_KEY.get_secret_value(),
                                                      algorithms=[settings.JWT_ALGORITHM]).values())

    def test_reset_database_failures_roll_back_without_plaintext_or_hash_logs(self):
        for method in ("get_for_password_change", "set_password"):
            with self.subTest(method=method), patch.object(UserRepository, method, side_effect=SQLAlchemyError("private")), \
                    patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback, self.assertNoLogs(level="WARNING"):
                self.assert_no_store(self.reset(), 500)
                rollback.assert_called_once()
            self.assertEqual(self.db.get(User, 102).password_hash, self.original_hash)
            self.assertFalse(self.db.get(User, 102).must_change_password)
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("private")), self.assertNoLogs(level="WARNING"):
            result = self.reset()
        self.assert_no_store(result, 500)
        self.assertNotIn("temporary_password", result.json())
        self.db.expire_all()
        self.assertEqual(self.db.get(User, 102).password_hash, self.original_hash)
        self.assertFalse(self.db.get(User, 102).must_change_password)

    def test_change_failure_rolls_back_both_hash_and_required_flag(self):
        temporary = self.temporary_login()
        before = self.db.get(User, 102).password_hash
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("private")):
            self.assert_no_store(self.change_password(temporary), 500)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, 102).password_hash, before)
        self.assertTrue(self.db.get(User, 102).must_change_password)
        self.assert_no_store(self.change_password(temporary), 204)

    def test_admin_under_forced_change_cannot_reset_others(self):
        self.assert_no_store(self.reset(101), 200)
        token = TokenService(settings).create_access_token(101, self.db.get(User, 101).password_hash)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.assert_no_store(self.reset(), 403)
