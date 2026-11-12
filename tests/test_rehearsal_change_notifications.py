"""Meaningful rehearsal changes and physical deletion notify only affected users."""
import unittest
from datetime import timedelta, timezone
from unittest.mock import patch

import test_rehearsal_notifications as fixtures
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Notification, Rehearsal, RehearsalAttendance
from app.repositories.notification import NotificationRepository
from app.repositories.rehearsal import RehearsalRepository


class RehearsalChangeNotificationTests(unittest.TestCase):
    setUp = fixtures.RehearsalAssignmentNotificationTests.setUp
    login = fixtures.RehearsalAssignmentNotificationTests.login
    ticket = fixtures.RehearsalAssignmentNotificationTests.ticket
    connect = fixtures.RehearsalAssignmentNotificationTests.connect
    update = fixtures.RehearsalAssignmentNotificationTests.update
    create = fixtures.RehearsalAssignmentNotificationTests.create
    notifications = fixtures.RehearsalAssignmentNotificationTests.notifications
    assigned_member_ids = fixtures.RehearsalAssignmentNotificationTests.assigned_member_ids

    def delete(self, rehearsal_id=303):
        return self.client.delete(f"/rehearsals/{rehearsal_id}", headers=self.headers)

    def notices(self, kind):
        return [row for row in self.notifications() if row.type == kind]

    def test_name_change_notifies_assigned_users_with_exact_format(self):
        response = self.update(name="Sunday practice")
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.notices("REHEARSAL_UPDATED")
        self.assertEqual({row.user_id for row in rows}, {102, 104})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row.title, "Rehearsal updated")
            self.assertEqual(row.message, 'The details of your rehearsal "Sunday practice" have changed.')
            self.assertEqual(row.resource_type, "rehearsal")
            self.assertEqual(row.resource_id, 301)
            self.assertFalse(row.is_read)
            self.assertIsNotNone(row.created_at)

    def test_start_and_end_changes_each_trigger_one_notice_per_participant(self):
        for data in ({"start_at": "2026-10-25T19:00:00Z"},
                     {"end_at": "2026-10-25T22:00:00Z"}):
            before = len(self.notifications())
            response = self.update(**data)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(self.notifications()) - before, 2)
        self.assertEqual(len(self.notices("REHEARSAL_UPDATED")), 4)

    def test_multiple_field_changes_generate_only_one_notice_each_and_noop_retry_none(self):
        data = {"name": "Changed", "start_at": "2026-10-25T19:00:00Z", "end_at": "2026-10-25T22:00:00Z"}
        for _ in range(2):
            response = self.update(**data)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(self.notices("REHEARSAL_UPDATED")), 2)

    def test_noops_reordering_and_nonmeaningful_fields_produce_no_update_notices(self):
        old = self.db.get(Rehearsal, 301)
        data = {"name": old.name, "start_at": old.start_at.replace(tzinfo=timezone.utc).isoformat(),
                "end_at": old.end_at.replace(tzinfo=timezone.utc).isoformat()}
        self.assertEqual(self.update(**data).status_code, 200)
        self.assertEqual(self.update(ministry_member_ids=[206, 203]).status_code, 200)
        self.assertEqual(self.update(description="Description", tolerance_minutes=30).status_code, 200)
        self.assertEqual(self.notifications(), [])

    def test_same_instant_with_different_timezone_offset_is_not_a_change(self):
        old = self.db.get(Rehearsal, 301)
        start = old.start_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        end = old.end_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(self.update(start_at=start.isoformat(), end_at=end.isoformat()).status_code, 200)
        self.assertEqual(self.notifications(), [])

    def test_participant_only_additions_and_removals_do_not_send_updates(self):
        self.assertEqual(self.update(ministry_member_ids=[203, 205]).status_code, 200)
        self.assertEqual(self.notices("REHEARSAL_UPDATED"), [])
        self.assertEqual([row.user_id for row in self.notices("REHEARSAL_ASSIGNED")], [103])
        self.assertEqual(self.update(ministry_member_ids=[203]).status_code, 200)
        self.assertEqual(len(self.notifications()), 1)

    def test_combined_change_excludes_new_and_removed_participants_from_updates(self):
        response = self.update(name="Changed", ministry_member_ids=[203, 205])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row.user_id for row in self.notices("REHEARSAL_UPDATED")], [102])
        self.assertEqual([row.user_id for row in self.notices("REHEARSAL_ASSIGNED")], [103])
        self.assertEqual(len(self.notifications()), 2)
        self.assertNotIn(104, [row.user_id for row in self.notifications()])

    def test_successful_deletion_notifies_predelete_participants_and_preserves_history(self):
        created = self.create(ministry_member_ids=[203, 206, 205])
        self.assertEqual(created.status_code, 201, created.text)
        rehearsal_id = created.json()["id"]
        self.assertEqual(self.delete(rehearsal_id).status_code, 204)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Rehearsal, rehearsal_id))
        self.assertEqual(self.assigned_member_ids(rehearsal_id), set())
        rows = self.notices("REHEARSAL_CANCELLED")
        self.assertEqual(len(rows), 3)
        self.assertEqual({row.user_id for row in rows}, {102, 103, 104})
        for row in rows:
            self.assertEqual(row.title, "Rehearsal canceled")
            self.assertEqual(row.message, 'Your rehearsal "Sunday worship practice" has been canceled.')
            self.assertEqual(row.resource_id, rehearsal_id)
            self.assertEqual(row.resource_type, "rehearsal")
            self.assertFalse(row.is_read)
        self.assertEqual(len(self.notices("REHEARSAL_ASSIGNED")), 3)
        self.assertEqual(self.delete(rehearsal_id).status_code, 404)
        self.assertEqual(len(self.notices("REHEARSAL_CANCELLED")), 3)
        self.login(102)
        history = self.client.get("/notifications", headers=self.headers).json()
        self.assertEqual(history["total"], 2)
        self.assertEqual({item["type"] for item in history["items"]}, {"REHEARSAL_ASSIGNED", "REHEARSAL_CANCELLED"})

    def test_delete_with_no_participants_sends_no_notices(self):
        self.assertEqual(self.delete(305).status_code, 204)
        self.assertEqual(self.notifications(), [])

    def test_failed_and_unauthorized_deletions_never_notify(self):
        with patch.object(self.delivery, "publish_created") as publish:
            self.assertEqual(self.delete(301).status_code, 409)  # recorded attendance
            self.assertEqual(self.delete(99999).status_code, 404)
            self.assertEqual(self.delete(304).status_code, 404)  # invisible ministry
            self.login(102)
            self.assertEqual(self.delete().status_code, 403)
            self.assertEqual(self.update(name="No access").status_code, 403)
            publish.assert_not_called()
        self.assertEqual(self.notifications(), [])

    def test_notification_insert_and_commit_failures_roll_back_updates_and_deletion(self):
        original_name = self.db.get(Rehearsal, 301).name
        original_add = NotificationRepository.add_many
        def fail_after_insert(repository, notifications):
            original_add(repository, notifications)
            raise SQLAlchemyError("private notification failure")
        operations = (lambda: self.update(name="Should roll back"), lambda: self.delete())
        for method in ("insert", "commit"):
            for operation in operations:
                failure = (patch.object(NotificationRepository, "add_many", autospec=True, side_effect=fail_after_insert)
                           if method == "insert" else patch.object(self.db, "commit", side_effect=SQLAlchemyError("commit failed")))
                with self.subTest(method=method, operation=operation), failure, \
                        patch.object(self.delivery, "publish_created") as publish:
                    self.assertEqual(operation().status_code, 500)
                    publish.assert_not_called()
                self.db.expire_all()
                self.assertEqual(self.db.get(Rehearsal, 301).name, original_name)
                self.assertIsNotNone(self.db.get(Rehearsal, 303))
                self.assertEqual(self.assigned_member_ids(303), {206})
                self.assertEqual(self.notifications(), [])

    def test_delete_failure_after_flush_rolls_back_both_cancellation_and_delete(self):
        original_delete = RehearsalRepository.delete
        def fail_after_delete(repository, rehearsal):
            original_delete(repository, rehearsal)
            raise SQLAlchemyError("delete failed")
        with patch.object(RehearsalRepository, "delete", autospec=True, side_effect=fail_after_delete), \
                patch.object(self.delivery, "publish_created") as publish:
            self.assertEqual(self.delete().status_code, 500)
            publish.assert_not_called()
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(Rehearsal, 303))
        self.assertEqual(self.notifications(), [])

    def test_websocket_events_see_committed_update_and_deleted_resource(self):
        self.login(102)
        participant_ticket = self.ticket()
        self.login(101)
        admin_ticket = self.ticket()  # admin is not a participant of rehearsal 301/303
        original_publish = self.delivery.publish_created
        committed = []
        async def verify_commit(notification_id):
            with Session(self.engine) as db:
                notification = db.get(Notification, notification_id)
                if notification is not None:
                    rehearsal = db.get(Rehearsal, notification.resource_id)
                    committed.append((notification.type, rehearsal.name if rehearsal else None))
            return await original_publish(notification_id)
        with self.connect(participant_ticket) as participant, self.connect(admin_ticket) as admin, \
                patch.object(self.delivery, "publish_created", side_effect=verify_commit):
            response = self.update(name="Committed name", ministry_member_ids=[203])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(committed, [("REHEARSAL_UPDATED", "Committed name")])
            event = participant.receive_json()
            self.assertEqual(event["type"], "notification.created")
            self.assertEqual(event["data"]["notification_type"], "REHEARSAL_UPDATED")
            # Create a rehearsal for the admin; its first event must be its own assignment,
            # proving no previous private update was queued for this socket.
            created = self.create(ministry_member_ids=[201])
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(admin.receive_json()["data"]["notification_type"], "REHEARSAL_ASSIGNED")
            self.assertEqual(self.delete(created.json()["id"]).status_code, 204)
            self.assertEqual(committed[-1], ("REHEARSAL_CANCELLED", None))
            event = admin.receive_json()
            self.assertEqual(event["data"]["notification_type"], "REHEARSAL_CANCELLED")
            self.assertEqual(event["data"]["resource_id"], created.json()["id"])

    def test_delivery_failure_cannot_undo_updates_deletions_or_skip_recipients(self):
        with patch.object(self.delivery, "publish_created", side_effect=RuntimeError("socket unavailable")) as publish:
            self.assertEqual(self.update(name="Committed offline").status_code, 200)
            self.assertEqual(publish.call_count, 2)
            self.assertEqual(self.delete().status_code, 204)
            self.assertEqual(publish.call_count, 3)
        self.db.expire_all()
        self.assertEqual(self.db.get(Rehearsal, 301).name, "Committed offline")
        self.assertIsNone(self.db.get(Rehearsal, 303))
        self.assertEqual(len(self.notifications()), 3)

    def test_offline_recipients_and_notification_user_isolation(self):
        self.assertEqual(self.update(name="Offline change").status_code, 200)
        for user_id, expected in ((101, 0), (102, 1), (104, 1), (103, 0)):
            self.login(user_id)
            response = self.client.get("/notifications", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], expected)
