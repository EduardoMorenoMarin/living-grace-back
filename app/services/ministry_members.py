from app.core.exceptions import MinistryNotFound
from app.repositories.ministry_members import MinistryMembersRepository
from app.schemas.ministry_members import MinistryMemberInfo, MinistryMembersResponse


class MinistryMembersService:
    def __init__(self, repository: MinistryMembersRepository):
        self.repository = repository

    def list_members(self, ministry_id: int) -> MinistryMembersResponse:
        rows = self.repository.get_member_rows(ministry_id)
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
                    first_name=row["first_name"], last_name=row["last_name"],
                )
            member = members[member_id]
            if row["function"] is not None and row["function"] not in member.functions:
                member.functions.append(row["function"])
        return MinistryMembersResponse(ministry_id=ministry_id, members=list(members.values()))
