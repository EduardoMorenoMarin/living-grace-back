from datetime import datetime, timedelta, timezone

import hashlib
import hmac

import jwt

from app.core.config import Settings


class TokenService:
    """Create signed access tokens using the configured JWT policy."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def create_access_token(self, user_id: int, password_hash: str) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.settings.JWT_EXPIRE_MINUTES)
        payload = {"sub": str(user_id), "exp": expires_at,
                   "pwdv": self.credential_version(user_id, password_hash)}
        return jwt.encode(
            payload,
            self.settings.JWT_SECRET_KEY.get_secret_value(),
            algorithm=self.settings.JWT_ALGORITHM,
        )

    def credential_version(self, user_id: int, password_hash: str) -> str:
        # Bind access to the current credential without putting its hash in JWTs.
        return hmac.new(
            self.settings.JWT_SECRET_KEY.get_secret_value().encode(),
            f"credential-version:{user_id}:{password_hash}".encode(), hashlib.sha256,
        ).hexdigest()

    def matches_credentials(self, claim, user_id: int, password_hash: str) -> bool:
        return isinstance(claim, str) and hmac.compare_digest(
            claim.encode(), self.credential_version(user_id, password_hash).encode(),
        )
