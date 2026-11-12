from app.core.exceptions import RehearsalAttendanceConflict, RehearsalNotFound
from app.repositories.notification import NotificationRepository
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.notification import NotificationService


class RehearsalDeleteService:
    def __init__(self, repository: RehearsalRepository, reads: RehearsalReadRepository,
                 notifications: NotificationService | None = None):
        self.repository = repository
        self.reads = reads
        self.notifications = notifications if notifications is not None else NotificationService(
            NotificationRepository(repository.db),
        )

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
            # Snapshot recipients and flush notifications while assignments still
            # exist. Generic resource_id has no FK to the rehearsal being deleted.
            notification_ids = self.notifications.create_rehearsal_cancellations(
                rehearsal, [row.ministry_member_id for row in attendance],
            )
            # The existing FK cascades only this rehearsal's assignments.
            self.repository.delete(rehearsal)
        self.notifications.publish_after_commit(notification_ids)
