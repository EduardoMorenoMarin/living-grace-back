from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import RehearsalNotFound, RehearsalReadError
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.schemas.rehearsal_read import (
    AdminRehearsalDetail, AdminRehearsalItem, AdminRehearsalPage,
    RehearsalInfo, RehearsalPage, RehearsalParticipant,
)


@dataclass(frozen=True)
class RehearsalViewer:
    user_id: int
    is_admin: bool


class RehearsalReadService:
    def __init__(self, repository: RehearsalReadRepository):
        self.repository = repository

    @staticmethod
    def _metadata(row) -> dict:
        return {
            **{key: row[key] for key in (
                "id", "name", "description", "start_at", "end_at", "tolerance_minutes", "created_at", "updated_at",
            )},
            "ministry": {"id": row["ministry_id"], "name": row["ministry_name"]},
            "created_by": {"id": row["creator_id"], "first_name": row["creator_first_name"],
                           "last_name": row["creator_last_name"]},
        }

    def list_visible(
        self, viewer: RehearsalViewer, limit: int, offset: int, ministry_id: int | None,
    ) -> AdminRehearsalPage | RehearsalPage:
        try:
            rows, total = self.repository.list_visible(viewer.user_id, viewer.is_admin, limit, offset, ministry_id)
        except SQLAlchemyError:
            raise RehearsalReadError from None
        if viewer.is_admin:
            return AdminRehearsalPage(items=[
                AdminRehearsalItem(**self._metadata(row), participant_count=row["participant_count"])
                for row in rows
            ], total=total)
        return RehearsalPage(items=[RehearsalInfo(**self._metadata(row)) for row in rows], total=total)

    def get_visible(self, viewer: RehearsalViewer, rehearsal_id: int) -> AdminRehearsalDetail | RehearsalInfo:
        try:
            row = self.repository.get_visible(viewer.user_id, viewer.is_admin, rehearsal_id)
            if row is None:
                raise RehearsalNotFound
            if not viewer.is_admin:
                return RehearsalInfo(**self._metadata(row))
            participants: dict[int, RehearsalParticipant] = {}
            for participant in self.repository.get_participants(rehearsal_id):
                member_id = participant["ministry_member_id"]
                if member_id not in participants:
                    participants[member_id] = RehearsalParticipant(**{
                        key: participant[key] for key in (
                            "ministry_member_id", "user_id", "first_name", "last_name", "arrival_at", "attendance_status",
                        )
                    })
                function = participant["function"]
                if function is not None and function not in participants[member_id].functions:
                    participants[member_id].functions.append(function)
            return AdminRehearsalDetail(**self._metadata(row), participants=list(participants.values()))
        except SQLAlchemyError:
            raise RehearsalReadError from None
