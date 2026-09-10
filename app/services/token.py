from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import Settings


class TokenService:
    """Create signed access tokens using the configured JWT policy."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def create_access_token(self, user_id: int) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.settings.JWT_EXPIRE_MINUTES)
        payload = {"sub": str(user_id), "exp": expires_at}
        return jwt.encode(
            payload,
            self.settings.JWT_SECRET_KEY.get_secret_value(),
            algorithm=self.settings.JWT_ALGORITHM,
        )
