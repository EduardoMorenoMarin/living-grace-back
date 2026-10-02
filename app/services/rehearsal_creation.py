from app.core.exceptions import MinistryNotFound, RestrictedRehearsalError
from app.models.rehearsal import Rehearsal
from app.repositories.membership import MembershipRepository
from app.repositories.rehearsal import RehearsalRepository
from app.schemas.rehearsal import RehearsalCreate, RehearsalResponse


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


class RestrictedRehearsalCreator(RehearsalCreator):
    """Intentional BEFORE-LSP violation: strengthen the description precondition."""

    def create(self, data: RehearsalCreate, user_id: int) -> RehearsalResponse:
        # Educational restriction, not a real ministry authorization policy.
        if data.description is None or not data.description.strip():
            raise RestrictedRehearsalError("Restricted creator requires a description.")
        return super().create(data, user_id)
