from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RehearsalQrSession(Base):
    __tablename__ = "rehearsal_qr_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > opens_at"),
        Index("uq_rehearsal_qr_sessions_non_revoked", "rehearsal_id", unique=True,
              postgresql_where=text("revoked_at IS NULL"), sqlite_where=text("revoked_at IS NULL")),
        Index("ix_rehearsal_qr_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rehearsal_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rehearsals.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
