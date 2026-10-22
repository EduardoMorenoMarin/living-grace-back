"""AFTER-LSP: the same persistence and HTTP contract applies to both creators."""

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

import jwt
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, MetaData, create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, engine as postgres_engine
from app.dependencies.database import get_db
from app.dependencies.rehearsals import get_rehearsal_creator
from app.main import app
from app.models.ministry import Ministry
from app.models.enums import MembershipStatus, MinistryStatus, RoleScope, RoleStatus, UserStatus
from app.models.ministry_member import MinistryMember
from app.models.ministry_member_role import MinistryMemberRole
from app.models.role import Role
from app.models.rehearsal import Rehearsal
from app.models.rehearsal_attendance import RehearsalAttendance
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.authorization import AuthorizationRepository
from app.services.rehearsal_creation import LoggingRehearsalCreator, RehearsalCreator
from app.services.token import TokenService


class RehearsalContract:
    # None exercises production wiring; base subclasses override only the creator.
    creator_type = None

    def prepare(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        with self.session_factory() as db:
            unique = uuid4().hex
            user = User(email=f"rehearsal-{unique}@example.com",
                        password_hash="unused", first_name="Test", last_name="Creator")
            ministry = Ministry(name="Rehearsal test")
            db.add_all([user, ministry])
            db.flush()
            self.user_id, self.ministry_id = user.id, ministry.id
            if db.get_bind().dialect.name == "sqlite":
                db.add_all([
                    Role(id=17, name="ADMIN", scope=RoleScope.GLOBAL,
                         status=RoleStatus.ACTIVE, is_system_role=True),
                    Role(id=18, name="USER", scope=RoleScope.GLOBAL,
                         status=RoleStatus.ACTIVE, is_system_role=True),
                    Role(id=19, name="ADMIN", scope=RoleScope.MINISTRY,
                         status=RoleStatus.ACTIVE, is_system_role=False),
                ])
                db.flush()
            self.admin_role_id = db.scalar(select(Role.id).where(
                Role.name == "ADMIN", Role.scope == RoleScope.GLOBAL,
                Role.status == RoleStatus.ACTIVE, Role.deleted_at.is_(None),
            ))
            self.assertIsNotNone(self.admin_role_id)
            member = MinistryMember(user_id=user.id, ministry_id=ministry.id, status=MembershipStatus.ACTIVE)
            db.add(member)
            db.flush()
            self.member_id = member.id
            self.assignment_id = secrets.randbelow(9223372036854775807) + 1
            db.add(MinistryMemberRole(id=self.assignment_id, ministry_member_id=member.id,
                                     role_id=self.admin_role_id))
            self.participant_ids = []
            for index in range(3):
                participant = User(
                    email=f"participant-{index}-{unique}@example.com",
                    password_hash="unused", first_name="Participant", last_name=str(index),
                )
                db.add(participant)
                db.flush()
                participant_member = MinistryMember(
                    user_id=participant.id, ministry_id=ministry.id, status=MembershipStatus.ACTIVE,
                )
                db.add(participant_member)
                db.flush()
                self.participant_ids.append(participant_member.id)
            db.commit()

        def override_db():
            with self.session_factory() as db:
                yield db

        stack.enter_context(patch.dict(app.dependency_overrides, {get_db: override_db}))
        if self.creator_type is not None:
            def creator(db: Session = Depends(get_db)) -> RehearsalCreator:
                return self.creator_type(RehearsalRepository(db), MembershipRepository(db))

            stack.enter_context(patch.dict(app.dependency_overrides, {get_rehearsal_creator: creator}))
        self.client = stack.enter_context(TestClient(app))
        self.headers = {"Authorization": f"Bearer {TokenService(settings).create_access_token(self.user_id)}"}
        self.payload = {
            "ministry_id": self.ministry_id, "name": "Worship Team Rehearsal",
            "start_at": "2026-10-03T19:00:00-05:00", "end_at": "2026-10-03T21:00:00-05:00",
            "ministry_member_ids": self.participant_ids[:1],
        }

    def post(self, **changes):
        return self.client.post("/rehearsals", headers=self.headers, json={**self.payload, **changes})

    def rows(self):
        with self.session_factory() as db:
            return list(db.scalars(select(Rehearsal).where(Rehearsal.ministry_id == self.ministry_id)))

    def attendance_rows(self):
        with self.session_factory() as db:
            return list(db.scalars(select(RehearsalAttendance).where(
                RehearsalAttendance.ministry_member_id.in_(self.participant_ids),
            )))

    def test_multiple_selected_participants_are_persisted_with_null_attendance(self):
        response = self.post(ministry_member_ids=self.participant_ids[:2])
        self.assertEqual(response.status_code, 201, response.text)
        records = self.attendance_rows()
        self.assertEqual(len(records), 2)
        self.assertEqual({row.ministry_member_id for row in records}, set(self.participant_ids[:2]))
        self.assertEqual({row.rehearsal_id for row in records}, {response.json()["id"]})
        self.assertEqual(len({row.id for row in records}), 2)
        for record in records:
            self.assertTrue(0 < record.id <= 9223372036854775807)
            self.assertIsNone(record.arrival_at)
            self.assertIsNone(record.attendance_status)

    def test_participant_list_validation(self):
        invalid = [[], None, {}, "1", [self.participant_ids[0]] * 2]
        invalid += [[value] for value in (0, -1, True, "1", 1.5, None, 9223372036854775808)]
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.post(ministry_member_ids=value).status_code, 422)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attendance_rows(), [])

    def test_nonexistent_participant_rejected_before_inserts(self):
        with patch.object(RehearsalRepository, "add") as add:
            response = self.post(ministry_member_ids=[self.participant_ids[0], 9223372036854775807])
        self.assertEqual(response.status_code, 422)
        add.assert_not_called()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attendance_rows(), [])

    def test_ineligible_participants_rejected_before_inserts(self):
        member_id = self.participant_ids[1]
        for target, attribute, value in (
            (MinistryMember, "status", MembershipStatus.PENDING),
            (MinistryMember, "status", MembershipStatus.INACTIVE),
            (MinistryMember, "deleted_at", datetime.now(timezone.utc)),
            (User, "status", UserStatus.INACTIVE),
            (User, "deleted_at", datetime.now(timezone.utc)),
        ):
            with self.subTest(target=target, attribute=attribute, value=value):
                with self.session_factory() as db:
                    member = db.get(MinistryMember, member_id)
                    entity = member if target is MinistryMember else db.get(User, member.user_id)
                    identifier = entity.id
                    original = getattr(entity, attribute)
                    setattr(entity, attribute, value)
                    db.commit()
                with patch.object(RehearsalRepository, "add") as add:
                    response = self.post(ministry_member_ids=self.participant_ids[:2])
                self.assertEqual(response.status_code, 422, response.text)
                add.assert_not_called()
                self.assertEqual(self.rows(), [])
                self.assertEqual(self.attendance_rows(), [])
                with self.session_factory() as db:
                    setattr(db.get(target, identifier), attribute, original)
                    db.commit()

    def test_other_ministry_participant_rejected(self):
        with self.session_factory() as db:
            ministry = Ministry(name="Other participants")
            db.add(ministry)
            db.flush()
            db.get(MinistryMember, self.participant_ids[1]).ministry_id = ministry.id
            db.commit()
        with patch.object(RehearsalRepository, "add") as add:
            response = self.post(ministry_member_ids=self.participant_ids[:2])
        self.assertEqual(response.status_code, 422)
        add.assert_not_called()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attendance_rows(), [])

    def test_attendance_insert_failure_rolls_back_all_records(self):
        original = RehearsalRepository.add_attendance

        def fail_after_partial_insert(repository, rehearsal_id, member_ids):
            original(repository, rehearsal_id, member_ids[:1])
            raise SQLAlchemyError("private attendance failure")

        with patch.object(RehearsalRepository, "add_attendance", autospec=True, side_effect=fail_after_partial_insert):
            response = self.post(ministry_member_ids=self.participant_ids[:2])
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to create rehearsal."})
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attendance_rows(), [])
        self.assertEqual(self.post(ministry_member_ids=self.participant_ids[:2]).status_code, 201)
        self.assertEqual(len(self.attendance_rows()), 2)

    def test_attendance_id_collision_rolls_back_all_records(self):
        with patch("app.repositories.rehearsal.secrets.randbelow", return_value=123):
            response = self.post(ministry_member_ids=self.participant_ids[:2])
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attendance_rows(), [])

    def test_success_persists_authenticated_creator_and_server_fields(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["created_by"], self.user_id)
        self.assertEqual(body["ministry_id"], self.ministry_id)
        self.assertEqual(body["name"], self.payload["name"])
        self.assertIsNone(body["description"])
        self.assertEqual(body["tolerance_minutes"], 0)
        for field in ("id", "created_at", "updated_at"):
            self.assertTrue(body[field])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, body["id"])
        self.assertEqual(rows[0].created_by, self.user_id)

    def test_nonexistent_ministry(self):
        response = self.post(ministry_id=9223372036854775807)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Ministry does not exist."})
        self.assertEqual(self.rows(), [])

    def test_unauthenticated(self):
        response = self.client.post("/rehearsals", json=self.payload)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.rows(), [])

    def test_invalid_expired_and_unknown_user_tokens(self):
        key = settings.JWT_SECRET_KEY.get_secret_value()
        algorithm = settings.JWT_ALGORITHM
        tokens = [
            "invalid",
            jwt.encode({"sub": str(self.user_id), "exp": 1}, key, algorithm=algorithm),
            jwt.encode({"sub": str(self.user_id)}, key, algorithm=algorithm),
            jwt.encode({"sub": "not-an-id", "exp": 9999999999}, key, algorithm=algorithm),
            TokenService(settings).create_access_token(9223372036854775807),
            jwt.encode({"sub": str(self.user_id), "exp": 9999999999}, secrets.token_urlsafe(48), algorithm=algorithm),
        ]
        for token in tokens:
            with self.subTest(token=tokens.index(token)):
                response = self.client.post("/rehearsals", json=self.payload,
                                            headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.rows(), [])

    def test_invalid_request_and_server_owned_fields(self):
        invalid = [
            {"ministry_id": 0}, {"ministry_id": "1"}, {"ministry_id": True},
            {"name": "  "}, {"start_at": "not-a-date"}, {"start_at": "2026-10-03T19:00:00"},
            {"end_at": self.payload["start_at"]}, {"tolerance_minutes": -1},
            {"id": 99}, {"created_by": 99}, {"created_at": "2026-01-01T00:00:00Z"},
            {"updated_at": "2026-01-01T00:00:00Z"}, {"location": "Auditorium"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(**changes).status_code, 422)
        for field in ("ministry_id", "name", "start_at", "end_at", "ministry_member_ids"):
            payload = {key: value for key, value in self.payload.items() if key != field}
            self.assertEqual(self.client.post("/rehearsals", headers=self.headers, json=payload).status_code, 422)
        self.assertEqual(self.rows(), [])

    def test_commit_failure_rolls_back_and_allows_retry(self):
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("private details")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to create rehearsal."})
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.attendance_rows(), [])
        self.assertEqual(self.post().status_code, 201)

    def test_foreign_key_failure_rolls_back(self):
        with patch.object(MembershipRepository, "get_ministry", return_value=Ministry(id=9223372036854775807)):
            with patch.object(RehearsalRepository, "eligible_participant_ids", return_value=set(self.payload["ministry_member_ids"])):
                response = self.post(ministry_id=9223372036854775807)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to create rehearsal."})
        self.assertEqual(self.rows(), [])

    def test_lsp_contract_accepts_optional_description(self):
        self.assertEqual(self.post().status_code, 201)
        for description in (None, "", "   ", "Weekly rehearsal"):
            with self.subTest(description=description):
                response = self.post(description=description)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()["description"], description)
        self.assertCountEqual([row.description for row in self.rows()],
                              [None, None, "", "   ", "Weekly rehearsal"])

    def test_logging_failure_does_not_change_creation_result(self):
        with patch("app.services.rehearsal_creation.logger.info", side_effect=RuntimeError("handler failure")):
            response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len(self.rows()), 1)

    def test_subtype_logs_only_successful_creation(self):
        with self.session_factory() as db:
            self.assertIsInstance(get_rehearsal_creator(db), LoggingRehearsalCreator)
        with patch("app.services.rehearsal_creation.logger.info") as log:
            response = self.post()
            self.assertEqual(response.status_code, 201)
            if self.creator_type is None:
                log.assert_called_once_with("Created rehearsal %s", response.json()["id"])
            else:
                log.assert_not_called()
            log.reset_mock()
            with patch.object(Session, "commit", side_effect=SQLAlchemyError("commit failure")):
                self.assertEqual(self.post().status_code, 500)
            log.assert_not_called()
        self.assertEqual(len(self.rows()), 1)


