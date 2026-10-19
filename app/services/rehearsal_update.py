from datetime import datetime, timezone

from app.core.exceptions import RehearsalAttendanceConflict, RehearsalNotFound, RehearsalValidationError
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.schemas.rehearsal_read import AdminRehearsalDetail
from app.schemas.rehearsal_update import RehearsalUpdate
from app.services.rehearsal_read import RehearsalReadService, RehearsalViewer


class RehearsalUpdateService:
    def __init__(
        self, repository: RehearsalRepository, reads: RehearsalReadRepository,
        reader: RehearsalReadService,
    ):
        self.repository = repository
        self.reads = reads
        self.reader = reader

    def update(self, rehearsal_id: int, user_id: int, data: RehearsalUpdate) -> AdminRehearsalDetail:
        with self.repository.transaction():
            rehearsal = self.reads.get_admin_for_update(user_id, rehearsal_id)
            if rehearsal is None:
                raise RehearsalNotFound
            changes = data.model_dump(exclude_unset=True)
            desired = changes.pop("ministry_member_ids", None)
            start = changes.get("start_at", rehearsal.start_at)
            end = changes.get("end_at", rehearsal.end_at)
            if end <= start:
                raise RehearsalValidationError("end_at must be after start_at.")

            removed = []
            added = []
            if desired is not None:
                desired_ids = set(desired)
                if desired and self.repository.eligible_participant_ids(rehearsal.ministry_id, desired) != desired_ids:
                    raise RehearsalValidationError("Selected participants are not available for this ministry.")
                attendance = self.repository.get_attendance_for_update(rehearsal.id)
                current_ids = {row.ministry_member_id for row in attendance}
                removed = [row for row in attendance if row.ministry_member_id not in desired_ids]
                if any(row.arrival_at is not None or row.attendance_status is not None for row in removed):
                    raise RehearsalAttendanceConflict("Cannot remove participants with recorded arrival or attendance information.")
                added = [member_id for member_id in desired if member_id not in current_ids]

            changed = bool(removed or added)
            for field, value in changes.items():
                if getattr(rehearsal, field) != value:
                    setattr(rehearsal, field, value)
                    changed = True
            if changed:
                rehearsal.updated_at = datetime.now(timezone.utc)
            if removed:
                self.repository.remove_attendance([row.id for row in removed])
            if added:
                self.repository.add_attendance(rehearsal.id, added)
            self.repository.flush()
            result = self.reader.get_visible(RehearsalViewer(user_id, True), rehearsal.id)
        return result
