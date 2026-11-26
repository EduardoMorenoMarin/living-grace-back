from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RehearsalSong(Base):
    __tablename__ = "rehearsal_songs"
    __table_args__ = (
        CheckConstraint("position > 0"),
        UniqueConstraint("rehearsal_id", "song_id"),
        UniqueConstraint("rehearsal_id", "position", deferrable=True, initially="IMMEDIATE"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rehearsal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rehearsals.id", onupdate="CASCADE", ondelete="CASCADE"),
    )
    song_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("songs.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    position: Mapped[int] = mapped_column(Integer)
    assigned_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