class SQLiteRehearsalTests(RehearsalContract, unittest.TestCase):
    def assert_forbidden_without_insert(self):
        with patch.object(RehearsalRepository, "add") as add:
            response = self.post()
        self.assertEqual(response.status_code, 403, response.text)
        add.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_user_role_cannot_create(self):
        with self.session_factory() as db:
            db.get(MinistryMemberRole, self.assignment_id).role_id = 18
            db.commit()
        self.assert_forbidden_without_insert()

    def test_no_role_cannot_create_even_with_admin_jwt_claims(self):
        with self.session_factory() as db:
            db.delete(db.get(MinistryMemberRole, self.assignment_id))
            db.commit()
        token = jwt.encode(
            {"sub": str(self.user_id), "exp": 9999999999, "role": "ADMIN", "roles": ["ADMIN"]},
            settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM,
        )
        self.headers = {"Authorization": f"Bearer {token}"}
        self.assert_forbidden_without_insert()

    def test_revoked_admin_is_denied(self):
        with self.session_factory() as db:
            db.get(MinistryMemberRole, self.assignment_id).revoked_at = datetime.now(timezone.utc)
            db.commit()
        self.assert_forbidden_without_insert()

    def test_inactive_or_deleted_admin_role_is_denied(self):
        for attribute, value in (("status", RoleStatus.INACTIVE), ("deleted_at", datetime.now(timezone.utc))):
            with self.subTest(attribute=attribute):
                with self.session_factory() as db:
                    role = db.get(Role, self.admin_role_id)
                    original = getattr(role, attribute)
                    setattr(role, attribute, value)
                    db.commit()
                self.assert_forbidden_without_insert()
                with self.session_factory() as db:
                    setattr(db.get(Role, self.admin_role_id), attribute, original)
                    db.commit()

    def test_ministry_scoped_admin_is_denied(self):
        with self.session_factory() as db:
            db.get(MinistryMemberRole, self.assignment_id).role_id = 19
            db.commit()
        self.assert_forbidden_without_insert()

    def test_pending_inactive_or_deleted_membership_is_denied(self):
        for attribute, value in (
            ("status", MembershipStatus.PENDING), ("status", MembershipStatus.INACTIVE),
            ("deleted_at", datetime.now(timezone.utc)),
        ):
            with self.subTest(attribute=attribute, value=value):
                with self.session_factory() as db:
                    member = db.get(MinistryMember, self.member_id)
                    original = getattr(member, attribute)
                    setattr(member, attribute, value)
                    db.commit()
                self.assert_forbidden_without_insert()
                with self.session_factory() as db:
                    setattr(db.get(MinistryMember, self.member_id), attribute, original)
                    db.commit()

    def test_unavailable_user_or_ministry_is_denied(self):
        for model, identifier, attribute, value in (
            (User, self.user_id, "status", UserStatus.INACTIVE),
            (User, self.user_id, "deleted_at", datetime.now(timezone.utc)),
            (Ministry, self.ministry_id, "status", MinistryStatus.INACTIVE),
            (Ministry, self.ministry_id, "deleted_at", datetime.now(timezone.utc)),
        ):
            with self.subTest(model=model, attribute=attribute):
                with self.session_factory() as db:
                    entity = db.get(model, identifier)
                    original = getattr(entity, attribute)
                    setattr(entity, attribute, value)
                    db.commit()
                self.assert_forbidden_without_insert()
                with self.session_factory() as db:
                    setattr(db.get(model, identifier), attribute, original)
                    db.commit()

    def test_global_admin_in_another_ministry_can_create(self):
        with self.session_factory() as db:
            ministry = Ministry(name="Another ministry")
            db.add(ministry)
            db.flush()
            member = MinistryMember(user_id=self.user_id, ministry_id=ministry.id, status=MembershipStatus.ACTIVE)
            db.add(member)
            db.flush()
            # Only the second ministry grants ADMIN; GLOBAL applies to either.
            db.get(MinistryMemberRole, self.assignment_id).role_id = 18
            db.add(MinistryMemberRole(id=self.assignment_id + 1, ministry_member_id=member.id,
                                     role_id=self.admin_role_id))
            db.commit()
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["created_by"], self.user_id)

    def test_other_users_admin_does_not_grant_permission(self):
        with self.session_factory() as db:
            other = User(email="other@example.com", password_hash="unused",
                         first_name="Other", last_name="User")
            db.add(other)
            db.flush()
            db.get(MinistryMember, self.member_id).user_id = other.id
            db.commit()
        self.assert_forbidden_without_insert()

    def test_role_changes_apply_to_existing_jwt(self):
        with self.session_factory() as db:
            db.get(MinistryMemberRole, self.assignment_id).role_id = 18
            db.commit()
        self.assert_forbidden_without_insert()
        with self.session_factory() as db:
            db.get(MinistryMemberRole, self.assignment_id).role_id = self.admin_role_id
            db.commit()
        self.assertEqual(self.post().status_code, 201)
        with self.session_factory() as db:
            db.get(MinistryMemberRole, self.assignment_id).revoked_at = datetime.now(timezone.utc)
            db.commit()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(len(self.rows()), 1)

    def test_authorization_database_failure_does_not_insert(self):
        with patch.object(AuthorizationRepository, "has_global_role", side_effect=SQLAlchemyError("private details")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to authorize user."})
        self.assertEqual(self.rows(), [])

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            copy = table.to_metadata(metadata)
            for column in copy.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(engine)
        self.session_factory = lambda: Session(engine, autoflush=False)
        self.prepare()


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "opt-in PostgreSQL tests")
class PostgreSQLRehearsalTests(RehearsalContract, unittest.TestCase):
    def setUp(self):
        connection = postgres_engine.connect()
        self.addCleanup(connection.close)
        transaction = connection.begin()
        self.addCleanup(transaction.rollback)
        self.session_factory = lambda: Session(bind=connection, autoflush=False, join_transaction_mode="create_savepoint")
        self.prepare()


class SQLiteBaseRehearsalTests(SQLiteRehearsalTests):
    creator_type = RehearsalCreator


class PostgreSQLBaseRehearsalTests(PostgreSQLRehearsalTests):
    creator_type = RehearsalCreator


if __name__ == "__main__":
    unittest.main()
