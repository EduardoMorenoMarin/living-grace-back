from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import EquipmentCondition, EquipmentStatus


equipment_condition = ENUM(EquipmentCondition, name="equipment_condition", create_type=False)


class Equipment(Base):
    __tablename__ = "equipment"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''"),
        CheckConstraint("btrim(category) <> ''"),
        CheckConstraint("deleted_at IS NULL OR status = 'RETIRED'"),
        Index("idx_equipment_created_by", "created_by"),
        Index("idx_equipment_ministry_id", "ministry_id"),
        Index("idx_equipment_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ministry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ministries.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    serial_number: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EquipmentStatus] = mapped_column(
        ENUM(EquipmentStatus, name="equipment_status", create_type=False), server_default=text("'AVAILABLE'"),
    )
    condition: Mapped[EquipmentCondition] = mapped_column(equipment_condition, server_default=text("'GOOD'"))
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
