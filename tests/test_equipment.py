"""Equipment contracts with disposable PostgreSQL schemas and real JWT authorization."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import test_rehearsal_read  # Establish safe application configuration defaults.
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import EquipmentConflict
from app.dependencies.database import get_db
from app.main import app
from app.models import Equipment, EquipmentLoan, Ministry, MinistryMember, MinistryMemberRole, Role, User
from app.models.enums import EquipmentStatus, MembershipStatus, RoleScope, RoleStatus, UserStatus
from app.repositories.equipment import EquipmentRepository
from app.schemas.equipment import LoanCreate, LoanReturn
from app.services.equipment import EquipmentLoanService
from app.services.token import TokenService


OPERATIONS = {
    "/equipment": {"get": "200", "post": "201"},
    "/equipment/{equipment_id}": {"get": "200", "patch": "200", "delete": "204"},
    "/equipment/{equipment_id}/loans": {"get": "200", "post": "201"},
    "/equipment/loans": {"get": "200"},
    "/equipment/loans/me": {"get": "200"},
    "/equipment/loans/{loan_id}/return": {"patch": "200"},
}


class EquipmentContractTests(unittest.TestCase):
    def test_openapi_contract_filters_and_authentication(self):
        paths = app.openapi()["paths"]
        for path, methods in OPERATIONS.items():
            for method, success in methods.items():
                with self.subTest(path=path, method=method):
                    operation = paths[path][method]
                    self.assertEqual(operation["security"], [{"HTTPBearer": []}])
                    self.assertLessEqual({success, "401", "403", "422", "500"}, operation["responses"].keys())
                    self.assertEqual("content" in operation["responses"][success], success != "204")
                    self.assertEqual("requestBody" in operation, method in {"post", "patch"})
        for path, names in (
            ("/equipment", {"ministry_id", "status", "condition", "category", "search", "limit", "offset"}),
            ("/equipment/loans", {"ministry_id", "equipment_id", "ministry_member_id", "active", "overdue", "limit", "offset"}),
            ("/equipment/loans/me", {"active"}),
        ):
            self.assertEqual({param["name"] for param in paths[path]["get"]["parameters"]}, names)
        with TestClient(app) as client:
            for path, methods in OPERATIONS.items():
                url = path.format(equipment_id=1, loan_id=1)
                for method in methods:
                    self.assertEqual(client.request(method, url).status_code, 401)


@unittest.skipUnless(os.environ.get("EQUIPMENT_TEST_DATABASE_URL"), "opt-in disposable PostgreSQL equipment tests")
class PostgreSQLEquipmentTests(unittest.TestCase):
    def setUp(self):
        url = os.environ["EQUIPMENT_TEST_DATABASE_URL"]
        schema = f"equipment_test_{uuid4().hex}"
        bootstrap = create_engine(url, isolation_level="AUTOCOMMIT")
        self.addCleanup(bootstrap.dispose)
        self.assertEqual(bootstrap.dialect.name, "postgresql")
        with bootstrap.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def drop_schema():
            with bootstrap.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

        self.addCleanup(drop_schema)
        self.engine = create_engine(url, connect_args={
            "options": f"-c search_path={schema} -c lock_timeout=5000 -c statement_timeout=8000",
        })
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            enums = {col.type.name: col.type for table in Base.metadata.tables.values()
                     for col in table.columns if isinstance(col.type, ENUM)}
            for enum in enums.values():
                enum.create(connection)
            Base.metadata.create_all(connection)
        with self.session() as db:
            self.seed(db)

        def override_db():
            with self.session() as db:
                yield db

        self.enterContext(patch.dict(app.dependency_overrides, {get_db: override_db}))
        self.client = self.enterContext(TestClient(app))

    def session(self):
        return Session(self.engine, autoflush=False)

    def seed(self, db):
        db.add_all([Ministry(id=i, name=f"Ministry {i}") for i in (11, 12, 13)])
        db.add_all([
            User(id=i, email=f"user{i}@example.com", password_hash="test-hash", first_name=f"User {i}",
                 last_name="Test", status=UserStatus.ACTIVE, must_change_password=i == 6)
            for i in range(1, 7)
        ])
        db.add_all([
            Role(id=i, name=name, scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True)
            for i, name in ((1, "ADMIN"), (2, "USER"))
        ])
        db.flush()
        db.add_all([
            MinistryMember(id=member, user_id=user, ministry_id=ministry,
                           status=MembershipStatus.INACTIVE if member == 7 else MembershipStatus.ACTIVE)
            for member, user, ministry in (
                (1, 1, 11), (2, 2, 11), (3, 3, 12), (4, 4, 11), (5, 5, 12), (6, 6, 11),
                (7, 5, 11), (8, 2, 12),
            )
        ])
        db.flush()
        db.add_all([
            MinistryMemberRole(id=member, ministry_member_id=member, role_id=role)
            for member, role in ((1, 1), (2, 2), (3, 1), (4, 2), (5, 2), (6, 1))
        ])
        db.commit()

    def request(self, method, path, status=200, *, user=1, **kwargs):
        token = TokenService(settings).create_access_token(user, "test-hash")
        response = self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        if status == 204:
            self.assertEqual(response.content, b"")
            return None
        return response.json()

    def equipment(self, name="Acoustic Guitar", *, user=1, ministry=11, **fields):
        return self.request("POST", "/equipment", 201, user=user, json={
            "ministry_id": ministry, "name": name, "category": "Instruments", **fields,
        })

    def lend(self, item, member=2, status=201, *, user=1, **fields):
        return self.request("POST", f"/equipment/{item}/loans", status, user=user, json={
            "ministry_member_id": member, **fields,
        })

    def return_loan(self, loan, condition="GOOD", status=200, *, user=1, **fields):
        return self.request("PATCH", f"/equipment/loans/{loan}/return", status, user=user, json={
            "condition_at_return": condition, **fields,
        })

    def test_inventory_crud_filters_and_soft_delete(self):
        a = self.equipment(" Acoustic Guitar ", brand="Yamaha", model="F310", serial_number="YM%001")
        b = self.equipment("Acoustic Guitar", category="Audio", condition="FAIR")
        self.equipment("Damaged", condition="DAMAGED")
        self.equipment("Other", user=3, ministry=12)
        path = f"/equipment/{a['id']}"
        self.assertEqual(a["name"], "Acoustic Guitar")
        self.assertEqual(a["created_by"], 1)
        self.assertEqual(a["ministry"], {"id": 11, "name": "Ministry 11"})
        self.assertEqual(a["status"], "AVAILABLE")
        detail = self.request("GET", path)
        self.assertEqual(detail, {**a, "active_loan": None})
        for search in ("acou", "yamaha", "f310", "YM%001", "%"):
            match = self.request("GET", "/equipment", params={"search": search})
            self.assertIn(a["id"], [item["id"] for item in match["items"]])
        self.assertEqual(self.request("GET", "/equipment", params={"search": "%"})["total"], 1)
        filters = {"ministry_id": 11, "status": "AVAILABLE", "condition": "FAIR", "category": "Audio"}
        self.assertEqual(self.request("GET", "/equipment", params=filters), {"items": [b], "total": 1})
        page = self.request("GET", "/equipment", params={"limit": 1, "offset": 1})
        self.assertEqual(page, {"items": [b], "total": 3})
        updated = self.request("PATCH", path, json={"description": "New", "brand": None})
        self.assertIsNone(updated["brand"])
        self.assertEqual(updated["model"], "F310")
        self.assertEqual(updated["created_at"], a["created_at"])
        self.assertGreater(updated["updated_at"], a["updated_at"])
        self.request("DELETE", path, 204)
        self.request("GET", path, 404)
        self.request("PATCH", path, 404, json={"name": "Hidden"})
        self.assertEqual(self.request("GET", f"{path}/loans"), [])
        self.assertEqual(self.request("GET", "/equipment")["total"], 2)
        with self.session() as db:
            item = db.get(Equipment, a["id"])
            self.assertEqual(item.status, EquipmentStatus.RETIRED)
            self.assertIsNotNone(item.deleted_at)

    def test_validation_and_terminal_status(self):
        item = self.equipment()["id"]
        path = f"/equipment/{item}"
        for field, value in (("name", ""), ("name", " \t "), ("name", None),
                             ("category", ""), ("category", " \n"), ("category", None),
                             ("brand", "Bad\x00text"), ("created_by", 3)):
            self.request("POST", "/equipment", 422, json={
                "ministry_id": 11, "name": "Valid", "category": "Valid", field: value,
            })
            self.request("PATCH", path, 422, json={field: value})
        for payload in ({}, {"ministry_id": 12}, {"status": None}, {"condition": "BROKEN"}, {"updated_at": "now"}):
            self.request("PATCH", path, 422, json=payload)
        self.request("PATCH", path, 409, json={"status": "BORROWED"})
        self.request("PATCH", path, 409, json={"condition": "DAMAGED", "status": "AVAILABLE"})
        self.assertEqual(self.request("PATCH", path, json={"condition": "DAMAGED"})["status"], "MAINTENANCE")
        self.request("PATCH", path, 409, json={"status": "AVAILABLE"})
        self.lend(item, status=409)
        self.assertEqual(self.request("PATCH", path, json={"condition": "FAIR", "status": "AVAILABLE"})["status"], "AVAILABLE")
        self.request("PATCH", path, json={"status": "RETIRED"})
        self.request("PATCH", path, 409, json={"status": "AVAILABLE"})
        self.lend(item, status=409)
        self.lend(self.equipment(condition="DAMAGED")["id"], status=409)
        maintenance = self.equipment()["id"]
        self.request("PATCH", f"/equipment/{maintenance}", json={"status": "MAINTENANCE"})
        self.lend(maintenance, status=409)

    def test_roles_ministry_scope_and_invalid_borrowers(self):
        item = self.equipment()["id"]
        other = self.equipment(user=3, ministry=12)["id"]
        own_loan = self.lend(item)["id"]
        self.lend(other, member=5, user=3)
        for method, path, body in (
            ("GET", "/equipment", None), ("POST", "/equipment", {"ministry_id": 11, "name": "X", "category": "X"}),
            ("GET", f"/equipment/{item}", None), ("PATCH", f"/equipment/{item}", {"name": "X"}),
            ("DELETE", f"/equipment/{item}", None), ("GET", "/equipment/loans", None),
            ("GET", f"/equipment/{item}/loans", None), ("POST", f"/equipment/{item}/loans", {"ministry_member_id": 2}),
            ("PATCH", f"/equipment/loans/{own_loan}/return", {"condition_at_return": "GOOD"}),
        ):
            self.request(method, path, 403, user=2, json=body)
            self.request(method, path, 403, user=6, json=body)
            if path.startswith(f"/equipment/{item}"):
                self.request(method, path, 404, user=3, json=body)
        self.return_loan(own_loan, status=404, user=3)
        self.request("GET", "/equipment/loans/me", 403, user=6)
        for ministry in (12, 13, 999):
            self.request("POST", "/equipment", 404, json={"ministry_id": ministry, "name": "X", "category": "X"})
        self.assertEqual(self.request("GET", "/equipment/loans", params={"ministry_id": 12})["items"], [])
        self.assertEqual(self.request("GET", "/equipment/loans", params={"equipment_id": other})["total"], 0)
        fresh = self.equipment()["id"]
        for member in (5, 7, 999):
            self.lend(fresh, member=member, status=404)
        self.lend(fresh, status=422, assigned_by=3)
        self.lend(fresh, status=422, expected_return_at="2000-01-01T00:00:00Z")
        self.lend(fresh, status=422, expected_return_at="2050-01-01T00:00:00")

    def test_loan_returns_conditions_and_retired_history(self):
        item = self.equipment(condition="FAIR")["id"]
        path = f"/equipment/{item}"
        expected = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        loan = self.lend(item, expected_return_at=expected, notes="For rehearsals")
        self.assertEqual(loan["assigned_by"], 1)
        self.assertEqual(loan["condition_at_loan"], "FAIR")
        self.assertIsNone(loan["returned_at"])
        detail = self.request("GET", path)
        self.assertEqual(detail["status"], "BORROWED")
        self.assertEqual(detail["active_loan"], loan)
        self.request("PATCH", path, json={"name": "Renamed"})
        self.request("PATCH", path, 409, json={"status": "RETIRED"})
        self.request("PATCH", path, 409, json={"condition": "GOOD"})
        self.request("DELETE", path, 409)
        self.lend(item, status=409)
        self.return_loan(loan["id"], status=422, returned_by=3)
        returned = self.return_loan(loan["id"], "DAMAGED", return_notes="Broken strings")
        self.assertEqual(returned["condition_at_return"], "DAMAGED")
        self.assertEqual(returned["returned_by"], 1)
        self.assertEqual(returned["return_notes"], "Broken strings")
        self.assertIsNotNone(returned["returned_at"])
        self.assertFalse(returned["overdue"])
        self.return_loan(loan["id"], status=409)
        detail = self.request("GET", path)
        self.assertEqual(detail["condition"], "DAMAGED")
        self.assertEqual(detail["status"], "MAINTENANCE")
        self.assertIsNone(detail["active_loan"])
        self.assertEqual(detail["updated_at"], returned["returned_at"])
        self.request("PATCH", path, json={"condition": "GOOD", "status": "AVAILABLE"})
        second = self.lend(item)
        self.return_loan(second["id"], "FAIR")
        self.assertEqual(self.request("GET", path)["status"], "AVAILABLE")
        self.request("PATCH", path, json={"status": "RETIRED"})
        self.assertEqual([row["id"] for row in self.request("GET", f"{path}/loans")], [second["id"], loan["id"]])
        self.request("DELETE", path, 204)
        self.assertEqual(len(self.request("GET", f"{path}/loans")), 2)
        self.return_loan(loan["id"], status=409)

    def test_overdue_filters_and_history_survives_membership_deactivation(self):
        my_item, undated, other_item = [self.equipment(name)["id"] for name in ("Mine", "Undated", "Other")]
        now = datetime.now(timezone.utc)
        with patch("app.services.equipment.utc_now", return_value=now - timedelta(days=2)):
            overdue = self.lend(my_item, expected_return_at=(now - timedelta(days=1)).isoformat())
        undated_loan = self.lend(undated)
        other_loan = self.lend(other_item, member=4)
        path = "/equipment/loans"
        self.assertEqual(self.request("GET", path, params={"overdue": True})["items"][0]["id"], overdue["id"])
        non_overdue = self.request("GET", path, params={"overdue": False, "active": True})
        self.assertEqual({row["id"] for row in non_overdue["items"]}, {undated_loan["id"], other_loan["id"]})
        filtered = self.request("GET", path, params={"ministry_member_id": 2, "limit": 1, "offset": 1})
        self.assertEqual(filtered["total"], 2)
        self.assertTrue(filtered["items"][0]["overdue"])
        self.return_loan(undated_loan["id"])
        self.request("DELETE", f"/equipment/{undated}", 204)
        with self.session() as db:
            member = db.get(MinistryMember, 2)
            member.status = MembershipStatus.INACTIVE
            member.deleted_at = now
            db.commit()
        personal = "/equipment/loans/me"
        self.assertEqual([row["id"] for row in self.request("GET", personal, user=2)], [undated_loan["id"], overdue["id"]])
        open_items = self.request("GET", personal, user=2, params={"active": True})
        self.assertEqual([row["id"] for row in open_items], [overdue["id"]])
        self.assertTrue(open_items[0]["overdue"])
        closed_items = self.request("GET", personal, user=2, params={"active": False})
        self.assertEqual([row["id"] for row in closed_items], [undated_loan["id"]])
        self.assertFalse(closed_items[0]["overdue"])
        self.assertTrue({"borrower", "assigned_by", "returned_by", "notes", "return_notes"}.isdisjoint(open_items[0]))
        self.assertEqual(self.request("GET", personal, user=3), [])

    def test_unique_index_and_transaction_rollback(self):
        item = self.equipment()["id"]
        path = f"/equipment/{item}"
        with patch.object(EquipmentRepository, "loan_detail", side_effect=SQLAlchemyError):
            self.lend(item, status=500)
        self.assertEqual(self.request("GET", path)["status"], "AVAILABLE")
        self.assertEqual(self.request("GET", f"{path}/loans"), [])
        loan = self.lend(item)
        with patch.object(EquipmentRepository, "loan_detail", side_effect=SQLAlchemyError):
            self.return_loan(loan["id"], "DAMAGED", status=500)
        self.assertEqual(self.request("GET", path)["status"], "BORROWED")
        self.assertIsNone(self.request("GET", path)["active_loan"]["returned_at"])
        with self.session() as db:
            # Force an inconsistent status so the DB uniqueness check is reached.
            db.get(Equipment, item).status = EquipmentStatus.AVAILABLE
            db.commit()
        with patch.object(EquipmentRepository, "has_active_loan", return_value=False):
            self.lend(item, member=4, status=409)
        self.assertEqual(self.request("GET", path)["status"], "AVAILABLE")
        self.assertEqual([row["id"] for row in self.request("GET", f"{path}/loans")], [loan["id"]])
        self.return_loan(loan["id"])

    def race(self, equipment_id, winner, contender):
        started = Event()
        state = {}

        def run_contender():
            with self.session() as db:
                state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                return contender(EquipmentLoanService(EquipmentRepository(db)))

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.session() as db:
                repository = EquipmentRepository(db)
                self.assertIsNotNone(repository.lock_equipment(1, equipment_id))
                result = pool.submit(run_contender)
                self.assertTrue(started.wait(3))
                deadline = monotonic() + 3
                while not db.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), state):
                    self.assertLess(monotonic(), deadline, "Contender never blocked on the equipment row")
                    sleep(0.01)
                winner(EquipmentLoanService(repository))
            return result.result(timeout=5)

    def test_concurrent_loans_and_returns_have_one_winner_each(self):
        item = self.equipment()["id"]
        with self.assertRaisesRegex(EquipmentConflict, "unavailable"):
            self.race(
                item, lambda service: service.create(1, item, LoanCreate(ministry_member_id=2)),
                lambda service: service.create(1, item, LoanCreate(ministry_member_id=4)),
            )
        with self.session() as db:
            loans = db.scalars(select(EquipmentLoan)).all()
            self.assertEqual(len(loans), 1)
            loan_id = loans[0].id
        with self.assertRaisesRegex(EquipmentConflict, "already been returned"):
            self.race(
                item, lambda service: service.return_loan(1, loan_id, LoanReturn(condition_at_return="GOOD")),
                lambda service: service.return_loan(1, loan_id, LoanReturn(condition_at_return="DAMAGED")),
            )
        self.assertEqual(self.request("GET", f"/equipment/{item}")["status"], "AVAILABLE")
