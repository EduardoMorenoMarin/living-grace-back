"""Profile HTTP contract exercised with real queries against isolated SQLite."""
import os
import secrets
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, MetaData, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.dependencies.database import get_db
from app.main import app
from app.models import Function, Ministry, MinistryMember, MinistryMemberFunction, User
from app.models.enums import FunctionStatus, MembershipStatus, MinistryStatus, UserStatus
from app.services.password_hasher import PasswordHasher
from app.services.token import TokenService


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.stack.callback(self.engine.dispose)
        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            copied = table.to_metadata(metadata)
            for column in copied.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(self.engine)
        self.db = self.stack.enter_context(Session(self.engine, autoflush=False))
        self.user = User(
            email="test@example.com", password_hash="private-hash",
            first_name="Test", last_name="Two", phone="987654322",
            birth_date=date(2000, 1, 1), status=UserStatus.ACTIVE,
        )
        self.other = User(email="other@example.com", password_hash="other-hash",
                          first_name="Other", last_name="User")
        self.db.add_all([self.user, self.other])
        self.db.commit()
        self.user_id = self.user.id
        self.headers = {"Authorization": f"Bearer {TokenService(settings).create_access_token(self.user_id)}"}

        def override_db():
            yield self.db

        self.stack.enter_context(patch.dict(app.dependency_overrides, {get_db: override_db}))
        self.client = self.stack.enter_context(TestClient(app))

    def membership(self, name, functions, user_id=None, status=MembershipStatus.PENDING):
        ministry = Ministry(name=name, status=MinistryStatus.ACTIVE)
        self.db.add(ministry)
        self.db.flush()
        member = MinistryMember(user_id=user_id or self.user_id, ministry_id=ministry.id, status=status)
        self.db.add(member)
        self.db.flush()
        for name in functions:
            function = Function(name=name, ministry_id=ministry.id, status=FunctionStatus.ACTIVE)
            self.db.add(function)
            self.db.flush()
            self.db.add(MinistryMemberFunction(ministry_member_id=member.id, function_id=function.id))
        self.db.commit()
        return member

    def get(self, **kwargs):
        return self.client.get("/users/me", headers=self.headers, **kwargs)

    def test_authenticated_profile_and_sensitive_fields(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"first_name", "last_name", "email", "phone", "birth_date", "created_at", "memberships"})
        self.assertEqual(body["email"], "test@example.com")
        self.assertEqual(body["birth_date"], "2000-01-01")
        self.assertEqual(body["first_name"], "Test")
        self.assertEqual(body["last_name"], "Two")
        self.assertEqual(body["phone"], "987654322")
        self.assertTrue(body["created_at"])
        for secret in ("private-hash", "password", "access_token", "refresh_token", "username"):
            self.assertNotIn(secret, response.text)

    def test_existing_user_email_login_and_token_authenticate_profile(self):
        password = "Existing-account-password-123!"
        self.user.password_hash = PasswordHasher().hash(password)
        self.db.commit()
        response = self.client.post("/auth/login", json={
            "identifier": self.user.email, "password": password,
        })
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        claims = jwt.decode(token, settings.JWT_SECRET_KEY.get_secret_value(),
                            algorithms=[settings.JWT_ALGORITHM])
        self.assertEqual(claims["sub"], str(self.user_id))
        profile = self.client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["email"], self.user.email)
        self.assertNotIn("username", profile.json())

    def test_missing_authentication(self):
        for headers in ({}, {"Authorization": "Basic invalid"}):
            response = self.client.get("/users/me", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_invalid_expired_and_malformed_claims(self):
        def token(claims, key=None):
            return jwt.encode(claims, key or settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        tokens = [
            "invalid", token({"sub": str(self.user_id), "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}),
            token({"sub": str(self.user_id), "exp": future}, secrets.token_urlsafe(48)),
            token({"sub": "not-an-id", "exp": future}), token({"sub": str(self.user_id)}),
        ]
        for value in tokens:
            with self.subTest(token=value):
                self.assertEqual(self.client.get("/users/me", headers={"Authorization": f"Bearer {value}"}).status_code, 401)

    def test_one_ministry_one_function(self):
        self.membership("Alabanza", ["Teclado"])
        self.assertEqual(self.get().json()["memberships"], [{"ministry": "Alabanza", "functions": ["Teclado"]}])

    def test_one_ministry_multiple_functions(self):
        self.membership("Alabanza", ["Teclado", "Vocalista"], status=MembershipStatus.ACTIVE)
        self.assertEqual(self.get().json()["memberships"], [{"ministry": "Alabanza", "functions": ["Teclado", "Vocalista"]}])

    def test_multiple_ministries_group_functions(self):
        self.membership("Alabanza", ["Teclado", "Vocalista"])
        self.membership("Producción", ["Sound", "Video"])
        self.assertEqual(self.get().json()["memberships"], [
            {"ministry": "Alabanza", "functions": ["Teclado", "Vocalista"]},
            {"ministry": "Producción", "functions": ["Sound", "Video"]},
        ])

    def test_no_memberships(self):
        self.assertEqual(self.get().json()["memberships"], [])

    def test_membership_without_functions(self):
        self.membership("Hospitality", [])
        self.assertEqual(self.get().json()["memberships"], [{"ministry": "Hospitality", "functions": []}])

    def test_missing_optional_fields(self):
        self.user.phone = None
        self.user.birth_date = None
        self.db.commit()
        body = self.get().json()
        self.assertIsNone(body["phone"])
        self.assertIsNone(body["birth_date"])

    def test_missing_deleted_and_inactive_users(self):
        for attribute, value in (("deleted_at", datetime.now(timezone.utc)), ("status", UserStatus.INACTIVE)):
            with self.subTest(attribute=attribute):
                setattr(self.user, attribute, value)
                self.db.commit()
                self.assertEqual(self.get().status_code, 401)
                setattr(self.user, attribute, None if attribute == "deleted_at" else UserStatus.ACTIVE)
                self.db.commit()
        self.db.delete(self.user)
        self.db.commit()
        self.assertEqual(self.get().status_code, 401)

    def test_other_users_data_cannot_be_requested(self):
        self.membership("Private ministry", ["Private function"], user_id=self.other.id)
        response = self.get(params={"user_id": self.other.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], self.user.email)
        self.assertEqual(response.json()["memberships"], [])

    def test_cross_ministry_function_is_excluded(self):
        member = self.membership("Alabanza", ["Teclado"])
        other_member = self.membership("Producción", ["Sound"], user_id=self.other.id)
        other_function = self.db.query(Function).filter_by(ministry_id=other_member.ministry_id).one()
        self.db.add(MinistryMemberFunction(ministry_member_id=member.id, function_id=other_function.id))
        self.db.commit()
        self.assertEqual(self.get().json()["memberships"], [{"ministry": "Alabanza", "functions": ["Teclado"]}])

    def test_unavailable_ministries_and_memberships_are_excluded(self):
        for target, attribute, value in (
            ("member", "deleted_at", datetime.now(timezone.utc)),
            ("member", "status", MembershipStatus.INACTIVE),
            ("ministry", "deleted_at", datetime.now(timezone.utc)),
            ("ministry", "status", MinistryStatus.INACTIVE),
        ):
            member = self.membership(str((target, attribute)), ["Function"])
            entity = member if target == "member" else self.db.get(Ministry, member.ministry_id)
            setattr(entity, attribute, value)
        self.db.commit()
        self.assertEqual(self.get().json()["memberships"], [])

    def test_unavailable_functions_preserve_empty_ministry(self):
        member = self.membership("Alabanza", ["Deleted", "Inactive", "Revoked"])
        functions = self.db.query(Function).filter_by(ministry_id=member.ministry_id).order_by(Function.id).all()
        functions[0].deleted_at = datetime.now(timezone.utc)
        functions[1].status = FunctionStatus.INACTIVE
        assignment = self.db.query(MinistryMemberFunction).filter_by(function_id=functions[2].id).one()
        assignment.revoked_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.get().json()["memberships"], [{"ministry": "Alabanza", "functions": []}])

    def test_constant_query_count(self):
        for index in range(4):
            self.membership(f"Ministry {index}", ["One", "Two"])
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        # Expire the identity map so authentication must perform its lookup too.
        self.db.expire_all()
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            self.assertEqual(self.get().status_code, 200)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 2)

    def test_profile_database_failure_is_safe(self):
        with patch.object(self.db, "execute", side_effect=SQLAlchemyError("private database details")):
            # Keep authentication's lookup in the identity map; fail the profile read.
            response = self.get()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to retrieve profile."})
