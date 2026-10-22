"""Role-scoped rehearsal reads with real SQL, JWT validation and serialization."""
import os
import secrets
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, MetaData, create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.dependencies.database import get_db
from app.main import app
from app.models import (
    Function, Ministry, MinistryMember, MinistryMemberFunction, MinistryMemberRole,
    Rehearsal, RehearsalAttendance, Role, User,
)
from app.models.enums import AttendanceStatus, FunctionStatus, MembershipStatus, MinistryStatus, RoleScope, RoleStatus, UserStatus
from app.repositories.authorization import AuthorizationRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.schemas.rehearsal_read import RehearsalInfo
from app.services.rehearsal_read import RehearsalReadService, RehearsalViewer
from app.services.token import TokenService


COMMON = {"id", "ministry", "name", "description", "start_at", "end_at", "tolerance_minutes", "created_at", "updated_at", "created_by"}


class RehearsalReadTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.stack.callback(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            copy = table.to_metadata(metadata)
            for column in copy.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(self.engine)
        self.db = self.stack.enter_context(Session(self.engine, autoflush=False))
        self.db.add_all([Ministry(id=i, name=f"Ministry {i}") for i in (11, 12, 13)])
        self.db.add_all([
            User(id=i, email=f"user{i}@example.com", password_hash="private-hash",
                 first_name=name, last_name="Last", status=UserStatus.ACTIVE)
            for i, name in ((101, "Admin"), (102, "Regular"), (103, "OtherAdmin"), (104, "OtherParticipant"))
        ])
        self.db.add_all([
            Role(id=17, name="ADMIN", scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True),
            Role(id=18, name="USER", scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True),
            Role(id=19, name="ADMIN", scope=RoleScope.MINISTRY, status=RoleStatus.ACTIVE, is_system_role=False),
        ])
        self.db.flush()
        self.db.add_all([
            MinistryMember(id=i, user_id=user, ministry_id=ministry, status=MembershipStatus.ACTIVE)
            for i, user, ministry in (
                (201, 101, 11), (202, 101, 12), (203, 102, 11), (204, 102, 12),
                (205, 103, 11), (206, 104, 11), (207, 104, 13),
            )
        ])
        self.db.flush()
        self.db.add_all([
            MinistryMemberRole(id=i, ministry_member_id=member, role_id=role)
            for i, member, role in ((501, 201, 17), (502, 202, 18), (503, 203, 18), (504, 205, 17))
        ])
        start = datetime(2026, 10, 25, 18, tzinfo=timezone(timedelta(hours=-5)))
        self.db.add_all([
            Rehearsal(id=i, ministry_id=ministry, created_by=103, name=f"Rehearsal {i}",
                      description=None, start_at=start + timedelta(days=day),
                      end_at=start + timedelta(days=day, hours=2), tolerance_minutes=15)
            for i, ministry, day in ((301, 11, 0), (302, 12, 0), (303, 11, 1), (304, 13, 2), (305, 11, 3))
        ])
        self.db.flush()
        self.db.add_all([
            RehearsalAttendance(id=i, rehearsal_id=rehearsal, ministry_member_id=member,
                                arrival_at=start if i == 401 else None,
                                attendance_status=AttendanceStatus.ON_TIME if i == 401 else None)
            for i, rehearsal, member in ((401, 301, 203), (402, 301, 206), (403, 302, 204), (404, 303, 206), (405, 304, 207))
        ])
        self.db.add_all([
            Function(id=601, ministry_id=11, name="Teclado", status=FunctionStatus.ACTIVE),
            Function(id=602, ministry_id=11, name="Vocalista", status=FunctionStatus.ACTIVE),
            Function(id=603, ministry_id=12, name="Wrong ministry", status=FunctionStatus.ACTIVE),
        ])
        self.db.flush()
        self.db.add_all([
            MinistryMemberFunction(id=i, ministry_member_id=203, function_id=function)
            for i, function in ((701, 601), (702, 602), (703, 603))
        ])
        self.db.commit()

        def override_db():
            yield self.db

        self.stack.enter_context(patch.dict(app.dependency_overrides, {get_db: override_db}))
        self.client = self.stack.enter_context(TestClient(app))
        self.login(101)

    def login(self, user_id):
        self.headers = {"Authorization": f"Bearer {TokenService(settings).create_access_token(user_id)}"}

    def get(self, rehearsal_id=None, **params):
        path = "/rehearsals" if rehearsal_id is None else f"/rehearsals/{rehearsal_id}"
        return self.client.get(path, headers=self.headers, params=params)

    def ids(self, **params):
        response = self.get(**params)
        self.assertEqual(response.status_code, 200, response.text)
        return [item["id"] for item in response.json()["items"]]

    def change(self, model, identifier, **values):
        entity = self.db.get(model, identifier)
        for key, value in values.items():
            setattr(entity, key, value)
        self.db.commit()

    def test_admin_multiple_ministries_other_creator_and_counts(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 4)
        self.assertEqual([item["id"] for item in body["items"]], [301, 302, 303, 305])
        self.assertEqual([item["participant_count"] for item in body["items"]], [2, 1, 1, 0])
        for item in body["items"]:
            self.assertEqual(set(item), COMMON | {"participant_count"})
            self.assertEqual(item["created_by"], {"id": 103, "first_name": "OtherAdmin", "last_name": "Last"})
            self.assertNotIn("private-hash", str(item))
        self.assertEqual(self.ids(ministry_id=11), [301, 303, 305])
        self.assertEqual(self.get(304).status_code, 404)
        self.assertEqual(self.ids(ministry_id=13), [])

    def test_admin_single_active_ministry(self):
        self.change(MinistryMember, 202, status=MembershipStatus.INACTIVE)
        self.assertEqual(self.ids(), [301, 303, 305])
        self.assertEqual(self.get(302).status_code, 404)

    def test_admin_detail_all_participants_and_grouped_functions(self):
        response = self.get(301)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), COMMON | {"participants"})
        self.assertEqual(body["ministry"], {"id": 11, "name": "Ministry 11"})
        self.assertEqual(body["participants"], [
            {"ministry_member_id": 203, "user_id": 102, "first_name": "Regular", "last_name": "Last",
             "functions": ["Teclado", "Vocalista"], "arrival_at": body["participants"][0]["arrival_at"], "attendance_status": "ON_TIME"},
            {"ministry_member_id": 206, "user_id": 104, "first_name": "OtherParticipant", "last_name": "Last",
             "functions": [], "arrival_at": None, "attendance_status": None},
        ])
        self.assertNotIn("username", response.text)
        self.assertIsNotNone(body["participants"][0]["arrival_at"])
        self.assertEqual(self.get(305).json()["participants"], [])

    def test_admin_participant_history_preserved(self):
        self.change(MinistryMember, 203, status=MembershipStatus.INACTIVE)
        self.change(User, 102, deleted_at=datetime.now(timezone.utc))
        self.assertEqual(len(self.get(301).json()["participants"]), 2)
        self.assertEqual(self.get().json()["items"][0]["participant_count"], 2)

    def test_user_only_assigned_across_ministries_no_participant_data(self):
        self.login(102)
        with patch.object(RehearsalReadRepository, "get_participants", side_effect=AssertionError("USER must not load participants")):
            self.assertEqual(self.ids(), [301, 302])
            for identifier in (301, 302):
                body = self.get(identifier).json()
                self.assertEqual(set(body), COMMON)
                for secret in ("participants", "participant_count", "attendance_status", "arrival_at", "functions", "OtherParticipant", "Regular"):
                    self.assertNotIn(secret, str(body))
        for item in self.get().json()["items"]:
            self.assertEqual(set(item), COMMON)
        self.assertEqual(self.get(303).status_code, 404)
        self.assertEqual(self.get(304).status_code, 404)
        self.assertEqual(self.get(305).status_code, 404)

    def test_user_filter_and_client_ids_cannot_expand_visibility(self):
        self.login(102)
        self.assertEqual(self.ids(ministry_id=11, user_id=101, role="ADMIN"), [301])
        self.assertEqual(self.get(ministry_id=13).json(), {"items": [], "total": 0})
        self.assertEqual(self.get(304, user_id=101, role="ADMIN").status_code, 404)

    def test_admin_takes_precedence_and_role_changes_apply_immediately(self):
        self.assertEqual(self.ids(), [301, 302, 303, 305])
        self.change(MinistryMemberRole, 501, revoked_at=datetime.now(timezone.utc))
        # Same JWT, remaining GLOBAL USER role, no selected attendance for admin.
        self.assertEqual(self.get().json(), {"items": [], "total": 0})
        self.assertEqual(self.get(301).status_code, 404)
        self.change(MinistryMemberRole, 501, revoked_at=None)
        self.assertEqual(self.ids(), [301, 302, 303, 305])
        self.assertIn("participants", self.get(301).json())

    def test_no_valid_role_forbidden_in_both_endpoints(self):
        self.login(104)
        for identifier in (None, 301):
            self.assertEqual(self.get(identifier).status_code, 403)

    def test_revoked_inactive_deleted_wrong_scope_roles_denied(self):
        self.login(102)
        changes = (
            (MinistryMemberRole, 503, "revoked_at", datetime.now(timezone.utc)),
            (Role, 18, "status", RoleStatus.INACTIVE),
            (Role, 18, "deleted_at", datetime.now(timezone.utc)),
            (Role, 18, "scope", RoleScope.MINISTRY),
            (Role, 18, "name", "UNRELATED"),
        )
        for model, identifier, field, value in changes:
            with self.subTest(field=field, value=value):
                original = getattr(self.db.get(model, identifier), field)
                self.change(model, identifier, **{field: value})
                self.assertEqual(self.get().status_code, 403)
                self.assertEqual(self.get(301).status_code, 403)
                self.change(model, identifier, **{field: original})

    def test_invalid_role_memberships_do_not_authorize(self):
        self.login(102)
        for field, value in (("status", MembershipStatus.PENDING), ("status", MembershipStatus.INACTIVE),
                             ("deleted_at", datetime.now(timezone.utc))):
            with self.subTest(field=field, value=value):
                original = getattr(self.db.get(MinistryMember, 203), field)
                self.change(MinistryMember, 203, **{field: value})
                self.assertEqual(self.get().status_code, 403)
                self.assertEqual(self.get(301).status_code, 403)
                self.change(MinistryMember, 203, **{field: original})

    def test_user_attendance_membership_must_remain_eligible(self):
        self.login(102)
        for field, value in (("status", MembershipStatus.PENDING), ("status", MembershipStatus.INACTIVE),
                             ("deleted_at", datetime.now(timezone.utc))):
            with self.subTest(field=field, value=value):
                original = getattr(self.db.get(MinistryMember, 204), field)
                self.change(MinistryMember, 204, **{field: value})
                self.assertEqual(self.ids(), [301])
                self.assertEqual(self.get(302).status_code, 404)
                self.change(MinistryMember, 204, **{field: original})

    def test_admin_inactive_deleted_memberships_do_not_expand_scope(self):
        for field, value in (("status", MembershipStatus.PENDING), ("status", MembershipStatus.INACTIVE),
                             ("deleted_at", datetime.now(timezone.utc))):
            with self.subTest(field=field, value=value):
                original = getattr(self.db.get(MinistryMember, 202), field)
                self.change(MinistryMember, 202, **{field: value})
                self.assertEqual(self.ids(), [301, 303, 305])
                self.assertEqual(self.get(302).status_code, 404)
                self.change(MinistryMember, 202, **{field: original})

    def test_inactive_deleted_user_denied(self):
        for field, value in (("status", UserStatus.INACTIVE), ("deleted_at", datetime.now(timezone.utc))):
            original = getattr(self.db.get(User, 101), field)
            self.change(User, 101, **{field: value})
            self.assertEqual(self.get().status_code, 403)
            self.assertEqual(self.get(301).status_code, 403)
            self.change(User, 101, **{field: original})

    def test_inactive_deleted_ministries_excluded_for_both_roles(self):
        for field, value in (("status", MinistryStatus.INACTIVE), ("deleted_at", datetime.now(timezone.utc))):
            for actor in (101, 102):
                self.login(actor)
                original = getattr(self.db.get(Ministry, 12), field)
                self.change(Ministry, 12, **{field: value})
                self.assertNotIn(302, self.ids())
                self.assertEqual(self.get(302).status_code, 404)
                self.change(Ministry, 12, **{field: original})

    def test_pagination_total_sort_and_filter(self):
        response = self.get(limit=1, offset=1).json()
        self.assertEqual(response["total"], 4)
        self.assertEqual([item["id"] for item in response["items"]], [302])
        self.assertEqual(self.get(limit=1, offset=100).json(), {"items": [], "total": 4})
        response = self.get(limit=1, offset=1, ministry_id=11).json()
        self.assertEqual(response["total"], 3)
        self.assertEqual([item["id"] for item in response["items"]], [303])
        self.login(102)
        self.assertEqual(self.get(limit=1, offset=1).json()["total"], 2)
        self.assertEqual(self.ids(limit=1, offset=1), [302])

    def test_no_duplicate_rehearsals_from_roles_or_functions(self):
        self.db.add(MinistryMemberRole(id=505, ministry_member_id=202, role_id=17))
        self.db.commit()
        self.assertEqual(self.ids(), [301, 302, 303, 305])
        self.assertEqual(len(self.get(301).json()["participants"]), 2)

    def test_invalid_ids_and_pagination(self):
        for identifier in (0, -1, "abc", "1.5", 9223372036854775808):
            self.assertEqual(self.get(identifier).status_code, 422)
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "abc"},
                       {"ministry_id": 0}, {"ministry_id": 9223372036854775808}):
            self.assertEqual(self.get(**params).status_code, 422)
        self.assertEqual(self.get(99999).status_code, 404)

    def test_missing_invalid_expired_and_forged_claims(self):
        key = settings.JWT_SECRET_KEY.get_secret_value()
        tokens = [None, "invalid", jwt.encode({"sub": "101", "exp": 1}, key, algorithm=settings.JWT_ALGORITHM),
                  jwt.encode({"sub": "101", "exp": 9999999999}, secrets.token_urlsafe(48), algorithm=settings.JWT_ALGORITHM)]
        for token in tokens:
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            for path in ("/rehearsals", "/rehearsals/301"):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
        token = jwt.encode({"sub": "102", "exp": 9999999999, "role": "ADMIN", "user_id": 101}, key, algorithm=settings.JWT_ALGORITHM)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.ids(), [301, 302])
        self.assertNotIn("participants", self.get(301).json())

    def test_unavailable_functions_excluded(self):
        self.change(MinistryMemberFunction, 701, revoked_at=datetime.now(timezone.utc))
        self.change(Function, 602, status=FunctionStatus.INACTIVE)
        self.assertEqual(self.get(301).json()["participants"][0]["functions"], [])
        self.change(Function, 602, status=FunctionStatus.ACTIVE, deleted_at=datetime.now(timezone.utc))
        self.assertEqual(self.get(301).json()["participants"][0]["functions"], [])

    def test_query_counts_do_not_grow_with_page_or_participants(self):
        def capture_request(identifier=None, **params):
            statements = []
            self.db.expire_all()

            def capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(self.engine, "before_cursor_execute", capture)
            try:
                self.assertEqual(self.get(identifier, **params).status_code, 200)
            finally:
                event.remove(self.engine, "before_cursor_execute", capture)
            self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements))
            return statements

        self.assertEqual(len(capture_request(limit=1)), 4)
        self.assertEqual(len(capture_request(limit=100)), 4)
        self.assertEqual(len(capture_request(301)), 4)
        self.assertEqual(len(capture_request(305)), 4)
        self.login(102)
        self.assertEqual(len(capture_request(limit=100)), 4)
        statements = capture_request(301)
        self.assertEqual(len(statements), 3)
        self.assertFalse(any("arrival_at" in sql or "attendance_status" in sql for sql in statements))

    def test_database_errors_fail_closed(self):
        with patch.object(AuthorizationRepository, "get_global_roles", side_effect=SQLAlchemyError("private")):
            self.assertEqual(self.get().json(), {"detail": "Unable to authorize user."})
            self.assertEqual(self.get(301).status_code, 500)
        for method, identifier in (("list_visible", None), ("get_visible", 301), ("get_participants", 301)):
            with patch.object(RehearsalReadRepository, method, side_effect=SQLAlchemyError("private")):
                response = self.get(identifier)
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("private", response.text)

    def test_timezone_values_and_user_schema_preserved_in_service(self):
        aware = datetime(2026, 10, 25, 18, tzinfo=timezone(timedelta(hours=-5)))
        repository = Mock(spec=RehearsalReadRepository)
        row = dict(self.db.execute(RehearsalReadRepository(self.db)._metadata().where(Rehearsal.id == 301)).mappings().one())
        for key in ("start_at", "end_at", "created_at", "updated_at"):
            row[key] = aware
        repository.get_visible.return_value = row
        result = RehearsalReadService(repository).get_visible(RehearsalViewer(102, False), 301)
        self.assertIs(type(result), RehearsalInfo)
        self.assertEqual(result.start_at.utcoffset(), timedelta(hours=-5))
        self.assertIn("-05:00", result.model_dump_json())
        repository.get_participants.assert_not_called()
