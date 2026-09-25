"""Ministry registration behavior without an external database.

Route tests exercise the production application. Repository tests use SQLite.
"""

import os
import secrets
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.main import app
from app.dependencies.database import get_db
from app.models.enums import FunctionStatus, MinistryStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.repositories.ministry import MinistryRepository
from app.services.ministry_registration import MinistryRegistrationService


class MinistryRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock(spec=MinistryRepository)
        self.service = MinistryRegistrationService(self.repository)

    def test_maps_ministries_and_includes_only_active_functions(self):
        self.repository.list_active.return_value = [
            Ministry(id=1, name="Alabanza", description="Worship ministry", functions=[
                Function(id=10, name="Guitar", status=FunctionStatus.ACTIVE),
                Function(name="Retired role", status=FunctionStatus.INACTIVE),
                Function(id=11, name="Vocals", status=FunctionStatus.ACTIVE),
            ]),
            Ministry(id=2, name="Hospitality", description=None, functions=[]),
        ]

        result = [item.model_dump() for item in self.service.list_for_registration()]

        self.assertEqual(result, [
            {
                "id": 1, "name": "Alabanza", "description": "Worship ministry",
                "functions": ["Guitar", "Vocals"],
                "function_options": [{"id": 10, "name": "Guitar"}, {"id": 11, "name": "Vocals"}],
            },
            {
                "id": 2, "name": "Hospitality", "description": None,
                "functions": [], "function_options": [],
            },
        ])
        self.repository.list_active.assert_called_once_with()

    def test_ministry_with_only_inactive_functions_is_still_listed(self):
        self.repository.list_active.return_value = [
            Ministry(id=1, name="Hospitality", description=None, functions=[
                Function(name="Retired role", status=FunctionStatus.INACTIVE),
            ]),
        ]
        result = self.service.list_for_registration()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].functions, [])

    def test_empty_repository_returns_empty_list(self):
        self.repository.list_active.return_value = []
        self.assertEqual(self.service.list_for_registration(), [])


class MinistryRepositoryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        # Include functions for eager loading; explicit IDs avoid dialect-specific identities.
        Ministry.__table__.create(engine)
        Function.__table__.create(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.repository = MinistryRepository(self.db)

    def test_excludes_inactive_and_soft_deleted_ministries(self):
        deleted_at = datetime.now(timezone.utc)
        self.db.add_all([
            Ministry(id=1, name="Available", status=MinistryStatus.ACTIVE),
            Ministry(id=2, name="Inactive", status=MinistryStatus.INACTIVE),
            Ministry(id=3, name="Deleted active", status=MinistryStatus.ACTIVE, deleted_at=deleted_at),
            Ministry(id=4, name="Deleted inactive", status=MinistryStatus.INACTIVE, deleted_at=deleted_at),
            Ministry(id=5, name="Also available", status=MinistryStatus.ACTIVE),
        ])
        self.db.flush()
        self.assertEqual({ministry.id for ministry in self.repository.list_active()}, {1, 5})

    def test_empty_table_returns_empty_list(self):
        self.assertEqual(self.repository.list_active(), [])


class MinistryRouteTests(unittest.TestCase):
    def setUp(self):
        application = app
        self.db = Mock(spec=Session)

        def override_db():
            yield self.db

        application.dependency_overrides[get_db] = override_db
        self.addCleanup(application.dependency_overrides.clear)
        self.client = TestClient(application)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_registration_fields_response_uses_real_dependency_and_service(self):
        self.db.scalars.return_value = [
            Ministry(id=7, name="Producción", description=None, functions=[
                Function(id=12, name="Sound", status=FunctionStatus.ACTIVE),
                Function(name="Legacy", status=FunctionStatus.INACTIVE),
            ]),
        ]
        response = self.client.get("/ministries/registration-fields")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            "id": 7, "name": "Producción", "description": None,
            "functions": ["Sound"],
            "function_options": [{"id": 12, "name": "Sound"}],
        }])
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()

    def test_no_available_ministries_returns_empty_json_array(self):
        self.db.scalars.return_value = []
        response = self.client.get("/ministries/registration-fields")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
