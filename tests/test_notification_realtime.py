"""Ticket-authenticated sockets, user isolation, and committed-only delivery."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import jwt
import test_notifications as fixtures
import test_rehearsal_read as rehearsal_fixtures
from sqlalchemy import BigInteger, Integer, MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.database import Base
from app.core.notification_sockets import PROTOCOL, NotificationConnectionManager, NotificationTicketStore
from app.dependencies.notification_realtime import (
    get_notification_connection_manager, get_notification_socket_authenticator, get_notification_ticket_store,
)
from app.main import app
from app.models import Notification, User
from app.services.notification_realtime import NotificationDeliveryService, NotificationSocketAuthenticator


class NotificationSocketTests(unittest.TestCase):
    login = fixtures.NotificationTests.login
    change = fixtures.NotificationTests.change
    add = fixtures.NotificationTests.add

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # Real sockets authenticate concurrently. Give each session a separate
        # SQLite connection, as PostgreSQL does, rather than sharing StaticPool's
        # single DBAPI connection across worker threads.
        engine = create_engine("sqlite:///" + str(Path(directory.name) / "sockets.db"),
                               connect_args={"check_same_thread": False})
        with patch.object(rehearsal_fixtures, "create_engine", return_value=engine):
            fixtures.NotificationTests.setUp(self)
        self.manager = NotificationConnectionManager()
        self.tickets = NotificationTicketStore()
        self.authenticator = NotificationSocketAuthenticator(sessionmaker(bind=self.engine))
        self.delivery = NotificationDeliveryService(sessionmaker(bind=self.engine), self.manager)
        self.stack.enter_context(patch.dict(app.dependency_overrides, {
            get_notification_connection_manager: lambda: self.manager,
            get_notification_ticket_store: lambda: self.tickets,
            get_notification_socket_authenticator: lambda: self.authenticator,
        }))
        self.origin = {"Origin": settings.FRONTEND_ORIGIN.rstrip("/")}

    def ticket(self):
        response = self.client.post("/notifications/ws-ticket", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["expires_in"], 60)
        self.assertEqual(len(body["ticket"]), 43)
        self.assertNotIn(self.headers["Authorization"].split()[1], response.text)
        return body["ticket"]

    def connect(self, ticket=None, *, headers=None, url="/ws/notifications", protocols=None):
        return self.client.websocket_connect(url, headers=dict(self.origin if headers is None else headers),
                                             subprotocols=protocols or [PROTOCOL, "ticket." + (ticket or self.ticket())])

    def rejected(self, **kwargs):
        with self.assertRaises(WebSocketDisconnect) as raised:
            with self.connect(**kwargs):
                self.fail("Invalid socket accepted")
        self.assertEqual(raised.exception.code, 1008)

    def publish(self, identifier):
        return self.client.portal.call(self.delivery.publish_created, identifier)

    def count(self, user_id):
        return self.client.portal.call(self.manager.connection_count, user_id)

    def test_authenticated_event_structure_read_state_and_cleanup(self):
        self.add(42)
        ticket = self.ticket()
        with self.connect(ticket) as socket:
            self.assertEqual(socket.accepted_subprotocol, PROTOCOL)
            self.assertEqual(self.count(102), 1)
            self.assertEqual(self.publish(42), 1)
            self.assertEqual(socket.receive_json(), {
                "type": "notification.created",
                "data": {"id": 42, "notification_type": "REHEARSAL_ASSIGNED", "title": "Notification 42",
                         "message": "You have a rehearsal.", "resource_type": "rehearsal", "resource_id": 301,
                         "is_read": False, "created_at": "2026-11-15T17:00:00Z"},
            })
            self.assertEqual(self.client.get("/notifications/unread-count", headers=self.headers).json(),
                             {"unread_count": 1})
        self.assertEqual(self.count(102), 0)
        self.db.expire_all()
        self.assertFalse(self.db.get(Notification, 42).is_read)
        self.rejected(ticket=ticket)  # ticket is single use

    def test_multiple_connections_per_user_and_cross_user_isolation(self):
        self.add(42)
        self.add(43, user_id=101)
        a1, a2 = self.ticket(), self.ticket()
        self.login(101)
        b = self.ticket()
        with self.connect(a1) as first, self.connect(a2) as second, self.connect(b) as admin:
            self.assertEqual(self.count(102), 2)
            self.assertEqual(self.count(101), 1)
            self.assertEqual(self.publish(42), 2)
            self.assertEqual(first.receive_json()["data"]["id"], 42)
            self.assertEqual(second.receive_json()["data"]["id"], 42)
            self.assertEqual(self.publish(43), 1)
            self.assertEqual(admin.receive_json()["data"]["id"], 43)
        self.assertEqual(self.count(102), 0)
        self.assertEqual(self.count(101), 0)

    def test_missing_invalid_and_expired_tickets_or_tokens_are_rejected(self):
        self.rejected(protocols=[PROTOCOL])
        self.rejected(ticket="invalid")
        self.rejected(ticket="x" * 43)
        expired = jwt.encode({"sub": "102", "exp": 1, "pwdv": "invalid"},
                             settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        for access_token in ("invalid-jwt", expired):
            self.rejected(ticket=self.tickets.issue(access_token))
        with patch("app.core.notification_sockets.time.monotonic", return_value=100):
            ticket = self.ticket()
        with patch("app.core.notification_sockets.time.monotonic", return_value=160):
            self.rejected(ticket=ticket)

    def test_ticket_endpoint_uses_existing_jwt_and_password_change_rules(self):
        original = self.headers
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            self.assertEqual(self.client.post("/notifications/ws-ticket", headers=headers).status_code, 401)
        self.change(User, 102, password_hash="revoked-hash")
        self.assertEqual(self.client.post("/notifications/ws-ticket", headers=original).status_code, 401)
        self.login(102)
        self.change(User, 102, must_change_password=True)
        self.assertEqual(self.client.post("/notifications/ws-ticket", headers=self.headers).status_code, 403)

    def test_password_reset_revokes_outstanding_ticket_and_restricts_new_ticket(self):
        ticket = self.ticket()
        self.login(101)
        response = self.client.post("/users/102/reset-password", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.rejected(ticket=ticket)
        self.login(102)  # fresh JWT under new credential still requires password change
        self.assertEqual(self.client.post("/notifications/ws-ticket", headers=self.headers).status_code, 403)

    def test_must_change_password_after_issue_rejects_handshake(self):
        ticket = self.ticket()
        self.change(User, 102, must_change_password=True)
        self.rejected(ticket=ticket)

    def test_origins_and_query_credentials_are_rejected_before_acceptance(self):
        for headers in ({}, {"Origin": "https://attacker.example"}, {"Origin": "null"},
                        {"Origin": settings.FRONTEND_ORIGIN + ".attacker.example"}):
            self.rejected(ticket=self.ticket(), headers=headers)
        self.rejected(ticket=self.ticket(), url="/ws/notifications?user_id=101")
        self.rejected(ticket=self.ticket(), url="/ws/notifications?token=not-accepted")

    def test_offline_and_missing_notification_leave_database_available(self):
        self.add(42)
        self.assertEqual(self.publish(42), 0)
        self.assertEqual(self.publish(999), 0)
        response = self.client.get("/notifications", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], 42)

    def test_reset_connected_client_blocks_delivery_and_cleans_up(self):
        self.add(42)
        with self.connect() as socket:
            self.change(User, 102, password_hash="new-reset-hash", must_change_password=True)
            self.assertEqual(self.publish(42), 0)
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()
            self.assertEqual(self.count(102), 0)

    def test_periodic_authentication_closes_revoked_idle_connection(self):
        with patch("app.api.routes.notification_realtime.AUTH_RECHECK_SECONDS", 0.02):
            with self.connect() as socket:
                self.change(User, 102, must_change_password=True)
                with self.assertRaises(WebSocketDisconnect):
                    socket.receive_json()
        self.assertEqual(self.count(102), 0)

    def test_client_messages_cannot_publish_or_select_a_user(self):
        with self.connect() as socket:
            socket.send_json({"user_id": 101, "type": "notification.created", "data": {}})
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()
        self.assertEqual(self.count(102), 0)


class NotificationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_connection_does_not_affect_other_connections(self):
        manager = NotificationConnectionManager()
        failed, healthy, other = AsyncMock(), AsyncMock(), AsyncMock()
        failed.send_json.side_effect = RuntimeError("closed")
        authorize = AsyncMock(return_value=True)
        await manager.connect(1, failed, authorize)
        await manager.connect(1, healthy, authorize)
        await manager.connect(2, other, authorize)
        self.assertEqual(await manager.send_to_user(1, {"type": "notification.created", "data": {}}), 1)
        self.assertEqual(manager.connection_count(1), 1)
        healthy.send_json.assert_awaited_once()
        other.send_json.assert_not_awaited()
        failed.close.assert_awaited_once()

    async def test_no_delivery_before_commit_or_after_rollback(self):
        with TemporaryDirectory() as directory:
            engine = create_engine("sqlite:///" + str(Path(directory) / "commits.db"))
            metadata = MetaData()
            for table in Base.metadata.sorted_tables:
                copy = table.to_metadata(metadata)
                for column in copy.columns:
                    if isinstance(column.type, BigInteger):
                        column.type = Integer()
            metadata.create_all(engine)
            manager = NotificationConnectionManager()
            socket = AsyncMock()
            await manager.connect(1, socket, AsyncMock(return_value=True))
            delivery = NotificationDeliveryService(sessionmaker(bind=engine), manager)
            try:
                with Session(engine) as db:
                    db.add(User(id=1, email="test@example.com", password_hash="hash", first_name="Test", last_name="User"))
                    db.commit()
                    db.add(Notification(id=42, user_id=1, type="TEST", title="Test", message="Message"))
                    db.flush()
                    self.assertEqual(await delivery.publish_created(42), 0)
                    socket.send_json.assert_not_awaited()
                    db.commit()
                    self.assertEqual(await delivery.publish_created(42), 1)
                    self.assertEqual(socket.send_json.call_args.args[0]["data"]["id"], 42)
                    db.add(Notification(id=43, user_id=1, type="TEST", title="Rollback", message="Message"))
                    db.flush()
                    db.rollback()
                    self.assertEqual(await delivery.publish_created(43), 0)
                    self.assertEqual(socket.send_json.await_count, 1)
            finally:
                engine.dispose()

    async def test_database_delivery_failure_is_best_effort(self):
        from sqlalchemy.exc import SQLAlchemyError
        delivery = NotificationDeliveryService(None, NotificationConnectionManager())
        with patch.object(delivery, "_load_committed", side_effect=SQLAlchemyError("private")):
            self.assertEqual(await delivery.publish_created(42), 0)


class NotificationTicketTests(unittest.TestCase):
    def test_capacity_expiration_and_single_use(self):
        tickets = NotificationTicketStore(max_tickets=1)
        with patch("app.core.notification_sockets.time.monotonic", return_value=100):
            first = tickets.issue("token")
            with self.assertRaises(RuntimeError):
                tickets.issue("another")
            self.assertEqual(tickets.consume(first), "token")
            self.assertIsNone(tickets.consume(first))
            expired = tickets.issue("old")
        with patch("app.core.notification_sockets.time.monotonic", return_value=160):
            self.assertIsNone(tickets.consume(expired))
            self.assertIsInstance(tickets.issue("new"), str)
