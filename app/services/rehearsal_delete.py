from app.core.exceptions import RehearsalAttendanceConflict, RehearsalNotFound
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository


class RehearsalDeleteService:
    def __init__(self, repository: RehearsalRepository, reads: RehearsalReadRepository):
        self.repository = repository
        self.reads = reads

    def delete(self, rehearsal_id: int, user_id: int) -> None:
        with self.repository.transaction():
            rehearsal = self.reads.get_admin_for_update(user_id, rehearsal_id)
            if rehearsal is None:
                raise RehearsalNotFound
            # A parent lock blocks new FK assignments, but not attendance-only
            # UPDATEs. Lock and refresh every child before checking recorded data.
            attendance = self.repository.get_attendance_for_update(rehearsal.id)
            if any(row.arrival_at is not None or row.attendance_status is not None for row in attendance):
                raise RehearsalAttendanceConflict("Cannot delete a rehearsal with recorded attendance.")
            # The existing FK cascades only this rehearsal's assignments.
            self.repository.delete(rehearsal)
