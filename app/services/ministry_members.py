from app.core.exceptions import MinistryNotFound
from app.models.enums import MembershipStatus
from app.repositories.ministry_members import MinistryMembersRepository
from app.schemas.ministry_members import (MembershipStatusResponse, MinistryMemberInfo,
                                         MinistryMembersResponse)


class MinistryMembersService:
    def __init__(self, repository: MinistryMembersRepository):
        self.repository = repository

    def list_members(self, ministry_id: int, status: MembershipStatus = MembershipStatus.ACTIVE) -> MinistryMembersResponse:
        rows = self.repository.get_member_rows(ministry_id, status)
        if not rows:
            raise MinistryNotFound("Ministry does not exist or is unavailable.")
        members: dict[int, MinistryMemberInfo] = {}
        for row in rows:
            member_id = row["ministry_member_id"]
            if member_id is None:
                continue
            if member_id not in members:
                members[member_id] = MinistryMemberInfo(
                    ministry_member_id=member_id, user_id=row["user_id"],
                    first_name=row["first_name"], last_name=row["last_name"], status=row["status"],
                )
            member = members[member_id]
            if row["function"] is not None and row["function"] not in member.functions:
                member.functions.append(row["function"])
        return MinistryMembersResponse(ministry_id=ministry_id, members=list(members.values()))

    def approve(self, ministry_id: int, membership_id: int) -> MembershipStatusResponse:
        self.repository.approve(ministry_id, membership_id)
        return MembershipStatusResponse(
            ministry_id=ministry_id, ministry_member_id=membership_id, status=MembershipStatus.ACTIVE,
        )
