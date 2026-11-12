from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.core.exceptions import NotificationNotFound, NotificationPersistenceError
from app.models.enums import MembershipStatus, MinistryStatus, UserStatus
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.notification import Notification
from app.models.rehearsal_attendance import RehearsalAttendance
from app.models.user import User
from app.repositories.authorization import AuthorizationRepository


class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_for_delivery(self, notification_id: int) -> Notification | None:
        # Internal server-only lookup; the persisted row determines the recipient.
        return self.db.get(Notification, notification_id)

    def assignment_user_ids(self, rehearsal_id: int, ministry_id: int, member_ids: list[int]) -> list[int]:
        # Only memberships already assigned in this transaction can be recipients.
        return list(self.db.scalars(select(MinistryMember.user_id).join(
            RehearsalAttendance, RehearsalAttendance.ministry_member_id == MinistryMember.id,
        ).where(
            MinistryMember.id.in_(member_ids), MinistryMember.ministry_id == ministry_id,
            RehearsalAttendance.rehearsal_id == rehearsal_id,
        ).distinct().order_by(MinistryMember.user_id)))

    def pending_membership_recipients(self, membership_ids: list[int]):
        pending = aliased(MinistryMember)
        admin_membership = aliased(MinistryMember)
        return self.db.execute(select(pending.ministry_id, User.id.label("user_id"))
            .select_from(pending)
            .join(Ministry, Ministry.id == pending.ministry_id)
            .join(admin_membership, and_(
                admin_membership.ministry_id == pending.ministry_id,
                admin_membership.status == MembershipStatus.ACTIVE,
                admin_membership.deleted_at.is_(None),
            ))
            .join(User, User.id == admin_membership.user_id)
            .where(
                pending.id.in_(membership_ids), pending.status == MembershipStatus.PENDING,
                pending.deleted_at.is_(None), Ministry.status == MinistryStatus.ACTIVE,
                Ministry.deleted_at.is_(None), User.status == UserStatus.ACTIVE, User.deleted_at.is_(None),
                User.id.in_(AuthorizationRepository(self.db).global_admin_user_ids_statement()),
            ).distinct().order_by(pending.ministry_id, User.id)
        ).mappings().all()

    def add_many(self, notifications: list[Notification]) -> list[int]:
        # The caller owns the assignment transaction, including commit/rollback.
        self.db.add_all(notifications)
        self.db.flush()
        return [notification.id for notification in notifications]

    def unread_count(self, user_id: int) -> int:
        return self.db.scalar(select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id, Notification.is_read.is_(False),
        )) or 0

    def mark_read(self, user_id: int, notification_id: int):
        try:
            row = self.db.execute(update(Notification).where(
                Notification.id == notification_id, Notification.user_id == user_id,
            ).values(is_read=True).returning(
                Notification.id, Notification.is_read,
            ).execution_options(synchronize_session=False)).mappings().one_or_none()
            # Match already-read rows too: repeated calls return the same result.
            if row is None:
                raise NotificationNotFound
            self.db.commit()
            return row
        except SQLAlchemyError:
            self.db.rollback()
            raise NotificationPersistenceError from None
        except Exception:
            self.db.rollback()
            raise

    def mark_all_read(self, user_id: int) -> int:
        try:
            result = self.db.execute(update(Notification).where(
                Notification.user_id == user_id, Notification.is_read.is_(False),
            ).values(is_read=True).execution_options(synchronize_session=False))
            # PostgreSQL/psycopg reports matched rows reliably for this UPDATE.
            # The unread predicate excludes rows already in the target state.
            count = result.rowcount
            if not result.supports_sane_rowcount() or count < 0:
                raise SQLAlchemyError("Unable to determine updated notification count.")
            self.db.commit()
            return count
        except SQLAlchemyError:
            self.db.rollback()
            raise NotificationPersistenceError from None
        except Exception:
            self.db.rollback()
            raise

    def list_for_user(self, user_id: int, limit: int, offset: int, is_read: bool | None):
        conditions = [Notification.user_id == user_id]
        if is_read is not None:
            conditions.append(Notification.is_read == is_read)
        total = self.db.scalar(select(func.count()).select_from(Notification).where(*conditions))
        items = self.db.scalars(select(Notification).where(*conditions).order_by(
            Notification.created_at.desc(), Notification.id.desc(),
        ).limit(limit).offset(offset)).all()
        return items, total or 0
