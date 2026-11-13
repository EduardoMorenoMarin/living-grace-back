"""Membership notices follow validated registration and locked approval transitions."""
import inspect
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import test_notification_realtime as fixtures
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies.notification import get_membership_notification_service
from app.dependencies.notification_realtime import get_notification_delivery_service
from app.main import app
from app.models import MinistryMember, MinistryMemberRole, Notification, Role, User
from app.models.enums import MembershipStatus, RoleStatus, UserStatus
from app.repositories.notification import NotificationRepository


class MembershipNotificationTests(unittest.TestCase):
    login = fixtures.NotificationSocketTests.login
    change = fixtures.NotificationSocketTests.change
    ticket = fixtures.NotificationSocketTests.ticket
    connect = fixtures.NotificationSocketTests.connect

    def setUp(self):
        fixtures.NotificationSocketTests.setUp(self)
        self.stack.enter_context(patch.dict(app.dependency_overrides, {
            get_notification_delivery_service: lambda: self.delivery,
        }))
        self.login(101)

    def payload(self, ministries=(11,)):
        return {"email": f"request-{uuid4().hex}@example.com", "password": "New-membership-password-123!",
                "first_name": "New", "last_name": "Applicant",
                "memberships": [{"ministry_id": identifier} for identifier in ministries]}

    def register(self, ministries=(11,), *, data=None):
        return self.client.post("/users/register", json=data or self.payload(ministries))

    def requested(self, ministries=(11,)):
        response = self.register(ministries)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def approve(self, membership_id, ministry_id=11, status="ACTIVE"):
        return self.client.patch(f"/ministries/{ministry_id}/members/{membership_id}/status",
                                 headers=self.headers, json={"status": status})

    def notices(self, kind=None):
        self.db.expire_all()
        statement = select(Notification).order_by(Notification.id)
        if kind is not None:
            statement = statement.where(Notification.type == kind)
        return list(self.db.scalars(statement))

    def test_pending_registration_notifies_only_associated_effective_global_admins(self):
        self.db.add(MinistryMemberRole(id=550, ministry_member_id=206, role_id=19))  # MINISTRY ADMIN only
        self.db.commit()
        body = self.requested()
        self.assertEqual(body["memberships"][0]["status"], "PENDING")
        notices = self.notices("MEMBERSHIP_PENDING")
        self.assertEqual({row.user_id for row in notices}, {101, 103})
        self.assertEqual(len(notices), 2)
        for row in notices:
            self.assertEqual(row.title, "Nueva solicitud de ingreso")
            self.assertEqual(row.message, "Un nuevo usuario ha solicitado ingresar a tu ministerio.")
            self.assertEqual(row.resource_type, "ministry")
            self.assertEqual(row.resource_id, 11)
            self.assertFalse(row.is_read)
            self.assertIsNotNone(row.created_at)

    def test_multiple_ministries_deduplicate_roles_and_isolate_recipients(self):
        # Admin 101 is authorized globally through either ministry, and is a
        # member of both. Admin 103 is associated only with ministry 11.
        self.db.add_all([MinistryMemberRole(id=550, ministry_member_id=202, role_id=17),
                         MinistryMemberRole(id=551, ministry_member_id=201, role_id=19)])
        self.db.commit()
        self.requested((11, 12))
        self.assertEqual([(row.resource_id, row.user_id) for row in self.notices()],
                         [(11, 101), (11, 103), (12, 101)])

    def test_global_permission_from_other_ministry_still_requires_target_association(self):
        self.requested((12,))
        self.assertEqual([(row.resource_id, row.user_id) for row in self.notices()], [(12, 101)])
        # Admin 103 has global permission but no ministry 12 membership.
        self.assertNotIn(103, [row.user_id for row in self.notices()])

    def test_inactive_deleted_revoked_or_unavailable_admins_are_excluded(self):
        cases = (
            (User, 103, "status", UserStatus.INACTIVE),
            (User, 103, "deleted_at", datetime.now(timezone.utc)),
            (MinistryMember, 205, "status", MembershipStatus.INACTIVE),
            (MinistryMember, 205, "status", MembershipStatus.PENDING),
            (MinistryMember, 205, "deleted_at", datetime.now(timezone.utc)),
            (MinistryMemberRole, 504, "revoked_at", datetime.now(timezone.utc)),
        )
        for model, identifier, field, value in cases:
            with self.subTest(model=model, field=field, value=value):
                old = getattr(self.db.get(model, identifier), field)
                before = {row.id for row in self.notices()}
                self.change(model, identifier, **{field: value})
                self.requested()
                self.assertEqual([row.user_id for row in self.notices() if row.id not in before], [101])
                self.change(model, identifier, **{field: old})
        for field, value in (("status", RoleStatus.INACTIVE), ("deleted_at", datetime.now(timezone.utc))):
            old = getattr(self.db.get(Role, 17), field)
            before = len(self.notices())
            self.change(Role, 17, **{field: value})
            self.requested()
            self.assertEqual(len(self.notices()), before)
            self.change(Role, 17, **{field: old})

    def test_target_association_must_be_active_even_with_global_permission_elsewhere(self):
        for field, value in (("status", MembershipStatus.INACTIVE), ("status", MembershipStatus.PENDING),
                             ("deleted_at", datetime.now(timezone.utc))):
            old = getattr(self.db.get(MinistryMember, 202), field)
            self.change(MinistryMember, 202, **{field: value})
            self.requested((12,))
            self.assertEqual(self.notices(), [])
            self.change(MinistryMember, 202, **{field: old})

    def test_registration_without_admins_or_memberships_succeeds(self):
        self.requested((13,))  # no associated global admin
        self.requested(())
        self.assertEqual(self.notices(), [])

    def test_repeat_and_duplicate_or_invalid_registration_does_not_duplicate_notices(self):
        data = self.payload()
        self.assertEqual(self.register(data=data).status_code, 201)
        self.assertEqual(self.register(data=data).status_code, 409)
        self.assertEqual(len(self.notices()), 2)
        invalid = self.payload((11, 11))
        self.assertEqual(self.register(data=invalid).status_code, 422)
        self.assertEqual(self.register(data={**self.payload(), "notification_user_id": 104}).status_code, 422)
        self.assertEqual(len(self.notices()), 2)

    def test_approval_notifies_only_affected_user_and_active_retry_is_silent(self):
        body = self.requested()
        membership_id = body["memberships"][0]["id"]
        for _ in range(2):
            response = self.approve(membership_id)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "ACTIVE")
            rows = self.notices("MEMBERSHIP_APPROVED")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].user_id, body["id"])
        row = rows[0]
        self.assertEqual(row.title, "Solicitud de ingreso aprobada")
        self.assertEqual(row.message, "Tu solicitud de ingreso al ministerio ha sido aprobada.")
        self.assertEqual(row.resource_type, "ministry")
        self.assertEqual(row.resource_id, 11)
        self.assertFalse(row.is_read)
        self.login(body["id"])
        history = self.client.get("/notifications", headers=self.headers).json()
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["items"][0]["type"], "MEMBERSHIP_APPROVED")
        self.assertEqual(self.client.get("/notifications/unread-count", headers=self.headers).json(),
                         {"unread_count": 1})

    def test_all_other_status_transitions_do_not_generate_approval(self):
        for source in MembershipStatus:
            for target in MembershipStatus:
                with self.subTest(source=source, target=target):
                    body = self.requested()
                    membership_id = body["memberships"][0]["id"]
                    self.change(MinistryMember, membership_id, status=source)
                    before = len(self.notices("MEMBERSHIP_APPROVED"))
                    response = self.approve(membership_id, status=target.value)
                    self.assertEqual(response.status_code, 200, response.text)
                    expected = int(source == MembershipStatus.PENDING and target == MembershipStatus.ACTIVE)
                    self.assertEqual(len(self.notices("MEMBERSHIP_APPROVED")), before + expected)

    def test_unauthorized_cross_ministry_and_self_approval_keep_existing_rules(self):
        body = self.requested()
        membership_id = body["memberships"][0]["id"]
        self.db.add(MinistryMemberRole(id=550, ministry_member_id=206, role_id=19))
        self.db.commit()
        for user_id in (102, 104):
            self.login(user_id)
            self.assertEqual(self.approve(membership_id).status_code, 403)
        self.login(101)
        self.assertEqual(self.approve(membership_id, ministry_id=12).status_code, 404)
        self.change(MinistryMember, 202, status=MembershipStatus.PENDING)
        self.assertEqual(self.approve(202, ministry_id=12).status_code, 409)
        self.assertEqual(self.notices("MEMBERSHIP_APPROVED"), [])

    def test_registration_notification_insert_and_commit_failure_roll_back_everything(self):
        original_add = NotificationRepository.add_many
        def fail_after_insert(repository, notifications):
            original_add(repository, notifications)
            raise SQLAlchemyError("notification fixture failure")
        for method in ("insert", "commit"):
            data = self.payload()
            failure = (patch.object(NotificationRepository, "add_many", autospec=True, side_effect=fail_after_insert)
                       if method == "insert" else patch.object(self.db, "commit", side_effect=SQLAlchemyError("commit fixture failure")))
            with self.subTest(method=method), failure, patch.object(self.delivery, "publish_created") as publish:
                response = self.register(data=data)
                self.assertEqual(response.status_code, 500)
                publish.assert_not_called()
            self.assertIsNone(self.db.scalar(select(User).where(User.email == data["email"])))
            self.assertEqual(self.notices(), [])

    def test_approval_notification_insert_and_commit_failure_roll_back_status(self):
        body = self.requested()
        membership_id = body["memberships"][0]["id"]
        for method in ("insert", "commit"):
            failure = (patch.object(NotificationRepository, "add_many", side_effect=SQLAlchemyError("insert failed"))
                       if method == "insert" else patch.object(self.db, "commit", side_effect=SQLAlchemyError("commit failed")))
            with self.subTest(method=method), failure, patch.object(self.delivery, "publish_created") as publish:
                self.assertEqual(self.approve(membership_id).status_code, 500)
                publish.assert_not_called()
            self.db.expire_all()
            self.assertEqual(self.db.get(MinistryMember, membership_id).status, MembershipStatus.PENDING)
            self.assertEqual(self.notices("MEMBERSHIP_APPROVED"), [])

    def test_websocket_pending_and_approval_events_are_after_commit_and_private(self):
        admin_ticket = self.ticket()
        original_publish = self.delivery.publish_created
        delivered = []
        async def verify_commit(notification_id):
            with Session(self.engine) as db:
                row = db.get(Notification, notification_id)
                if row is not None:
                    status = db.scalar(select(MinistryMember.status).where(
                        MinistryMember.user_id == row.user_id, MinistryMember.ministry_id == row.resource_id,
                    ))
                    delivered.append((row.type, row.user_id, status))
            return await original_publish(notification_id)
        with self.connect(admin_ticket) as admin, patch.object(self.delivery, "publish_created", side_effect=verify_commit):
            body = self.requested((11, 12))
            self.assertEqual(len(delivered), 3)
            event = admin.receive_json()
            self.assertEqual(event["data"]["notification_type"], "MEMBERSHIP_PENDING")
            self.assertEqual(event["data"]["resource_id"], 11)
            admin.receive_json()  # pending notice for ministry 12
            self.assertEqual(self.approve(body["memberships"][1]["id"], ministry_id=12).status_code, 200)
            self.login(body["id"])
            user_ticket = self.ticket()
            self.login(101)
            with self.connect(user_ticket) as applicant:
                response = self.approve(body["memberships"][0]["id"])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(delivered[-1], ("MEMBERSHIP_APPROVED", body["id"], MembershipStatus.ACTIVE))
                event = applicant.receive_json()
                self.assertEqual(event["data"]["notification_type"], "MEMBERSHIP_APPROVED")
                self.assertNotIn("user_id", event["data"])
                self.assertFalse(event["data"]["is_read"])

    def test_failed_delivery_does_not_fail_registration_approval_or_later_recipients(self):
        with patch.object(self.delivery, "publish_created", side_effect=RuntimeError("delivery fixture failure")) as publish:
            body = self.requested()
            self.assertEqual(publish.call_count, 2)
            self.assertEqual(self.approve(body["memberships"][0]["id"]).status_code, 200)
            self.assertEqual(publish.call_count, 3)
        self.assertEqual(len(self.notices()), 3)


class MembershipBackgroundDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_schedule_after_commit_and_do_not_wait_for_sends(self):
        tasks = BackgroundTasks()
        delivery = AsyncMock()
        delivery.publish_created.side_effect = [RuntimeError("first failed"), 1]
        service = get_membership_notification_service(None, tasks, delivery)
        service.publish_after_commit([41, 42])
        delivery.publish_created.assert_not_called()
        self.assertEqual(len(tasks.tasks), 2)
        self.assertTrue(all(inspect.iscoroutinefunction(task.func) for task in tasks.tasks))
        await tasks()
        self.assertEqual(delivery.publish_created.await_count, 2)
