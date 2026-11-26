from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Song(Base):
    __tablename__ = "songs"
    __table_args__ = (CheckConstraint("btrim(title) <> ''"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    artist: Mapped[str | None] = mapped_column(Text)
    musical_key: Mapped[str | None] = mapped_column(Text)
    lyrics: Mapped[str | None] = mapped_column(Text)
    reference_url: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
