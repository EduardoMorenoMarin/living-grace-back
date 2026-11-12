import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.notification_sockets import PROTOCOL, NotificationConnectionManager, NotificationTicketStore
from app.dependencies.auth import bearer, get_current_user
from app.dependencies.notification_realtime import (
    get_notification_connection_manager, get_notification_socket_authenticator, get_notification_ticket_store,
)
from app.models.user import User
from app.schemas.notification_realtime import NotificationSocketTicket
from app.services.notification_realtime import NotificationSocketAuthenticator


router = APIRouter(tags=["notifications"])
AUTH_RECHECK_SECONDS = 30


@router.post("/notifications/ws-ticket", response_model=NotificationSocketTicket)
def issue_notification_socket_ticket(
    user: Annotated[User, Depends(get_current_user)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    tickets: Annotated[NotificationTicketStore, Depends(get_notification_ticket_store)],
    response: Response,
) -> NotificationSocketTicket:
    response.headers["Cache-Control"] = "no-store"
    try:
        ticket = tickets.issue(credentials.credentials)
    except RuntimeError:
        raise HTTPException(503, "Unable to issue notification connection ticket.",
                            headers={"Cache-Control": "no-store"}) from None
    return NotificationSocketTicket(ticket=ticket, expires_in=tickets.ttl_seconds)


@router.websocket("/ws/notifications")
async def notification_socket(
    websocket: WebSocket,
    tickets: Annotated[NotificationTicketStore, Depends(get_notification_ticket_store)],
    manager: Annotated[NotificationConnectionManager, Depends(get_notification_connection_manager)],
    authenticator: Annotated[NotificationSocketAuthenticator, Depends(get_notification_socket_authenticator)],
):
    origins = websocket.headers.getlist("origin")
    if origins != [settings.FRONTEND_ORIGIN.rstrip("/")] or websocket.query_params:
        await websocket.close(code=1008)
        return
    protocols = websocket.scope.get("subprotocols", [])
    ticket_protocols = [value[7:] for value in protocols if value.startswith("ticket.")]
    if PROTOCOL not in protocols or len(ticket_protocols) != 1 or len(protocols) != 2:
        await websocket.close(code=1008)
        return
    access_token = tickets.consume(ticket_protocols[0])
    if access_token is None:
        await websocket.close(code=1008)
        return
    try:
        user_id = await run_in_threadpool(authenticator.authenticate, access_token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    async def authorized():
        try:
            return await run_in_threadpool(authenticator.authenticate, access_token) == user_id
        except HTTPException:
            return False

    connection = await manager.connect(user_id, websocket, authorized)
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=AUTH_RECHECK_SECONDS)
                if message["type"] == "websocket.disconnect":
                    break
                # This is a server-to-client stream, not a client event/command API.
                await manager.close(connection)
                break
            except asyncio.TimeoutError:
                if not await authorized():
                    await manager.close(connection)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(connection)
