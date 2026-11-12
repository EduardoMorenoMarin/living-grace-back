from app.core.database import SessionLocal
from app.core.notification_sockets import NotificationConnectionManager, NotificationTicketStore
from app.services.notification_realtime import NotificationDeliveryService, NotificationSocketAuthenticator


# One registry per process, shared by the ticket route, socket route, and future producers.
manager = NotificationConnectionManager()
tickets = NotificationTicketStore()


def get_notification_connection_manager():
    return manager


def get_notification_ticket_store():
    return tickets


def get_notification_socket_authenticator():
    return NotificationSocketAuthenticator(SessionLocal)


def get_notification_delivery_service():
    return NotificationDeliveryService(SessionLocal, manager)
