"""Ministry member listing through real authentication and isolated SQL queries."""
import os
import secrets
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

if os.environ.get("RUN_DATABASE_TESTS") != "1":
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
from app.models import Function, Ministry, MinistryMember, MinistryMemberFunction, MinistryMemberRole, Role, User
from app.models.enums import FunctionStatus, MembershipStatus, MinistryStatus, RoleScope, RoleStatus, UserStatus
from app.services.token import TokenService


class MinistryMembersTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.stack.callback(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            copied = table.to_metadata(metadata)
            for column in copied.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(self.engine)
        self.db = self.stack.enter_context(Session(self.engine, autoflush=False))
        self.db.add_all([Ministry(id=1, name="Requested"), Ministry(id=2, name="Admin's ministry")])
        self.db.flush()
        self.admin = self.member(2, first_name="Admin")
        self.db.add_all([
            Role(id=37, name="ADMIN", scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True),
            Role(id=38, name="USER", scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True),
        ])
        self.db.flush()
        self.assignment = MinistryMemberRole(id=50, ministry_member_id=self.admin.id, role_id=37)
        self.db.add(self.assignment)
        self.db.commit()
        self.headers = {"Authorization": f"Bearer {TokenService(settings).create_access_token(self.admin.user_id)}"}

        def override_db():
            yield self.db

        self.stack.enter_context(patch.dict(app.dependency_overrides, {get_db: override_db}))
        self.client = self.stack.enter_context(TestClient(app))

    def member(self, ministry_id=1, first_name="Example", status=MembershipStatus.ACTIVE):
        unique = secrets.token_hex(8)
        user = User(email=f"{unique}@example.com", password_hash="private-hash",
                    first_name=first_name, last_name="Member", status=UserStatus.ACTIVE)
        self.db.add(user)
        self.db.flush()
        member = MinistryMember(user_id=user.id, ministry_id=ministry_id, status=status)
        self.db.add(member)
        self.db.flush()
        return member

    def function(self, member, name, ministry_id=None):
        function = Function(ministry_id=ministry_id or member.ministry_id, name=name, status=FunctionStatus.ACTIVE)
        self.db.add(function)
        self.db.flush()
        assignment = MinistryMemberFunction(ministry_member_id=member.id, function_id=function.id)
        self.db.add(assignment)
        self.db.flush()
        return function, assignment

    def get(self, ministry_id=1):
        return self.client.get(f"/ministries/{ministry_id}/members", headers=self.headers)

    def test_admin_gets_only_requested_members_with_grouped_functions(self):
        member = self.member()
        self.function(member, "Teclado")
        self.function(member, "Vocalista")
        self.db.commit()
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ministry_id": 1, "members": [{
            "ministry_member_id": member.id, "user_id": member.user_id,
            "first_name": "Example", "last_name": "Member", "status": "ACTIVE", "functions": ["Teclado", "Vocalista"],
        }]})
        self.assertNotIn("private-hash", response.text)
        self.assertNotIn("username", response.text)
        self.assertNotIn("email", response.json()["members"][0])

    def test_user_and_no_role_are_forbidden_before_member_query(self):
        from app.repositories.ministry_members import MinistryMembersRepository
        for role_id in (38, None):
            with self.subTest(role_id=role_id):
                if role_id:
                    self.assignment.role_id = role_id
                else:
                    self.db.delete(self.assignment)
                self.db.commit()
                with patch.object(MinistryMembersRepository, "get_member_rows") as query:
                    self.assertEqual(self.get().status_code, 403)
                query.assert_not_called()

    def test_missing_invalid_and_expired_authentication(self):
        expired = jwt.encode({"sub": str(self.admin.user_id), "exp": 1},
                             settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        for headers in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": f"Bearer {expired}"}):
            self.assertEqual(self.client.get("/ministries/1/members", headers=headers).status_code, 401)

    def test_pending_inactive_deleted_memberships_excluded(self):
        self.member(status=MembershipStatus.PENDING)
        self.member(status=MembershipStatus.INACTIVE)
        self.member().deleted_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.get().json(), {"ministry_id": 1, "members": []})

    def test_inactive_deleted_users_excluded(self):
        inactive = self.member()
        deleted = self.member()
        self.db.get(User, inactive.user_id).status = UserStatus.INACTIVE
        self.db.get(User, deleted.user_id).deleted_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.get().json()["members"], [])

    def test_members_without_functions(self):
        member = self.member()
        self.db.commit()
        members = self.get().json()["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["ministry_member_id"], member.id)
        self.assertEqual(members[0]["functions"], [])

    def test_no_members(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ministry_id": 1, "members": []})

    def test_nonexistent_deleted_and_inactive_ministries(self):
        self.assertEqual(self.get(999).status_code, 404)
        ministry = self.db.get(Ministry, 1)
        ministry.deleted_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.get().status_code, 404)
        ministry.deleted_at = None
        ministry.status = MinistryStatus.INACTIVE
        self.db.commit()
        self.assertEqual(self.get().status_code, 404)

    def test_invalid_path_id(self):
        for value in (0, -1, "abc", "1.5", 9223372036854775808):
            with self.subTest(value=value):
                self.assertEqual(self.get(value).status_code, 422)

    def test_unavailable_and_cross_ministry_functions_excluded(self):
        member = self.member()
        self.function(member, "Wrong ministry", ministry_id=2)
        function, _ = self.function(member, "Inactive")
        function.status = FunctionStatus.INACTIVE
        function, _ = self.function(member, "Deleted")
        function.deleted_at = datetime.now(timezone.utc)
        _, assignment = self.function(member, "Revoked")
        assignment.revoked_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.get().json()["members"][0]["functions"], [])

    def test_multiple_members_keep_functions_separate(self):
        first = self.member(first_name="First")
        self.function(first, "Teclado")
        second = self.member(first_name="Second")
        self.function(second, "Vocalista")
        self.db.commit()
        members = self.get().json()["members"]
        self.assertEqual([(m["first_name"], m["functions"]) for m in members],
                         [("First", ["Teclado"]), ("Second", ["Vocalista"])])

    def test_constant_query_count_and_read_only(self):
        for index in range(4):
            member = self.member(first_name=str(index))
            self.function(member, f"Function {index}")
        self.db.commit()
        self.db.expire_all()
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            self.assertEqual(self.get().status_code, 200)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        # Authentication, authorization, and one joined ministry/member query.
        self.assertEqual(len(statements), 3)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT") for statement in statements))

    def test_database_error_is_safe(self):
        original = self.db.execute

        def execute(statement, *args, **kwargs):
            if "function" in str(statement):
                raise SQLAlchemyError("private database details")
            return original(statement, *args, **kwargs)

        with patch.object(self.db, "execute", side_effect=execute):
            response = self.get()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to retrieve ministry members."})


    def approve(self, member_id, ministry_id=1, payload=None, headers=None):
        return self.client.patch(
            f"/ministries/{ministry_id}/members/{member_id}/status",
            json=payload if payload is not None else {"status": "ACTIVE"},
            headers=self.headers if headers is None else headers,
        )

    def test_status_filters_and_approval_preserve_relationships(self):
        pending = self.member(status=MembershipStatus.PENDING)
        active = self.member()
        inactive = self.member(status=MembershipStatus.INACTIVE)
        _, assignment = self.function(pending, "Guitar")
        role = MinistryMemberRole(id=51, ministry_member_id=pending.id, role_id=38)
        self.db.add(role)
        self.db.commit()
        for status, expected in (("PENDING", pending), ("ACTIVE", active), ("INACTIVE", inactive)):
            response = self.client.get(f"/ministries/1/members?status={status}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([m["ministry_member_id"] for m in response.json()["members"]], [expected.id])
            self.assertEqual(response.json()["members"][0]["status"], status)
        self.assertEqual(len(self.get().json()["members"]), 1)
        response = self.approve(pending.id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ministry_id": 1, "ministry_member_id": pending.id, "status": "ACTIVE"})
        self.assertEqual(len(self.get().json()["members"]), 2)
        self.assertEqual(self.client.get("/ministries/1/members?status=PENDING", headers=self.headers).json()["members"], [])
        self.assertEqual(self.db.get(MinistryMemberFunction, assignment.id).function_id, assignment.function_id)
        self.assertIsNone(assignment.revoked_at)
        self.assertEqual(self.db.get(MinistryMemberRole, role.id).role_id, 38)
        self.assertEqual(self.get().json()["members"][0]["functions"], ["Guitar"])

    def test_approval_requires_real_global_admin(self):
        member = self.member(status=MembershipStatus.PENDING)
        self.db.commit()
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            self.assertEqual(self.approve(member.id, headers=headers).status_code, 401)
        self.assignment.role_id = 38
        self.db.commit()
        self.assertEqual(self.approve(member.id).status_code, 403)
        self.assertEqual(self.db.get(MinistryMember, member.id).status, MembershipStatus.PENDING)

    def test_approval_ids_statuses_and_transitions(self):
        pending = self.member(status=MembershipStatus.PENDING)
        active = self.member()
        inactive = self.member(status=MembershipStatus.INACTIVE)
        self.db.commit()
        for member_id, ministry_id in ((pending.id, 2), (999, 1), (pending.id, 999)):
            self.assertEqual(self.approve(member_id, ministry_id).status_code, 404)
        for value in (0, -1, "abc", 9223372036854775808):
            self.assertEqual(self.approve(value).status_code, 422)
            self.assertEqual(self.approve(pending.id, value).status_code, 422)
        for payload in ({}, {"status": "PENDING"}, {"status": "INACTIVE"}, {"status": "INVALID"},
                        {"status": "ACTIVE", "user_id": 1}, {"status": None}):
            self.assertEqual(self.approve(pending.id, payload=payload).status_code, 422)
        for member in (active, inactive):
            self.assertEqual(self.approve(member.id).status_code, 409)
        self.assertEqual(self.client.get("/ministries/1/members?status=ALL", headers=self.headers).status_code, 422)

    def test_unavailable_records_cannot_be_approved_or_listed_as_pending(self):
        for target, attribute, value in (
            ("membership", "deleted_at", datetime.now(timezone.utc)),
            ("user", "deleted_at", datetime.now(timezone.utc)),
            ("user", "status", UserStatus.INACTIVE),
            ("ministry", "deleted_at", datetime.now(timezone.utc)),
            ("ministry", "status", MinistryStatus.INACTIVE),
        ):
            with self.subTest(target=target, attribute=attribute):
                member = self.member(status=MembershipStatus.PENDING)
                record = {"membership": member, "user": self.db.get(User, member.user_id),
                          "ministry": self.db.get(Ministry, 1)}[target]
                old = getattr(record, attribute)
                setattr(record, attribute, value)
                self.db.commit()
                self.assertEqual(self.approve(member.id).status_code, 404)
                response = self.client.get("/ministries/1/members?status=PENDING", headers=self.headers)
                if target == "ministry":
                    self.assertEqual(response.status_code, 404)
                else:
                    self.assertNotIn(member.id, [m["ministry_member_id"] for m in response.json()["members"]])
                setattr(record, attribute, old)
                self.db.commit()

    def test_approval_commit_failure_rolls_back(self):
        member = self.member(status=MembershipStatus.PENDING)
        self.db.commit()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("private details")):
            response = self.approve(member.id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to update membership status."})
        self.assertEqual(self.db.get(MinistryMember, member.id).status, MembershipStatus.PENDING)
        self.assertEqual(self.approve(member.id).status_code, 200)
        self.assertEqual(self.approve(member.id).status_code, 409)


    def test_registered_memberships_are_active_visible_and_keep_global_user_roles(self):
        existing = self.member(status=MembershipStatus.PENDING)
        function, _ = self.function(existing, "Guitar")
        self.db.commit()
        response = self.client.post("/users/register", json={
            "email": f"registration-{secrets.token_hex(8)}@example.com",
            "password": "Registration-password-123!",
            "first_name": "New", "last_name": "Member",
            "memberships": [
                {"ministry_id": 1, "function_ids": [function.id]},
                {"ministry_id": 2},
            ],
        })
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(len(body["memberships"]), 2)
        for membership in body["memberships"]:
            self.assertEqual(membership["status"], "ACTIVE")
            stored = self.db.get(MinistryMember, membership["id"])
            self.assertEqual(stored.status, MembershipStatus.ACTIVE)
            self.assertEqual([a.role_id for a in stored.ministry_member_roles], [38])
            self.assertIsNone(stored.ministry_member_roles[0].revoked_at)
            listing = self.get(membership["ministry_id"])
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertIn({
                "ministry_member_id": stored.id, "user_id": body["id"],
                "first_name": "New", "last_name": "Member", "status": "ACTIVE",
                "functions": ["Guitar"] if stored.ministry_id == 1 else [],
            }, listing.json()["members"])
        self.assertEqual(self.db.get(MinistryMember, existing.id).status, MembershipStatus.PENDING)
        user_headers = {"Authorization": f"Bearer {TokenService(settings).create_access_token(body['id'])}"}
        self.assertEqual(self.client.get("/ministries/1/members", headers=user_headers).status_code, 403)
