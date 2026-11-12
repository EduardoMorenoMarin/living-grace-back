"""Process-local tickets and user-scoped sockets; replace with shared infrastructure for multiple workers."""
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from contextlib import suppress
import hashlib
import secrets
from threading import Lock
import time

from fastapi import WebSocket


PROTOCOL = "living-grace.notifications"
TICKET_TTL_SECONDS = 60


class NotificationTicketStore:
    def __init__(self, ttl_seconds=TICKET_TTL_SECONDS, max_tickets=10000):
        self.ttl_seconds = ttl_seconds
        self.max_tickets = max_tickets
        self._tickets: dict[str, tuple[float, str]] = {}
        self._lock = Lock()

    def _prune(self, now):
        self._tickets = {key: value for key, value in self._tickets.items() if value[0] > now}

    def issue(self, access_token: str) -> str:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if len(self._tickets) >= self.max_tickets:
                raise RuntimeError("Ticket capacity reached.")
            ticket = secrets.token_urlsafe(32)
            self._tickets[hashlib.sha256(ticket.encode()).hexdigest()] = (now + self.ttl_seconds, access_token)
            return ticket

    def consume(self, ticket: str) -> str | None:
        if len(ticket) != 43:
            return None
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            entry = self._tickets.pop(hashlib.sha256(ticket.encode()).hexdigest(), None)
            return entry[1] if entry else None


@dataclass(eq=False)
class NotificationConnection:
    user_id: int
    websocket: WebSocket
    authorized: Callable[[], Awaitable[bool]]
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class NotificationConnectionManager:
    def __init__(self):
        self._connections: dict[int, set[NotificationConnection]] = {}

    async def connect(self, user_id, websocket, authorized):
        await websocket.accept(subprotocol=PROTOCOL)
        connection = NotificationConnection(user_id, websocket, authorized)
        self._connections.setdefault(user_id, set()).add(connection)
        return connection

    def disconnect(self, connection):
        connections = self._connections.get(connection.user_id)
        if connections is not None:
            connections.discard(connection)
            if not connections:
                self._connections.pop(connection.user_id, None)

    def connection_count(self, user_id):
        return len(self._connections.get(user_id, ()))

    def has_connections(self):
        return bool(self._connections)

    async def close(self, connection, code=1008):
        self.disconnect(connection)
        async with connection.send_lock:
            with suppress(Exception):
                await asyncio.wait_for(connection.websocket.close(code=code), timeout=1)

    async def _send(self, connection, event):
        try:
            async with connection.send_lock:
                if not await connection.authorized():
                    raise PermissionError
                await asyncio.wait_for(connection.websocket.send_json(event), timeout=5)
            return 1
        except Exception:
            # A broken/slow socket never prevents delivery to another connection.
            await self.close(connection)
            return 0

    async def send_to_user(self, user_id: int, event: dict) -> int:
        connections = tuple(self._connections.get(user_id, ()))
        if not connections:
            return 0
        return sum(await asyncio.gather(*(self._send(connection, event) for connection in connections)))
