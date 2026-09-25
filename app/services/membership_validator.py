from collections.abc import Sequence

from app.core.exceptions import RegistrationValidationError
from app.models.enums import FunctionStatus, MinistryStatus
from app.repositories.membership import MembershipRepository
from app.schemas.membership import MembershipCreate
from app.services.membership_rules import MembershipRule


class MembershipValidator:
    """Enforce reference invariants, then run injected membership requirements."""

    def __init__(self, repository: MembershipRepository, rules: Sequence[MembershipRule] = ()):
        self.repository = repository
        self.rules = tuple(rules)

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

            # Extensions receive valid references and cannot replace these guards.
            selected_functions = tuple(functions)
            for rule in self.rules:
                rule.validate(ministry, selected_functions)
