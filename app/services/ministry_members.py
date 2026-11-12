from app.core.exceptions import MinistryNotFound
from app.models.enums import MembershipStatus
from app.repositories.ministry_members import MinistryMembersRepository
from app.repositories.notification import NotificationRepository
from app.services.notification import NotificationService
from app.schemas.ministry_members import (MembershipStatusResponse, MinistryMemberInfo,
                                         MinistryMembersResponse)


class MinistryMembersService:
    def __init__(self, repository: MinistryMembersRepository, notifications: NotificationService | None = None):
        self.repository = repository
        self.notifications = notifications if notifications is not None else NotificationService(
            NotificationRepository(repository.db),
        )

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

    def update_status(self, ministry_id: int, membership_id: int, status: MembershipStatus, actor_user_id: int) -> MembershipStatusResponse:
        notification_ids = self.repository.update_status(
            ministry_id, membership_id, status, actor_user_id, on_approved=self.notifications.create_membership_approved,
        )
        self.notifications.publish_after_commit(notification_ids)
        return MembershipStatusResponse(
            ministry_id=ministry_id, ministry_member_id=membership_id, status=status,
        )
