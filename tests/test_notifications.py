"""Authenticated notification listing with real SQL and isolated fixtures."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import jwt
import test_rehearsal_read as fixtures
from pydantic import ValidationError
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Notification, User
from app.repositories.notification import NotificationRepository
from app.schemas.notification import NotificationItem, NotificationPage


class NotificationTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        self.now = datetime(2026, 11, 15, 17, tzinfo=timezone.utc)
        self.login(102)

    def add(self, identifier, *, user_id=102, is_read=False, created_at=None):
        item = Notification(id=identifier, user_id=user_id, type="REHEARSAL_ASSIGNED",
                            title=f"Notification {identifier}", message="You have a rehearsal.",
                            resource_type="rehearsal", resource_id=301, is_read=is_read,
                            created_at=created_at or self.now)
        self.db.add(item)
        self.db.commit()
        return item

    def get(self, **params):
        return self.client.get("/notifications", headers=self.headers, params=params)

    def body(self, **params):
        response = self.get(**params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def test_empty_and_default_pagination(self):
        self.assertEqual(self.body(), {"total": 0, "limit": 20, "offset": 0, "items": []})
        for identifier in range(1, 23):
            self.add(identifier)
        body = self.body()
        self.assertEqual(body["total"], 22)
        self.assertEqual(len(body["items"]), 20)
        self.assertEqual([n["id"] for n in body["items"]], list(range(22, 2, -1)))

    def test_response_contract_defaults_and_optional_resource(self):
        self.db.add(Notification(id=1, user_id=102, type="ANNOUNCEMENT", title="Hello", message="Welcome"))
        self.db.commit()
        item = self.body()["items"][0]
        self.assertEqual(set(item), {"id", "type", "title", "message", "resource_type", "resource_id", "is_read", "created_at"})
        self.assertIsNone(item["resource_type"])
        self.assertIsNone(item["resource_id"])
        self.assertFalse(item["is_read"])
        self.assertEqual(datetime.fromisoformat(item["created_at"]).utcoffset(), timedelta(0))
        for secret in ("user_id", "email", "password_hash", "private-hash"):
            self.assertNotIn(secret, item)

    def test_user_isolation_including_admin_and_client_supplied_user_id(self):
        self.add(1)
        self.add(2, user_id=101)
        self.add(3, user_id=104)
        for user, expected in ((102, 1), (101, 2), (104, 3)):
            with self.subTest(user=user):
                self.login(user)
                body = self.body(user_id=103, role="ADMIN")
                self.assertEqual(body["total"], 1)
                self.assertEqual([item["id"] for item in body["items"]], [expected])
        self.login(103)
        self.assertEqual(self.body(user_id=102)["total"], 0)

    def test_ordering_pagination_and_offset_beyond_results(self):
        self.add(50, created_at=self.now - timedelta(days=1))
        self.add(1, created_at=self.now + timedelta(seconds=1))
        self.add(3)
        self.add(2)
        self.add(99, user_id=101, created_at=self.now + timedelta(days=1))
        self.assertEqual([i["id"] for i in self.body(limit=2)["items"]], [1, 3])
        page = self.body(limit=2, offset=2)
        self.assertEqual(page["total"], 4)
        self.assertEqual(page["limit"], 2)
        self.assertEqual(page["offset"], 2)
        self.assertEqual([i["id"] for i in page["items"]], [2, 50])
        self.assertEqual(self.body(limit=100, offset=20), {"total": 4, "limit": 100, "offset": 20, "items": []})

    def test_read_filter_applies_to_count_before_pagination(self):
        for identifier in range(1, 7):
            self.add(identifier, is_read=identifier % 2 == 0)
        self.add(99, user_id=101, is_read=True)
        for value, expected in ((True, [6, 4, 2]), (False, [5, 3, 1])):
            with self.subTest(value=value):
                body = self.body(is_read=str(value).lower(), limit=2, offset=1)
                self.assertEqual(body["total"], 3)
                self.assertEqual([i["id"] for i in body["items"]], expected[1:])
        self.assertEqual(self.body()["total"], 6)
        self.login(103)
        self.assertEqual(self.body(is_read=True)["items"], [])

    def test_invalid_query_values_are_rejected(self):
        for params in ({"limit": 0}, {"limit": -1}, {"limit": 101}, {"limit": "x"},
                       {"offset": -1}, {"offset": "x"}, {"offset": 9223372036854775808},
                       {"is_read": "invalid"}):
            with self.subTest(params=params):
                self.assertEqual(self.get(**params).status_code, 422)

    def test_missing_invalid_expired_and_stale_tokens(self):
        expired = jwt.encode({"sub": "102", "exp": 1, "pwdv": "stale"},
                             settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        original = self.headers
        for headers in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": f"Bearer {expired}"}):
            with self.subTest(headers=headers):
                self.headers = headers
                with patch.object(NotificationRepository, "list_for_user") as query:
                    response = self.get()
                self.assertEqual(response.status_code, 401)
                query.assert_not_called()
        self.headers = original
        self.change(User, 102, password_hash="changed-hash")
        self.assertEqual(self.get().status_code, 401)

    def test_mandatory_password_change_uses_existing_authentication_rule(self):
        self.change(User, 102, must_change_password=True)
        response = self.get()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Password change required.")

    def test_reads_do_not_mark_read_and_query_count_is_constant(self):
        for identifier in range(1, 8):
            self.add(identifier)
        statements = []
        def record(conn, cursor, statement, parameters, context, many):
            statements.append(statement)
        self.db.expire_all()
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            self.body(limit=1)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(len(statements), 3)  # authenticated user, total, items
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))
        self.db.expire_all()
        self.assertFalse(self.db.get(Notification, 7).is_read)

    def test_database_errors_are_sanitized(self):
        for method in ("scalar", "scalars"):
            with self.subTest(method=method), patch.object(self.db, method, side_effect=SQLAlchemyError("private database details")):
                response = self.get()
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json(), {"detail": "Unable to retrieve notifications."})

    def test_explicit_response_schemas_validate_required_fields_and_utc(self):
        self.add(1)
        body = self.body()
        NotificationPage.model_validate(body)
        item = body["items"][0]
        for key, value in (("title", None), ("created_at", "invalid"), ("id", "invalid")):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                NotificationItem.model_validate({**item, key: value})
        with self.assertRaises(ValidationError):
            NotificationItem.model_validate({key: value for key, value in item.items() if key != "message"})
        utc = NotificationItem.model_validate({**item, "created_at": self.now.astimezone(timezone(timedelta(hours=-5)))})
        self.assertEqual(utc.created_at, self.now)
        self.assertEqual(utc.created_at.utcoffset(), timedelta(0))


class NotificationUnreadCountTests(unittest.TestCase):
    setUp = NotificationTests.setUp
    login = NotificationTests.login
    change = NotificationTests.change
    add = NotificationTests.add

    def get(self, **params):
        return self.client.get("/notifications/unread-count", headers=self.headers, params=params)

    def assert_count(self, expected, **params):
        response = self.get(**params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {"unread_count": expected})

    def test_empty_and_all_read_return_zero(self):
        self.assert_count(0)
        self.add(1, is_read=True)
        self.add(2, is_read=True)
        self.assert_count(0)

    def test_mixed_statuses_count_all_unread_without_pagination(self):
        for identifier in range(1, 31):
            self.add(identifier, is_read=identifier % 3 == 0)
        self.assert_count(20, limit=1, offset=100, is_read=True)
        listing = self.client.get("/notifications", headers=self.headers,
                                  params={"is_read": "false", "limit": 1})
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()["total"], 20)
        self.assertEqual(len(listing.json()["items"]), 1)

    def test_user_and_admin_counts_are_isolated_from_client_parameters(self):
        self.add(1, user_id=102)
        self.add(2, user_id=102)
        self.add(3, user_id=101)
        self.add(4, user_id=101, is_read=True)
        self.add(5, user_id=104)
        for user, expected in ((102, 2), (101, 1), (104, 1), (103, 0)):
            with self.subTest(user=user):
                self.login(user)
                self.assert_count(expected, user_id=102, role="ADMIN")

    def test_missing_invalid_expired_and_stale_tokens_are_rejected_before_count(self):
        expired = jwt.encode({"sub": "102", "exp": 1, "pwdv": "stale"},
                             settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        original = self.headers
        for headers in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": f"Bearer {expired}"}):
            with self.subTest(headers=headers):
                self.headers = headers
                with patch.object(NotificationRepository, "unread_count") as query:
                    self.assertEqual(self.get().status_code, 401)
                query.assert_not_called()
        self.headers = original
        self.change(User, 102, password_hash="changed-hash")
        with patch.object(NotificationRepository, "unread_count") as query:
            self.assertEqual(self.get().status_code, 401)
        query.assert_not_called()

    def test_mandatory_password_change_blocks_count(self):
        self.change(User, 102, must_change_password=True)
        with patch.object(NotificationRepository, "unread_count") as query:
            response = self.get()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Password change required.")
        query.assert_not_called()

    def test_one_count_query_and_no_notification_rows_loaded_or_modified(self):
        self.add(1)
        statements = []
        def record(conn, cursor, statement, parameters, context, many):
            statements.append(statement)
        self.db.expire_all()
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            with patch.object(self.db, "scalars", side_effect=AssertionError("Must not load notification rows")):
                self.assert_count(1)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(len(statements), 2)  # authenticated user and COUNT(*)
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements))
        notification_queries = [sql for sql in statements if "FROM notifications" in sql]
        self.assertEqual(len(notification_queries), 1)
        self.assertIn("count(*)", notification_queries[0].lower())
        self.db.expire_all()
        self.assertFalse(self.db.get(Notification, 1).is_read)

    def test_database_failure_is_sanitized(self):
        with patch.object(self.db, "scalar", side_effect=SQLAlchemyError("private database details")):
            response = self.get()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to retrieve notifications."})

    def test_unread_count_schema_is_explicit_and_nonnegative(self):
        from app.schemas.notification import NotificationUnreadCount
        self.assertEqual(NotificationUnreadCount(unread_count=5).model_dump(), {"unread_count": 5})
        with self.assertRaises(ValidationError):
            NotificationUnreadCount(unread_count=-1)
        schema = self.client.get("/openapi.json").json()
        parameters = schema["paths"]["/notifications/unread-count"]["get"].get("parameters", [])
        self.assertEqual(parameters, [])


@unittest.skipUnless(os.environ.get("NOTIFICATIONS_TEST_DATABASE_URL"), "opt-in isolated PostgreSQL migration test")
class PostgreSQLNotificationMigrationTests(unittest.TestCase):
    def test_migration_defaults_indexes_foreign_key_and_orm(self):
        # Never use the application DATABASE_URL; isolate DDL in a unique scratch schema.
        engine = create_engine(os.environ["NOTIFICATIONS_TEST_DATABASE_URL"], isolation_level="AUTOCOMMIT")
        schema = "notification_test_" + uuid4().hex
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                try:
                    connection.exec_driver_sql(f'SET search_path TO "{schema}"')
                    connection.exec_driver_sql('CREATE TABLE users (id BIGINT PRIMARY KEY)')
                    migration = Path(__file__).resolve().parents[1] / "migrations/001_create_notifications.sql"
                    connection.exec_driver_sql(migration.read_text())
                    connection.exec_driver_sql('INSERT INTO users (id) VALUES (1)')
                    with Session(bind=connection) as db:
                        item = Notification(user_id=1, type="TEST", title="Test", message="Message")
                        db.add(item)
                        db.commit()
                        db.refresh(item)
                        self.assertGreater(item.id, 0)
                        self.assertFalse(item.is_read)
                        self.assertIsNotNone(item.created_at.tzinfo)
                        self.assertIsNone(item.resource_id)
                        NotificationItem.model_validate(item)
                        items, count = NotificationRepository(db).list_for_user(1, 20, 0, False)
                        self.assertEqual(count, 1)
                        self.assertEqual([row.id for row in items], [item.id])
                    indexes = inspect(connection).get_indexes("notifications", schema=schema)
                    self.assertEqual({i["name"] for i in indexes},
                                     {"ix_notifications_user_created_at_id", "ix_notifications_user_is_read_created_at_id"})
                    definitions = connection.execute(text('SELECT indexdef FROM pg_indexes WHERE schemaname = :schema'),
                                                     {"schema": schema}).scalars().all()
                    self.assertEqual(sum("created_at DESC, id DESC" in definition for definition in definitions), 2)
                    with self.assertRaises(IntegrityError):
                        connection.exec_driver_sql("INSERT INTO notifications (user_id, type, title, message) VALUES (999, 'TEST', 'T', 'M')")
                    connection.exec_driver_sql('DELETE FROM users WHERE id = 1')
                    self.assertEqual(connection.scalar(text('SELECT count(*) FROM notifications')), 0)
                finally:
                    connection.exec_driver_sql('SET search_path TO public')
                    connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            engine.dispose()
