from app.core.exceptions import ProfileUserUnavailable
from app.repositories.profile import ProfileRepository
from app.schemas.profile import ProfileMembership, UserProfile


class ProfileService:
    def __init__(self, repository: ProfileRepository):
        self.repository = repository

    def get_profile(self, user_id: int) -> UserProfile:
        rows = self.repository.get_profile_rows(user_id)
        if not rows:
            raise ProfileUserUnavailable
        memberships: dict[int, ProfileMembership] = {}
        for row in rows:
            if row["ministry_id"] is None:
                continue
            membership = memberships.setdefault(
                row["ministry_id"], ProfileMembership(ministry=row["ministry"]),
            )
            if row["function"] is not None and row["function"] not in membership.functions:
                membership.functions.append(row["function"])
        return UserProfile(
            **{key: rows[0][key] for key in (
                "first_name", "last_name", "email", "phone", "birth_date", "created_at", "is_global_admin",
            )},
            memberships=list(memberships.values()),
        )
