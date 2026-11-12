import logging

from app.core.exceptions import MinistryNotFound, RehearsalValidationError
from app.models.rehearsal import Rehearsal
from app.repositories.membership import MembershipRepository
from app.repositories.notification import NotificationRepository
from app.repositories.rehearsal import RehearsalRepository
from app.schemas.rehearsal import RehearsalCreate, RehearsalResponse
from app.services.notification import NotificationService


logger = logging.getLogger(__name__)


class RehearsalCreator:
    """Create schema-valid rehearsals for an existing ministry and authenticated user.

    Description is optional, including None/blank. Return the persisted rehearsal
    when storage succeeds; missing ministries and storage failures can raise.
    The caller supplies the trusted authenticated user ID separately from data.
    """

    def __init__(self, repository: RehearsalRepository, memberships: MembershipRepository,
                 notifications: NotificationService | None = None):
        self.repository = repository
        self.memberships = memberships
        self.notifications = notifications if notifications is not None else NotificationService(
            NotificationRepository(repository.db),
        )

    def create(self, data: RehearsalCreate, user_id: int) -> RehearsalResponse:
        with self.repository.transaction():
            if self.memberships.get_ministry(data.ministry_id) is None:
                raise MinistryNotFound("Ministry does not exist.")
            eligible_ids = self.repository.eligible_participant_ids(data.ministry_id, data.ministry_member_ids)
            if eligible_ids != set(data.ministry_member_ids):
                raise RehearsalValidationError("Selected participants are not available for this ministry.")
            rehearsal = Rehearsal(**data.model_dump(exclude={"ministry_member_ids"}), created_by=user_id)
            self.repository.add(rehearsal)
            self.repository.add_attendance(rehearsal.id, data.ministry_member_ids)
            notification_ids = self.notifications.create_rehearsal_assignments(rehearsal, data.ministry_member_ids)
            result = RehearsalResponse.model_validate(rehearsal)
        self.notifications.publish_after_commit(notification_ids)
        return result


class LoggingRehearsalCreator(RehearsalCreator):
    """Add best-effort logging while preserving the base creation contract."""

    def create(self, data: RehearsalCreate, user_id: int) -> RehearsalResponse:
        result = super().create(data, user_id)
        try:
            logger.info("Created rehearsal %s", result.id)
        except Exception:
            # A logging handler must not turn a committed creation into a failure.
            pass
        return result
