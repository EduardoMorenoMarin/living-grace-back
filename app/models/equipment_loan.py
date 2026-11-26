from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import EquipmentCondition
from app.models.equipment import equipment_condition


class EquipmentLoan(Base):
    __tablename__ = "equipment_loans"
    __table_args__ = (
        CheckConstraint("expected_return_at IS NULL OR expected_return_at > borrowed_at"),
        CheckConstraint("returned_at IS NULL OR returned_at >= borrowed_at"),
        CheckConstraint(
            "(returned_at IS NULL AND returned_by IS NULL AND condition_at_return IS NULL) OR "
            "(returned_at IS NOT NULL AND returned_by IS NOT NULL AND condition_at_return IS NOT NULL)",
        ),
        Index("idx_equipment_loans_assigned_by", "assigned_by"),
        Index("idx_equipment_loans_equipment_id", "equipment_id"),
        Index("idx_equipment_loans_member_id", "ministry_member_id"),
        Index("idx_equipment_loans_returned_by", "returned_by"),
        Index("uq_equipment_active_loan", "equipment_id", unique=True, postgresql_where=text("returned_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    equipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("equipment.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    ministry_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ministry_members.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    assigned_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    borrowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expected_return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
    )
    condition_at_loan: Mapped[EquipmentCondition] = mapped_column(equipment_condition)
    condition_at_return: Mapped[EquipmentCondition | None] = mapped_column(equipment_condition)
    notes: Mapped[str | None] = mapped_column(Text)
    return_notes: Mapped[str | None] = mapped_column(Text)
