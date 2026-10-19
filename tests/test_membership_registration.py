"""Registration contract using real SQL and the production HTTP application.

SQLite is disposable and checks persistence/rollback without credentials.
The same contract runs against PostgreSQL only when RUN_DATABASE_TESTS=1.
It uses the existing schema without applying migrations.
"""

import os
import secrets
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")

from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import BigInteger, Integer, MetaData, create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, engine as postgres_engine
from app.dependencies.database import get_db
from app.main import app
from app.models.enums import FunctionStatus, MinistryStatus, RoleScope, RoleStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.ministry_member_function import MinistryMemberFunction
from app.models.ministry_member_role import MinistryMemberRole
from app.models.role import Role
from app.repositories.registration_roles import RegistrationRoleRepository
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.services.password_hasher import PasswordHasher
from test_membership_rules import MembershipRuleExtensionContract


class MembershipRegistrationContract:
    def prepare_registration(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.payload = {
            "email": f"membership-{uuid4().hex}@example.com",
            "username": f"membership_{uuid4().hex}",
            "password": "Membership-password-123!",
            "first_name": "Test", "last_name": "Member",
            "phone": "+51987654321", "birth_date": "2000-01-02",
        }
        with self.session_factory() as db:
            if db.get_bind().dialect.name == "sqlite":
                # Explicit IDs match the verified no-default role PK and prove
                # production resolution does not hardcode role ID 2.
                db.add_all([
                    Role(id=37, name="USER", scope=RoleScope.GLOBAL,
                         status=RoleStatus.ACTIVE, is_system_role=True),
                    Role(id=38, name="ADMIN", scope=RoleScope.GLOBAL,
                         status=RoleStatus.ACTIVE, is_system_role=True),
                    Role(id=39, name="USER", scope=RoleScope.MINISTRY,
                         status=RoleStatus.ACTIVE, is_system_role=False),
                ])
                db.flush()
            self.user_role_id = db.scalar(select(Role.id).where(
                Role.name == "USER", Role.scope == RoleScope.GLOBAL,
                Role.status == RoleStatus.ACTIVE, Role.deleted_at.is_(None),
            ))
            self.initial_role_assignments = set(db.scalars(select(MinistryMemberRole.id)))
            ministries = [
                Ministry(name="Alabanza", status=MinistryStatus.ACTIVE),
                Ministry(name="Producción", status=MinistryStatus.ACTIVE),
                Ministry(name="Hospitality", status=MinistryStatus.ACTIVE),
                Ministry(name="Inactive", status=MinistryStatus.INACTIVE),
                Ministry(name="Deleted", status=MinistryStatus.ACTIVE, deleted_at=datetime.now(timezone.utc)),
            ]
            db.add_all(ministries)
            db.flush()
            self.ministries = {ministry.name: ministry.id for ministry in ministries}
            functions = [
                Function(ministry_id=self.ministries["Alabanza"], name="Guitar", status=FunctionStatus.ACTIVE),
                Function(ministry_id=self.ministries["Alabanza"], name="Vocals", status=FunctionStatus.ACTIVE),
                Function(ministry_id=self.ministries["Producción"], name="Sound", status=FunctionStatus.ACTIVE),
                Function(ministry_id=self.ministries["Alabanza"], name="Inactive", status=FunctionStatus.INACTIVE),
                Function(ministry_id=self.ministries["Alabanza"], name="Deleted", status=FunctionStatus.ACTIVE,
                         deleted_at=datetime.now(timezone.utc)),
            ]
            db.add_all(functions)
            db.flush()
            self.functions = {function.name: function.id for function in functions}
            db.commit()

        def override_db():
            with self.session_factory() as db:
                yield db

        self.stack.enter_context(patch.dict(app.dependency_overrides, {get_db: override_db}))
        self.client = self.stack.enter_context(TestClient(app))

    def selection(self, name="Alabanza"):
        result = {"ministry_id": self.ministries[name]}
        if name == "Alabanza":
            result.update(function_ids=[self.functions["Guitar"], self.functions["Vocals"]])
        elif name == "Producción":
            result.update(function_ids=[self.functions["Sound"]])
        return result

    def assert_no_registration(self):
        with self.session_factory() as db:
            self.assertEqual(set(db.scalars(select(MinistryMemberRole.id))), self.initial_role_assignments)
            self.assertIsNone(db.scalar(select(User.id).where(User.email == self.payload["email"])))
            self.assertEqual(list(db.scalars(select(MinistryMember.id).where(
                MinistryMember.ministry_id.in_(self.ministries.values())
            ))), [])
            self.assertEqual(list(db.scalars(select(MinistryMemberFunction.id).where(
                MinistryMemberFunction.function_id.in_(self.functions.values())
            ))), [])

    def assert_rejected(self, memberships, detail=None):
        with patch.object(PasswordHasher, "hash") as hash_password:
            response = self.client.post("/users/register", json={**self.payload, "memberships": memberships})
        self.assertEqual(response.status_code, 422, response.text)
        if detail is not None:
            self.assertEqual(response.json(), {"detail": detail})
        self.assertNotIn(self.payload["password"], response.text)
        hash_password.assert_not_called()
        self.assert_no_registration()

    def test_registers_multiple_memberships_and_functions_atomically(self):
        selections = [self.selection(), self.selection("Producción"), self.selection("Hospitality")]
        response = self.client.post("/users/register", json={**self.payload, "memberships": selections})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["phone"], self.payload["phone"])
        self.assertEqual(body["birth_date"], self.payload["birth_date"])
        self.assertEqual(len(body["memberships"]), 3)
        self.assertNotIn("password", body)
        self.assertNotIn("password_hash", body)
        with self.session_factory() as db:
            user = db.get(User, body["id"])
            self.assertTrue(PasswordHash.recommended().verify(self.payload["password"], user.password_hash))
            self.assertNotIn(user.password_hash, response.text)
            memberships = list(db.scalars(select(MinistryMember).where(MinistryMember.user_id == user.id)))
            by_ministry = {membership.ministry_id: membership for membership in memberships}
            self.assertEqual(set(by_ministry), {selection["ministry_id"] for selection in selections})
            for returned, selection in zip(body["memberships"], selections):
                membership = by_ministry[selection["ministry_id"]]
                self.assertEqual(returned, {
                    "id": membership.id, "ministry_id": membership.ministry_id, "status": "PENDING",
                    "function_ids": selection.get("function_ids", []),
                })
                self.assertEqual(membership.status, "PENDING")
                self.assertIsNotNone(membership.created_at)
                self.assertIsNotNone(membership.updated_at)
                self.assertIsNone(membership.deleted_at)
                assignments = membership.ministry_member_functions
                self.assertEqual({a.function_id for a in assignments}, set(selection.get("function_ids", [])))
                for assignment in assignments:
                    self.assertIsNotNone(assignment.assigned_at)
                    self.assertIsNone(assignment.revoked_at)
                self.assertEqual(len(membership.ministry_member_roles), 1)
                role = membership.ministry_member_roles[0]
                self.assertEqual(role.role_id, self.user_role_id)
                self.assertIsNone(role.revoked_at)
                self.assertIsNotNone(role.assigned_at)
                self.assertGreater(role.id, 0)

    def test_registration_without_memberships_remains_supported(self):
        response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["memberships"], [])
        with self.session_factory() as db:
            self.assertEqual(db.get(User, response.json()["id"]).ministry_members, [])

    def test_membership_without_functions_is_supported(self):
        selection = self.selection()
        selection.pop("function_ids")
        response = self.client.post("/users/register", json={**self.payload, "memberships": [selection]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["memberships"][0]["function_ids"], [])
        with self.session_factory() as db:
            membership = db.get(MinistryMember, response.json()["memberships"][0]["id"])
            self.assertEqual(membership.ministry_member_functions, [])

    def test_missing_inactive_and_deleted_ministries_are_rejected(self):
        for ministry_id in (9223372036854775807, self.ministries["Inactive"], self.ministries["Deleted"]):
            with self.subTest(ministry_id=ministry_id):
                self.assert_rejected([{"ministry_id": ministry_id}], "Selected ministry is not available.")

    def test_missing_inactive_deleted_and_wrong_ministry_functions_are_rejected(self):
        for function_id, detail in (
            (9223372036854775807, "Selected function is not available."),
            (self.functions["Inactive"], "Selected function is not available."),
            (self.functions["Deleted"], "Selected function is not available."),
            (self.functions["Sound"], "Selected function does not belong to the ministry."),
        ):
            with self.subTest(function_id=function_id):
                selection = {**self.selection(), "function_ids": [function_id]}
                self.assert_rejected([selection], detail)

    def test_nonrelational_extra_fields_are_rejected(self):
        for name in self.ministries:
            for field in ("main_instrument", "technical_area"):
                for value in (None, "Guitar"):
                    with self.subTest(ministry=name, field=field, value=value):
                        self.assert_rejected([{"ministry_id": self.ministries[name], field: value}])

    def test_duplicate_ministries_and_functions_are_rejected(self):
        selection = self.selection()
        self.assert_rejected([selection, selection])
        self.assert_rejected([{**selection, "function_ids": [self.functions["Guitar"]] * 2}])

    def test_ids_and_collection_shapes_are_validated(self):
        for value in (0, -1, True, 1.5, "1", None, 9223372036854775808):
            with self.subTest(value=value):
                self.assert_rejected([{**self.selection(), "ministry_id": value}])
                self.assert_rejected([{**self.selection(), "function_ids": [value]}])
        for value in (None, {}, "Alabanza", [None], [{}]):
            with self.subTest(memberships=value):
                self.assert_rejected(value)
        self.assert_rejected([{**self.selection(), "function_ids": None}])

    def test_roles_and_lifecycle_fields_cannot_be_self_assigned(self):
        for field, value in (
            ("role_ids", [1]), ("roles", ["ADMIN"]), ("role_id", 1),
            ("ministry_member_roles", [{"role_id": 1}]), ("status", "ACTIVE"),
            ("user_id", 1), ("id", 1), ("created_at", "2026-01-01"),
            ("assigned_at", "2026-01-01"), ("revoked_at", None), ("deleted_at", None),
        ):
            with self.subTest(field=field):
                self.assert_rejected([{**self.selection(), field: value}])
        for field in ("role_ids", "role_id", "roles", "ministry_member_roles", "ministry_id", "function_ids", "main_instrument", "technical_area"):
            with self.subTest(top_level=field):
                response = self.client.post("/users/register", json={**self.payload, field: [1]})
                self.assertEqual(response.status_code, 422, response.text)
                self.assert_no_registration()

    def test_invalid_second_membership_does_not_save_first_membership_or_user(self):
        invalid = {**self.selection("Producción"), "function_ids": [self.functions["Guitar"]]}
        self.assert_rejected([self.selection(), invalid])

    def test_late_persistence_failure_rolls_back_user_and_all_memberships(self):
        original_add = MembershipRepository.add
        calls = []

        def fail_second(repository, membership):
            original_add(repository, membership)
            calls.append(membership.id)
            if len(calls) == 2:
                raise SQLAlchemyError("private membership persistence details")

        with patch.object(MembershipRepository, "add", autospec=True, side_effect=fail_second):
            with self.assertLogs("app.repositories.user", level="ERROR"):
                response = self.client.post("/users/register", json={
                    **self.payload, "memberships": [self.selection(), self.selection("Producción")],
                })
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.assert_no_registration()
        # A retry on a new session succeeds after the rollback.
        response = self.client.post("/users/register", json={**self.payload, "memberships": [self.selection()]})
        self.assertEqual(response.status_code, 201, response.text)

    def test_function_foreign_key_failure_rolls_back_user_and_membership(self):
        missing_id = 9223372036854775807
        # Simulate a reference disappearing after validation: the real database
        # rejects the assignment, after the user/membership have been inserted.
        stale_function = Function(
            id=missing_id, ministry_id=self.ministries["Alabanza"],
            name="Unavailable", status=FunctionStatus.ACTIVE,
        )
        with patch.object(MembershipRepository, "get_functions", return_value=[stale_function]):
            with self.assertLogs("app.repositories.user", level="ERROR"):
                response = self.client.post("/users/register", json={
                    **self.payload,
                    "memberships": [{"ministry_id": self.ministries["Alabanza"], "function_ids": [missing_id]}],
                })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.assert_no_registration()

    def test_commit_failure_rolls_back_memberships_and_assignments(self):
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("private commit details")):
            with self.assertLogs("app.repositories.user", level="ERROR"):
                response = self.client.post("/users/register", json={**self.payload, "memberships": [self.selection()]})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.assert_no_registration()

    def test_catalog_lists_submittable_ids_and_excludes_unavailable_records(self):
        response = self.client.get("/ministries/registration-fields")
        self.assertEqual(response.status_code, 200, response.text)
        by_id = {ministry["id"]: ministry for ministry in response.json()}
        self.assertNotIn(self.ministries["Inactive"], by_id)
        self.assertNotIn(self.ministries["Deleted"], by_id)
        worship = by_id[self.ministries["Alabanza"]]
        self.assertEqual(set(worship["functions"]), {"Guitar", "Vocals"})
        self.assertEqual({item["id"] for item in worship["function_options"]},
                         {self.functions["Guitar"], self.functions["Vocals"]})
        for ministry in by_id.values():
            self.assertNotIn("extra_fields", ministry)
        self.assert_no_registration()

    def test_catalog_allows_configured_frontend_get_preflight(self):
        response = self.client.options("/ministries/registration-fields", headers={
            "Origin": settings.FRONTEND_ORIGIN, "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], settings.FRONTEND_ORIGIN)


class SQLiteMembershipRegistrationTests(MembershipRuleExtensionContract, MembershipRegistrationContract, unittest.TestCase):
    def test_default_role_for_single_membership_and_idempotent_assignment(self):
        response = self.client.post("/users/register", json={**self.payload, "memberships": [self.selection()]})
        self.assertEqual(response.status_code, 201, response.text)
        member_id = response.json()["memberships"][0]["id"]
        with self.session_factory() as db:
            repository = RegistrationRoleRepository(db)
            repository.assign(self.user_role_id, [member_id, member_id])
            db.commit()
            assignments = list(db.scalars(select(MinistryMemberRole).where(
                MinistryMemberRole.ministry_member_id == member_id,
            )))
            self.assertEqual(len(assignments), 1)
            self.assertEqual(assignments[0].role_id, 37)
            self.assertIsNone(assignments[0].revoked_at)

    def test_unavailable_default_role_rolls_back_and_never_falls_back(self):
        for attribute, value in (
            ("status", RoleStatus.INACTIVE),
            ("deleted_at", datetime.now(timezone.utc)),
            ("name", "NOT_USER"),
        ):
            with self.subTest(attribute=attribute):
                with self.session_factory() as db:
                    role = db.get(Role, self.user_role_id)
                    previous = getattr(role, attribute)
                    setattr(role, attribute, value)
                    db.commit()
                response = self.client.post("/users/register", json={**self.payload, "memberships": [self.selection()]})
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(response.json(), {"detail": "Unable to register user."})
                self.assert_no_registration()
                with self.session_factory() as db:
                    setattr(db.get(Role, self.user_role_id), attribute, previous)
                    db.commit()

    def test_no_memberships_does_not_require_default_role(self):
        with self.session_factory() as db:
            db.get(Role, self.user_role_id).status = RoleStatus.INACTIVE
            db.commit()
        with patch.object(RegistrationRoleRepository, "get_user_role_id") as lookup:
            response = self.client.post("/users/register", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["memberships"], [])
        lookup.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(list(db.scalars(select(MinistryMemberRole.id))), [])

    def test_role_persistence_failure_rolls_back_everything(self):
        original = RegistrationRoleRepository.assign

        def fail_after_insert(repository, role_id, membership_ids):
            original(repository, role_id, membership_ids)
            raise SQLAlchemyError("private assignment failure")

        with patch.object(RegistrationRoleRepository, "assign", autospec=True, side_effect=fail_after_insert):
            with self.assertLogs("app.repositories.user", level="ERROR"):
                response = self.client.post("/users/register", json={
                    **self.payload, "memberships": [self.selection(), self.selection("Producción")],
                })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.assert_no_registration()
        self.assertEqual(self.client.post("/users/register", json={
            **self.payload, "memberships": [self.selection()],
        }).status_code, 201)

    def test_explicit_assignment_id_collision_rolls_back_everything(self):
        with patch("app.repositories.registration_roles.secrets.randbelow", return_value=100):
            response = self.client.post("/users/register", json={
                **self.payload, "memberships": [self.selection(), self.selection("Producción")],
            })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to register user."})
        self.assert_no_registration()

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        # SQLite auto-generates IDs only for INTEGER PKs. Adapt a metadata copy,
        # keeping all production PostgreSQL types and constraints untouched.
        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            copy = table.to_metadata(metadata)
            for column in copy.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(engine)
        self.session_factory = lambda: Session(engine, autoflush=False)
        self.prepare_registration()


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "opt-in PostgreSQL tests")
class PostgreSQLMembershipRegistrationTests(MembershipRuleExtensionContract, MembershipRegistrationContract, unittest.TestCase):
    def setUp(self):
        connection = postgres_engine.connect()
        self.addCleanup(connection.close)
        transaction = connection.begin()
        self.addCleanup(transaction.rollback)
        self.session_factory = lambda: Session(
            bind=connection, autoflush=False, join_transaction_mode="create_savepoint",
        )
        self.prepare_registration()


if __name__ == "__main__":
    unittest.main()
