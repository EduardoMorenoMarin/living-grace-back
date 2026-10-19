from app.models.ministry_member import MinistryMember
from app.models.ministry_member_function import MinistryMemberFunction
from app.repositories.membership import MembershipRepository
from app.repositories.registration_roles import RegistrationRoleRepository
from app.schemas.membership import MembershipCreate, MembershipResponse


class MembershipRegistrationService:
    """Store validated selections using the registration transaction."""

    def __init__(self, repository: MembershipRepository, roles: RegistrationRoleRepository):
        self.repository = repository
        self.roles = roles

    def register(self, user_id: int, selections: list[MembershipCreate]) -> list[MembershipResponse]:
        result = []
        for selection in selections:
            membership = MinistryMember(
                user_id=user_id,
                ministry_id=selection.ministry_id,
                ministry_member_functions=[
                    MinistryMemberFunction(function_id=function_id)
                    for function_id in selection.function_ids
                ],
            )
            self.repository.add(membership)
            result.append(MembershipResponse(
                id=membership.id,
                ministry_id=membership.ministry_id,
                status=membership.status,
                function_ids=[assignment.function_id for assignment in membership.ministry_member_functions],
            ))
        if result:
            role_id = self.roles.get_user_role_id()
            self.roles.assign(role_id, [membership.id for membership in result])
        return result
