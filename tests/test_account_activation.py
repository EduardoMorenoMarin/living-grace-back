"""Account access follows active membership and preserves suspension/admin invariants."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_membership_notifications as fixtures
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.models import MinistryMember, Notification, User
from app.models.enums import MembershipStatus, UserStatus
from app.services.password_hasher import PasswordHasher
from app.services.token import TokenService


class AccountActivationTests(unittest.TestCase):
    setUp = fixtures.MembershipNotificationTests.setUp
    login = fixtures.MembershipNotificationTests.login
    change = fixtures.MembershipNotificationTests.change
    payload = fixtures.MembershipNotificationTests.payload
    register = fixtures.MembershipNotificationTests.register
    requested = fixtures.MembershipNotificationTests.requested
    approve = fixtures.MembershipNotificationTests.approve
    notices = fixtures.MembershipNotificationTests.notices
    ticket = fixtures.MembershipNotificationTests.ticket
    connect = fixtures.MembershipNotificationTests.connect

    def account_login(self, email, password="New-membership-password-123!"):
        return self.client.post("/auth/login", json={"identifier": email, "password": password})

    def jwt_for(self, user_id):
        user = self.db.get(User, user_id)
        return {"Authorization": "Bearer " + TokenService(settings)
                .create_access_token(user.id, user.password_hash)}

    def test_new_account_and_requested_memberships_are_pending_even_without_selections(self):
        for ministries in ((11, 12), ()):
            with self.subTest(ministries=ministries):
                body = self.requested(ministries)
                self.assertEqual(body["status"], "PENDING")
                self.assertEqual(self.db.get(User, body["id"]).status, UserStatus.PENDING)
                self.assertTrue(all(row["status"] == "PENDING" for row in body["memberships"]))
                response = self.account_login(body["email"])
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"detail": "Invalid credentials."})
                self.assertNotIn("access_token", response.json())

    def test_pending_jwt_is_rejected_everywhere_including_ws_ticket(self):
        body = self.requested()
        headers = self.jwt_for(body["id"])
        for path in ("/users/me", "/notifications", "/notifications/unread-count", "/dashboard/me", "/rehearsals"):
            self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/notifications/ws-ticket", headers=headers).status_code, 401)
        # Even an incorrectly preassigned active membership cannot bypass PENDING account state.
        self.change(MinistryMember, body["memberships"][0]["id"], status=MembershipStatus.ACTIVE)
        self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 401)

    def test_first_approval_activates_only_target_and_allows_login(self):
        body = self.requested((11, 12))
        other_status = self.db.get(User, 104).status
        response = self.approve(body["memberships"][0]["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, body["id"]).status, UserStatus.ACTIVE)
        self.assertEqual(self.db.get(User, 104).status, other_status)
        self.assertEqual(self.db.get(MinistryMember, body["memberships"][1]["id"]).status, MembershipStatus.PENDING)
        response = self.account_login(body["email"])
        self.assertEqual(response.status_code, 200, response.text)
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 200)
        notices = self.notices("MEMBERSHIP_APPROVED")
        self.assertEqual([row.user_id for row in notices], [body["id"]])

    def test_multiple_memberships_keep_access_until_last_active_membership_is_lost(self):
        body = self.requested((11, 12))
        first, second = [row["id"] for row in body["memberships"]]
        self.assertEqual(self.approve(first).status_code, 200)
        self.assertEqual(self.approve(second, ministry_id=12).status_code, 200)
        headers = self.jwt_for(body["id"])
        self.assertEqual(self.approve(first, status="INACTIVE").status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, body["id"]).status, UserStatus.ACTIVE)
        self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 200)
        self.assertEqual(self.approve(second, ministry_id=12, status="PENDING").status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, body["id"]).status, UserStatus.PENDING)
        self.assertEqual(self.account_login(body["email"]).status_code, 401)
        self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/notifications/ws-ticket", headers=headers).status_code, 401)

    def test_suspended_deleted_and_pending_accounts_cannot_log_in_or_use_old_jwts(self):
        password = "Activation-password-123!"
        self.change(User, 102, password_hash=PasswordHasher().hash(password))
        headers = self.jwt_for(102)
        self.assertEqual(self.account_login("user102@example.com", password).status_code, 200)
        for field, value in (("status", UserStatus.INACTIVE), ("status", UserStatus.PENDING),
                             ("deleted_at", datetime.now(timezone.utc))):
            old = getattr(self.db.get(User, 102), field)
            self.change(User, 102, **{field: value})
            self.assertEqual(self.account_login("user102@example.com", password).status_code, 401)
            self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 401)
            self.change(User, 102, **{field: old})

    def test_suspended_and_deleted_users_are_never_activated_by_approval(self):
        for field, value in (("status", UserStatus.INACTIVE), ("deleted_at", datetime.now(timezone.utc))):
            body = self.requested()
            self.change(User, body["id"], **{field: value})
            self.assertEqual(self.approve(body["memberships"][0]["id"]).status_code, 404)
            self.db.expire_all()
            self.assertEqual(getattr(self.db.get(User, body["id"]), field),
                             value.replace(tzinfo=None) if isinstance(value, datetime) else value)
            self.assertEqual(self.db.get(MinistryMember, body["memberships"][0]["id"]).status, MembershipStatus.PENDING)

    def test_commit_failure_restores_membership_account_and_notifications(self):
        body = self.requested()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("commit failed")):
            self.assertEqual(self.approve(body["memberships"][0]["id"]).status_code, 500)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, body["id"]).status, UserStatus.PENDING)
        self.assertEqual(self.db.get(MinistryMember, body["memberships"][0]["id"]).status, MembershipStatus.PENDING)
        self.assertEqual(self.notices("MEMBERSHIP_APPROVED"), [])
        self.assertEqual(self.approve(body["memberships"][0]["id"]).status_code, 200)
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("commit failed")):
            self.assertEqual(self.approve(body["memberships"][0]["id"], status="INACTIVE").status_code, 500)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, body["id"]).status, UserStatus.ACTIVE)

    def test_global_admins_remain_manageable_and_last_effective_admin_is_protected(self):
        # The second admin may be deactivated while the first remains eligible.
        self.assertEqual(self.approve(205, status="INACTIVE").status_code, 200)
        self.assertEqual(self.approve(201, status="INACTIVE").status_code, 409)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, 101).status, UserStatus.ACTIVE)
        self.assertEqual(self.db.get(MinistryMember, 201).status, MembershipStatus.ACTIVE)
        self.assertEqual(self.client.get("/ministries/11/members?status=PENDING", headers=self.headers).status_code, 200)
        body = self.requested()
        self.assertEqual(self.approve(body["memberships"][0]["id"]).status_code, 200)

    def test_legacy_active_account_without_eligible_membership_is_denied_without_get_writes(self):
        self.change(MinistryMember, 205, deleted_at=datetime.now(timezone.utc))
        headers = self.jwt_for(103)
        self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 401)
        self.db.expire_all()
        self.assertEqual(self.db.get(User, 103).status, UserStatus.ACTIVE)  # read paths never reconcile by writing

    def test_active_socket_and_outstanding_ticket_cannot_survive_last_membership_loss(self):
        self.login(102)
        connected_ticket, outstanding = self.ticket(), self.ticket()
        self.login(101)
        self.db.add(Notification(user_id=102, type="MEMBERSHIP_APPROVED", title="Historical", message="Historical"))
        self.db.commit()
        notification_id = self.db.scalar(select(Notification.id).where(Notification.user_id == 102))
        with self.connect(connected_ticket) as socket:
            self.assertEqual(self.approve(203, status="INACTIVE").status_code, 200)
            self.assertEqual(self.approve(204, ministry_id=12, status="INACTIVE").status_code, 200)
            self.assertEqual(self.client.portal.call(self.delivery.publish_created, notification_id), 0)
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.connect(outstanding):
                self.fail("Ineligible account connected")

    def test_password_change_restriction_still_allows_eligible_user_to_finish_change(self):
        self.change(User, 102, password_hash=PasswordHasher().hash("Temporary-password-123!"), must_change_password=True)
        response = self.account_login("user102@example.com", "Temporary-password-123!")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["must_change_password"])
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        self.assertEqual(self.client.get("/notifications", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/auth/change-password", headers=headers, json={
            "current_password": "Temporary-password-123!", "new_password": "Changed-password-123!",
        }).status_code, 204)
        self.assertEqual(self.account_login("user102@example.com", "Changed-password-123!").status_code, 200)

    def test_all_new_notification_texts_are_spanish_and_history_is_unchanged(self):
        body = self.requested()
        self.assertEqual(self.approve(body["memberships"][0]["id"]).status_code, 200)
        payload = {"ministry_id": 11, "name": "Práctica del domingo", "ministry_member_ids": [203],
                   "start_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                   "end_at": (datetime.now(timezone.utc) + timedelta(days=1, hours=2)).isoformat()}
        created = self.client.post("/rehearsals", headers=self.headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        rehearsal_id = created.json()["id"]
        self.assertEqual(self.client.patch(f"/rehearsals/{rehearsal_id}", headers=self.headers,
                                          json={"name": "Ensayo del domingo"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/rehearsals/{rehearsal_id}", headers=self.headers).status_code, 204)
        expected = {
            "REHEARSAL_ASSIGNED": ("Nuevo ensayo asignado", 'Se te ha asignado el ensayo "Práctica del domingo".'),
            "REHEARSAL_UPDATED": ("Ensayo actualizado", 'Los detalles de tu ensayo "Ensayo del domingo" han cambiado.'),
            "REHEARSAL_CANCELLED": ("Ensayo cancelado", 'Tu ensayo "Ensayo del domingo" ha sido cancelado.'),
            "MEMBERSHIP_PENDING": ("Nueva solicitud de ingreso", "Un nuevo usuario ha solicitado ingresar a tu ministerio."),
            "MEMBERSHIP_APPROVED": ("Solicitud de ingreso aprobada", "Tu solicitud de ingreso al ministerio ha sido aprobada."),
        }
        self.assertEqual({row.type for row in self.notices()}, set(expected))
        for row in self.notices():
            self.assertEqual((row.title, row.message), expected[row.type])
        self.db.add(Notification(user_id=102, type="MEMBERSHIP_APPROVED", title="Membership approved",
                                 message="Your membership request has been approved."))
        self.db.commit()
        self.login(102)
        response = self.client.get("/notifications", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["title"], "Membership approved")
        self.assertEqual(set(item), {"id", "type", "title", "message", "resource_type", "resource_id", "is_read", "created_at"})


# This integration test creates its own disposable schema and never falls back to
# the application's DATABASE_URL. Production migration application remains manual.
import os
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine
from app.core.database import Base
from app.models import Ministry, MinistryMemberRole, Role
from app.models.enums import RoleScope, RoleStatus
from app.repositories.ministry_members import MinistryMembersRepository
from app.repositories.user import UserRepository
from app.services.ministry_members import MinistryMembersService


@unittest.skipUnless(os.environ.get("ACCOUNT_ACTIVATION_TEST_DATABASE_URL"), "opt-in isolated PostgreSQL activation test")
class PostgreSQLAccountActivationTests(unittest.TestCase):
    def test_enum_extension_approval_and_non_destructive_reconciliation_preview(self):
        database_url = os.environ["ACCOUNT_ACTIVATION_TEST_DATABASE_URL"]
        management = create_engine(database_url, isolation_level="AUTOCOMMIT")
        schema = "activation_test_" + uuid4().hex
        business = None
        try:
            with management.connect() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                try:
                    connection.exec_driver_sql(f'SET search_path TO "{schema}"')
                    connection.exec_driver_sql("CREATE TYPE user_status AS ENUM ('ACTIVE', 'INACTIVE')")
                    seen = {"user_status"}
                    for table in Base.metadata.sorted_tables:
                        for column in table.columns:
                            enum = column.type
                            if hasattr(enum, "enums") and enum.name not in seen:
                                enum.create(connection, checkfirst=True)
                                seen.add(enum.name)
                    Base.metadata.create_all(connection)
                    root = Path(__file__).resolve().parents[1]
                    connection.exec_driver_sql((root / "migrations/002_add_pending_user_status.sql").read_text())
                    business = create_engine(database_url, connect_args={"options": f"-c search_path={schema}"})
                    with Session(business) as db:
                        db.add_all([User(id=i, email=f"pg{i}@example.com", password_hash="hash", first_name="PG", last_name="Test",
                                         status=state, deleted_at=datetime.now(timezone.utc) if i == 5 else None)
                                    for i, state in ((1, UserStatus.ACTIVE), (2, UserStatus.INACTIVE),
                                                     (3, UserStatus.PENDING), (4, UserStatus.ACTIVE), (5, UserStatus.ACTIVE))])
                        db.add(Ministry(id=1, name="PG ministry"))
                        db.add(Role(id=17, name="ADMIN", scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True))
                        db.flush()
                        db.add_all([MinistryMember(id=103, user_id=3, ministry_id=1, status=MembershipStatus.PENDING),
                                    MinistryMember(id=104, user_id=4, ministry_id=1, status=MembershipStatus.ACTIVE)])
                        db.flush()
                        db.add(MinistryMemberRole(id=1, ministry_member_id=104, role_id=17))
                        db.commit()
                        self.assertIsNone(UserRepository(db).get_by_identifier("pg1@example.com"))
                        self.assertIsNone(UserRepository(db).get_by_identifier("pg3@example.com"))
                        service = MinistryMembersService(MinistryMembersRepository(db))
                        service.update_status(1, 103, MembershipStatus.ACTIVE, 4)
                        db.expire_all()
                        self.assertEqual(db.get(User, 3).status, UserStatus.ACTIVE)
                        self.assertIsNotNone(UserRepository(db).get_by_identifier("pg3@example.com"))
                        service.update_status(1, 103, MembershipStatus.INACTIVE, 4)
                        db.expire_all()
                        self.assertEqual(db.get(User, 3).status, UserStatus.PENDING)
                        self.assertIsNone(UserRepository(db).get_by_identifier("pg3@example.com"))
                    connection.exec_driver_sql((root / "migrations/003_review_membership_account_status.sql").read_text()).close()
                    with Session(business) as db:
                        self.assertEqual(db.get(User, 1).status, UserStatus.ACTIVE)  # preview rolled back
                        self.assertEqual(db.get(User, 2).status, UserStatus.INACTIVE)
                        self.assertEqual(db.get(User, 4).status, UserStatus.ACTIVE)  # effective admin untouched
                        self.assertIsNotNone(db.get(User, 5).deleted_at)
                finally:
                    if business is not None:
                        business.dispose()
                    connection.exec_driver_sql('SET search_path TO public')
                    connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            management.dispose()
