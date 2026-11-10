import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

from app.core.exceptions import RehearsalAttendanceConflict, RehearsalNotFound, RehearsalValidationError
from app.models.rehearsal_qr_session import RehearsalQrSession
from app.repositories.rehearsal_qr_session import RehearsalQrSessionRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.schemas.rehearsal_qr_session import RehearsalQrSessionCreate, RehearsalQrSessionResponse


class RehearsalQrSessionService:
    def __init__(self, repository: RehearsalQrSessionRepository, reads: RehearsalReadRepository,
                 frontend_origin: str):
        self.repository = repository
        self.reads = reads
        self.frontend_origin = frontend_origin

    def create(self, rehearsal_id: int, user_id: int,
               data: RehearsalQrSessionCreate) -> RehearsalQrSessionResponse:
        with self.repository.transaction():
            # The route requires GLOBAL ADMIN; this shared query additionally
            # enforces active membership and ministry and serializes creators.
            rehearsal = self.reads.get_admin_for_update(user_id, rehearsal_id)
            if rehearsal is None:
                raise RehearsalNotFound
            if data.expires_at <= datetime.now(timezone.utc):
                raise RehearsalValidationError("QR session must expire in the future.")
            if self.repository.has_non_revoked(rehearsal.id):
                raise RehearsalAttendanceConflict("A non-revoked QR session already exists.")
            token = secrets.token_urlsafe(32)
            session = RehearsalQrSession(
                rehearsal_id=rehearsal.id, created_by=user_id,
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                opens_at=data.opens_at, expires_at=data.expires_at,
            )
            self.repository.add(session)
            result = RehearsalQrSessionResponse(
                id=session.id, rehearsal_id=session.rehearsal_id,
                check_in_url=f"{self.frontend_origin.rstrip('/')}/check-in?{urlencode({'token': token})}",
                opens_at=data.opens_at, expires_at=data.expires_at,
            )
        return result
