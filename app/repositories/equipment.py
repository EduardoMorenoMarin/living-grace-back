from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.selectable import ScalarSelect

from app.core.exceptions import EquipmentConflict, EquipmentPersistenceError
from app.models.equipment import Equipment
from app.models.equipment_loan import EquipmentLoan
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.user import User
from app.repositories.account_eligibility import eligible_ministry_member_ids
from app.repositories.authorization import AuthorizationRepository
from app.schemas.equipment import InventoryFilters, LoanFilters


class EquipmentRepository:
    def __init__(self, db: Session):
        self.db = db

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_equipment_active_loan":
                raise EquipmentConflict("Equipment already has an active loan.") from None
            raise EquipmentPersistenceError("Unable to save equipment or loan.") from None
        except SQLAlchemyError:
            self.db.rollback()
            raise EquipmentPersistenceError("Unable to save equipment or loan.") from None
        except Exception:
            self.db.rollback()
            raise

    def get_managed_ministry(self, user_id: int, ministry_id: int) -> Ministry | None:
        return self.db.scalar(
            select(Ministry).where(
                Ministry.id == ministry_id, Ministry.id.in_(AuthorizationRepository.managed_ministries(user_id)),
            ),
        )

    def list_inventory(self, user_id: int, filters: InventoryFilters) -> tuple[Sequence[Row], int]:
        statement = select(Equipment, Ministry).join(Ministry, Equipment.ministry_id == Ministry.id).where(
            Equipment.deleted_at.is_(None),
            Equipment.ministry_id.in_(AuthorizationRepository.managed_ministries(user_id)),
        )
        for column in (Equipment.ministry_id, Equipment.status, Equipment.condition, Equipment.category):
            value = getattr(filters, column.key)
            if value is not None:
                statement = statement.where(column == value)
        search = filters.search.strip() if filters.search else ""
        if search:
            statement = statement.where(or_(*(
                column.icontains(search, autoescape=True)
                for column in (Equipment.name, Equipment.brand, Equipment.model, Equipment.serial_number)
            )))
        return self._page(statement.order_by(Equipment.name, Equipment.id), filters.limit, filters.offset)

    def lock_equipment(
        self, user_id: int, equipment_id: int | ScalarSelect, *, include_deleted: bool = False,
    ) -> Row | None:
        statement = select(Equipment, Ministry).join(Ministry, Equipment.ministry_id == Ministry.id).where(
            Equipment.id == equipment_id,
            Equipment.ministry_id.in_(AuthorizationRepository.managed_ministries(user_id)),
        )
        if not include_deleted:
            statement = statement.where(Equipment.deleted_at.is_(None))
        return self.db.execute(
            statement.with_for_update(of=Equipment).execution_options(populate_existing=True),
        ).one_or_none()

    def equipment_id_for_loan(self, loan_id: int):
        return select(EquipmentLoan.equipment_id).where(EquipmentLoan.id == loan_id).scalar_subquery()

    def eligible_borrower(self, ministry_id: int, member_id: int) -> bool:
        statement = eligible_ministry_member_ids(ministry_id).where(MinistryMember.id == member_id)
        return self.db.scalar(statement.with_for_update(read=True)) is not None

    def has_active_loan(self, equipment_id: int) -> bool:
        return bool(self.db.scalar(select(select(EquipmentLoan.id).where(
            EquipmentLoan.equipment_id == equipment_id, EquipmentLoan.returned_at.is_(None),
        ).exists())))

    def loan_for_update(self, equipment_id: int, loan_id: int) -> EquipmentLoan | None:
        return self.db.scalar(
            select(EquipmentLoan).where(EquipmentLoan.id == loan_id, EquipmentLoan.equipment_id == equipment_id)
            .with_for_update().execution_options(populate_existing=True),
        )

    def add(self, item: Equipment | EquipmentLoan) -> None:
        self.db.add(item)
        self.flush()

    def flush(self) -> None:
        self.db.flush()

    @staticmethod
    def _overdue(now: datetime):
        return and_(
            EquipmentLoan.returned_at.is_(None), EquipmentLoan.expected_return_at.is_not(None),
            EquipmentLoan.expected_return_at < now,
        )

    def _loan_rows(self, now: datetime, *, include_empty: bool = False, active_only: bool = False):
        loan = EquipmentLoan.equipment_id == Equipment.id
        if active_only:
            loan = and_(loan, EquipmentLoan.returned_at.is_(None))
        return select(
            EquipmentLoan, Equipment, Ministry, User.first_name, User.last_name, self._overdue(now).label("overdue"),
        ).select_from(Equipment).join(Ministry, Equipment.ministry_id == Ministry.id).join(
            EquipmentLoan, loan, isouter=include_empty,
        ).join(MinistryMember, EquipmentLoan.ministry_member_id == MinistryMember.id, isouter=include_empty).join(
            User, MinistryMember.user_id == User.id, isouter=include_empty,
        )

    def loan_detail(self, loan_id: int, now: datetime) -> Row:
        return self.db.execute(self._loan_rows(now).where(EquipmentLoan.id == loan_id)).one()

    def equipment_detail(self, user_id: int, equipment_id: int, now: datetime) -> Row | None:
        return self.db.execute(self._loan_rows(now, include_empty=True, active_only=True).where(
            Equipment.id == equipment_id, Equipment.deleted_at.is_(None),
            Equipment.ministry_id.in_(AuthorizationRepository.managed_ministries(user_id)),
        )).one_or_none()

    def equipment_history(self, user_id: int, equipment_id: int, now: datetime) -> Sequence[Row]:
        return self._ordered_loans(self._loan_rows(now, include_empty=True).where(
            Equipment.id == equipment_id,
            Equipment.ministry_id.in_(AuthorizationRepository.managed_ministries(user_id)),
        ))

    def own_history(self, user_id: int, active: bool | None, now: datetime) -> Sequence[Row]:
        statement = self._loan_rows(now).where(MinistryMember.user_id == user_id)
        return self._ordered_loans(self._filter_active(statement, active))

    def list_loans(self, user_id: int, filters: LoanFilters, now: datetime) -> tuple[Sequence[Row], int]:
        statement = self._loan_rows(now).where(
            Equipment.ministry_id.in_(AuthorizationRepository.managed_ministries(user_id)),
        )
        for column in (Equipment.ministry_id, EquipmentLoan.equipment_id, EquipmentLoan.ministry_member_id):
            value = getattr(filters, column.key)
            if value is not None:
                statement = statement.where(column == value)
        statement = self._filter_active(statement, filters.active)
        if filters.overdue is not None:
            overdue = self._overdue(now)
            statement = statement.where(overdue if filters.overdue else ~overdue)
        return self._page(self._loan_order(statement), filters.limit, filters.offset)

    @staticmethod
    def _filter_active(statement: Select, active: bool | None) -> Select:
        if active is None:
            return statement
        return statement.where(EquipmentLoan.returned_at.is_(None) if active else EquipmentLoan.returned_at.is_not(None))

    @staticmethod
    def _loan_order(statement: Select) -> Select:
        return statement.order_by(EquipmentLoan.borrowed_at.desc(), EquipmentLoan.id.desc())

    def _ordered_loans(self, statement: Select) -> Sequence[Row]:
        return self.db.execute(self._loan_order(statement)).all()

    def _page(self, statement: Select, limit: int, offset: int) -> tuple[Sequence[Row], int]:
        total = self.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        return self.db.execute(statement.limit(limit).offset(offset)).all(), total or 0
