from app.core.exceptions import RegistrationValidationError
from app.models.enums import FunctionStatus, MinistryStatus
from app.repositories.membership import MembershipRepository
from app.schemas.membership import MembershipCreate


class MembershipValidator:
    """Validate membership selections against existing database records."""

    def __init__(self, repository: MembershipRepository):
        self.repository = repository

    def validate(self, memberships: list[MembershipCreate]) -> None:
        for selection in memberships:
            ministry = self.repository.get_ministry(selection.ministry_id)
            if ministry is None or ministry.status != MinistryStatus.ACTIVE or ministry.deleted_at is not None:
                raise RegistrationValidationError("Selected ministry is not available.")

            functions = self.repository.get_functions(selection.function_ids)
            if {function.id for function in functions} != set(selection.function_ids):
                raise RegistrationValidationError("Selected function is not available.")
            for function in functions:
                if function.status != FunctionStatus.ACTIVE or function.deleted_at is not None:
                    raise RegistrationValidationError("Selected function is not available.")
                if function.ministry_id != selection.ministry_id:
                    raise RegistrationValidationError("Selected function does not belong to the ministry.")
