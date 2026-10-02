import logging

from app.core.exceptions import MinistryNotFound
from app.models.rehearsal import Rehearsal
from app.repositories.membership import MembershipRepository
from app.repositories.rehearsal import RehearsalRepository
from app.schemas.rehearsal import RehearsalCreate, RehearsalResponse


logger = logging.getLogger(__name__)


class RehearsalCreator:
    """Create schema-valid rehearsals for an existing ministry and authenticated user.

    Description is optional, including None/blank. Return the persisted rehearsal
    when storage succeeds; missing ministries and storage failures can raise.
    The caller supplies the trusted authenticated user ID separately from data.
    """

    def __init__(self, repository: RehearsalRepository, memberships: MembershipRepository):
        self.repository = repository
        self.memberships = memberships

    def create(self, data: RehearsalCreate, user_id: int) -> RehearsalResponse:
        with self.repository.transaction():
            if self.memberships.get_ministry(data.ministry_id) is None:
                raise MinistryNotFound("Ministry does not exist.")
            rehearsal = Rehearsal(**data.model_dump(), created_by=user_id)
            self.repository.add(rehearsal)
            result = RehearsalResponse.model_validate(rehearsal)
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
