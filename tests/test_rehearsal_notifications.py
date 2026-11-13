"""Explicit rehearsal assignments persist atomically and deliver only after commit."""
import unittest
from unittest.mock import patch

import test_notification_realtime as fixtures
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies.notification_realtime import get_notification_delivery_service
from app.main import app
from app.models import Notification, Rehearsal, RehearsalAttendance
from app.repositories.notification import NotificationRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.notification import NotificationService


class RehearsalAssignmentNotificationTests(unittest.TestCase):
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
        self.payload = {
            "ministry_id": 11, "name": "Sunday worship practice",
            "start_at": "2026-12-01T18:00:00Z", "end_at": "2026-12-01T20:00:00Z",
            "ministry_member_ids": [203],
        }

    def create(self, **changes):
        return self.client.post("/rehearsals", headers=self.headers, json={**self.payload, **changes})

    def update(self, **changes):
        return self.client.patch("/rehearsals/301", headers=self.headers, json=changes)

    def notifications(self):
        self.db.expire_all()
        return list(self.db.scalars(select(Notification).order_by(Notification.id)))

    def assigned_member_ids(self, rehearsal_id):
        return set(self.db.scalars(select(RehearsalAttendance.ministry_member_id).where(
            RehearsalAttendance.rehearsal_id == rehearsal_id,
        )))

    def test_single_assignment_persists_offline_and_remains_personal(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        rehearsal_id = response.json()["id"]
        rows = self.notifications()
        self.assertEqual(len(rows), 1)
        notification = rows[0]
        self.assertEqual(notification.user_id, 102)
        self.assertEqual(notification.type, "REHEARSAL_ASSIGNED")
        self.assertEqual(notification.title, "Nuevo ensayo asignado")
        self.assertIn("Sunday worship practice", notification.message)
        self.assertEqual(notification.resource_type, "rehearsal")
        self.assertEqual(notification.resource_id, rehearsal_id)
        self.assertFalse(notification.is_read)
        self.assertIsNotNone(notification.created_at)
        self.assertEqual(self.assigned_member_ids(rehearsal_id), {203})
        self.assertEqual(self.client.get("/notifications", headers=self.headers).json()["total"], 0)
        self.login(102)
        listing = self.client.get("/notifications", headers=self.headers).json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["resource_id"], rehearsal_id)
        self.assertEqual(self.client.get("/notifications/unread-count", headers=self.headers).json(),
                         {"unread_count": 1})

    def test_bulk_creation_notifies_each_validated_user_once(self):
        response = self.create(ministry_member_ids=[203, 206, 205])
        self.assertEqual(response.status_code, 201, response.text)
        rows = self.notifications()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row.user_id for row in rows}, {102, 103, 104})
        self.assertEqual({row.resource_id for row in rows}, {response.json()["id"]})

    def test_existing_participant_addition_notifies_only_new_members_and_uses_new_name(self):
        response = self.update(name="Renamed practice", ministry_member_ids=[203, 206, 201, 205])
        self.assertEqual(response.status_code, 200, response.text)
        notifications = self.notifications()
        rows = [row for row in notifications if row.type == "REHEARSAL_ASSIGNED"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.user_id for row in rows}, {101, 103})
        updates = [row for row in notifications if row.type == "REHEARSAL_UPDATED"]
        self.assertEqual({row.user_id for row in updates}, {102, 104})
        self.assertTrue(all("Renamed practice" in row.message for row in rows))
        self.assertEqual({row.resource_id for row in rows}, {301})
        self.assertEqual(self.assigned_member_ids(301), {203, 206, 201, 205})

    def test_unchanged_repeated_and_metadata_only_updates_do_not_notify(self):
        self.assertEqual(self.update(ministry_member_ids=[203, 206]).status_code, 200)
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.update(ministry_member_ids=[203, 206, 205]).status_code, 200)
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(self.update(ministry_member_ids=[205, 206, 203]).status_code, 200)
        self.assertEqual(self.update(description="Description only", tolerance_minutes=20).status_code, 200)
        self.assertEqual(len(self.notifications()), 1)
        # Removing a pending assignment does not send a cancellation notification.
        self.assertEqual(self.update(ministry_member_ids=[203, 206]).status_code, 200)
        self.assertEqual(len(self.notifications()), 1)
        # Re-adding a removed assignment is a genuinely new assignment.
        self.assertEqual(self.update(ministry_member_ids=[203, 206, 205]).status_code, 200)
        self.assertEqual(len(self.notifications()), 2)

    def test_duplicate_and_cross_ministry_participants_fail_without_notifications(self):
        for members in ([203, 203], [203, 204], [99999]):
            with self.subTest(members=members):
                self.assertEqual(self.create(ministry_member_ids=members).status_code, 422)
                self.assertEqual(self.update(ministry_member_ids=members).status_code, 422)
        self.assertEqual(self.notifications(), [])

    def test_notification_helper_deduplicates_recipients_without_committing(self):
        repository = NotificationRepository(self.db)
        service = NotificationService(repository)
        rehearsal = self.db.get(Rehearsal, 301)
        with patch.object(self.db, "commit", wraps=self.db.commit) as commit:
            ids = service.create_rehearsal_assignments(rehearsal, [203, 203, 206, 204])
            commit.assert_not_called()
        self.assertEqual(len(ids), 2)  # member 204 is not assigned in ministry 11
        self.db.rollback()
        self.assertEqual(self.notifications(), [])

    def test_notification_insert_failure_rolls_back_creation_and_participant_update(self):
        add_many = NotificationRepository.add_many
        def fail_after_insert(repository, notifications):
            add_many(repository, notifications)
            raise SQLAlchemyError("private notification insert failure")
        rehearsal_ids = set(self.db.scalars(select(Rehearsal.id)))
        with patch.object(NotificationRepository, "add_many", autospec=True, side_effect=fail_after_insert), \
                patch.object(self.delivery, "publish_created") as publish:
            self.assertEqual(self.create().status_code, 500)
            self.assertEqual(self.update(ministry_member_ids=[203, 206, 205]).status_code, 500)
            publish.assert_not_called()
        self.assertEqual(set(self.db.scalars(select(Rehearsal.id))), rehearsal_ids)
        self.assertEqual(self.assigned_member_ids(301), {203, 206})
        self.assertEqual(self.notifications(), [])

    def test_commit_failure_rolls_back_notifications_and_does_not_publish(self):
        for operation in (lambda: self.create(), lambda: self.update(ministry_member_ids=[203, 206, 205])):
            with self.subTest(operation=operation), \
                    patch.object(self.db, "commit", side_effect=SQLAlchemyError("commit failed")), \
                    patch.object(self.delivery, "publish_created") as publish:
                self.assertEqual(operation().status_code, 500)
                publish.assert_not_called()
            self.assertEqual(self.notifications(), [])
            self.assertEqual(self.assigned_member_ids(301), {203, 206})

    def test_delivery_runs_after_commit_on_socket_loop_and_preserves_isolation(self):
        self.login(102)
        user_ticket = self.ticket()
        self.login(104)
        other_ticket = self.ticket()
        self.login(101)
        original_publish = self.delivery.publish_created
        committed = []
        async def verify_commit(notification_id):
            with Session(self.engine) as db:
                row = db.get(Notification, notification_id)
                self.assertIsNotNone(row)  # independent connection must see committed data
                assignment = db.scalar(select(RehearsalAttendance.id).where(
                    RehearsalAttendance.rehearsal_id == row.resource_id,
                ))
                self.assertIsNotNone(assignment)
                committed.append(notification_id)
            return await original_publish(notification_id)
        with self.connect(user_ticket) as user_socket, self.connect(other_ticket) as other_socket, \
                patch.object(self.delivery, "publish_created", side_effect=verify_commit):
            first = self.create()
            self.assertEqual(first.status_code, 201, first.text)
            self.assertEqual(len(committed), 1)
            event = user_socket.receive_json()
            self.assertEqual(event["type"], "notification.created")
            self.assertEqual(event["data"]["notification_type"], "REHEARSAL_ASSIGNED")
            self.assertEqual(event["data"]["title"], "Nuevo ensayo asignado")
            self.assertEqual(event["data"]["resource_id"], first.json()["id"])
            self.assertFalse(event["data"]["is_read"])
            second = self.create(ministry_member_ids=[206])
            self.assertEqual(second.status_code, 201, second.text)
            self.assertEqual(other_socket.receive_json()["data"]["resource_id"], second.json()["id"])
        self.assertEqual(len(committed), 2)

    def test_participant_addition_delivers_committed_notification(self):
        self.login(103)
        ticket = self.ticket()
        self.login(101)
        with self.connect(ticket) as socket:
            response = self.update(ministry_member_ids=[203, 206, 205])
            self.assertEqual(response.status_code, 200, response.text)
            rows = self.notifications()
            self.assertEqual(len(rows), 1)
            event = socket.receive_json()
            self.assertEqual(event["data"]["id"], rows[0].id)
            self.assertEqual(event["data"]["resource_id"], 301)
            self.assertEqual(rows[0].user_id, 103)

    def test_response_read_failure_also_rolls_back_assignments_and_notifications(self):
        with patch.object(RehearsalReadRepository, "get_participants", side_effect=SQLAlchemyError("read failed")), \
                patch.object(self.delivery, "publish_created") as publish:
            response = self.update(ministry_member_ids=[203, 206, 205])
            self.assertEqual(response.status_code, 500)
            publish.assert_not_called()
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.assigned_member_ids(301), {203, 206})

    def test_delivery_failure_cannot_undo_bulk_assignments_or_skip_other_users(self):
        with patch.object(self.delivery, "publish_created", side_effect=RuntimeError("broken socket")) as publish:
            response = self.create(ministry_member_ids=[203, 206])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(self.assigned_member_ids(response.json()["id"]), {203, 206})

    def test_regular_user_cannot_assign_or_create_arbitrary_notifications(self):
        self.login(102)
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.update(ministry_member_ids=[203, 206, 205]).status_code, 403)
        self.login(101)
        self.assertEqual(self.create(user_id=104, notification_type="ARBITRARY").status_code, 422)
        self.assertEqual(self.notifications(), [])
