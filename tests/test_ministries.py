"""Ministry registration behavior without an external database.

Route tests mount the ministry router independently because the production app
does not currently include it. Repository tests use an in-memory SQLite table.
"""

import os
import secrets
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.ministries import router
from app.dependencies.database import get_db
from app.models.enums import FunctionStatus, MinistryStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.repositories.ministry import MinistryRepository
from app.schemas.ministry import FieldDefinition
from app.services.ministry_fields import registry
from app.services.ministry_fields.base import MinistryFieldsProvider
from app.services.ministry_fields.registry import MinistryFieldsRegistry, register_ministry_fields
from app.services.ministry_registration import MinistryRegistrationService


class MinistryRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock(spec=MinistryRepository)
        self.service = MinistryRegistrationService(self.repository, MinistryFieldsRegistry())

    def test_maps_ministries_and_includes_only_active_functions(self):
        self.repository.list_active.return_value = [
            Ministry(id=1, name="Alabanza", description="Worship ministry", functions=[
                Function(name="Guitar", status=FunctionStatus.ACTIVE),
                Function(name="Retired role", status=FunctionStatus.INACTIVE),
                Function(name="Vocals", status=FunctionStatus.ACTIVE),
            ]),
            Ministry(id=2, name="Hospitality", description=None, functions=[]),
        ]

        result = [item.model_dump() for item in self.service.list_for_registration()]

        self.assertEqual(result, [
            {
                "id": 1, "name": "Alabanza", "description": "Worship ministry",
                "functions": ["Guitar", "Vocals"],
                "extra_fields": [{
                    "name": "main_instrument", "label": "Instrumento principal",
                    "type": "text", "required": True,
                }],
            },
            {
                "id": 2, "name": "Hospitality", "description": None,
                "functions": [], "extra_fields": [],
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

    def test_empty_repository_returns_empty_list_without_resolving_fields(self):
        self.repository.list_active.return_value = []
        fields = Mock(spec=MinistryFieldsRegistry)
        service = MinistryRegistrationService(self.repository, fields)
        self.assertEqual(service.list_for_registration(), [])
        fields.get_provider.assert_not_called()


class MinistryFieldsTests(unittest.TestCase):
    def test_builtin_providers_are_discovered(self):
        for name, field_name, label in (
            ("Alabanza", "main_instrument", "Instrumento principal"),
            ("Producción", "technical_area", "Área técnica"),
        ):
            with self.subTest(ministry=name):
                fields = MinistryFieldsRegistry().get_provider(name).provide()
                self.assertEqual([field.model_dump() for field in fields], [{
                    "name": field_name, "label": label, "type": "text", "required": True,
                }])

    def test_unknown_ministry_has_no_extra_fields(self):
        self.assertEqual(MinistryFieldsRegistry().get_provider("Unregistered ministry").provide(), [])

    def test_custom_provider_can_be_registered_without_affecting_other_ministries(self):
        with patch.dict(registry._REGISTRY):
            @register_ministry_fields("Test ministry")
            class TestFieldsProvider(MinistryFieldsProvider):
                def provide(self):
                    return [FieldDefinition(name="availability", label="Availability", type="text")]

            fields_registry = MinistryFieldsRegistry()
            provider = fields_registry.get_provider("Test ministry")
            self.assertIsInstance(provider, TestFieldsProvider)
            self.assertEqual(provider.provide()[0].name, "availability")
            self.assertEqual(fields_registry.get_provider("Unknown ministry").provide(), [])

    def test_provider_results_do_not_share_mutable_fields(self):
        provider = MinistryFieldsRegistry().get_provider("Alabanza")
        fields = provider.provide()
        fields[0].required = False
        fields.clear()
        fresh_fields = provider.provide()
        self.assertEqual(len(fresh_fields), 1)
        self.assertTrue(fresh_fields[0].required)


class MinistryRepositoryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        # Only this table is needed; explicit IDs avoid dialect-specific identities.
        Ministry.__table__.create(engine)
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
        application = FastAPI()
        application.include_router(router)
        self.db = Mock(spec=Session)

        def override_db():
            yield self.db

        application.dependency_overrides[get_db] = override_db
        self.client = TestClient(application)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_registration_fields_response_uses_real_dependency_and_service(self):
        self.db.scalars.return_value = [
            Ministry(id=7, name="Producción", description=None, functions=[
                Function(name="Sound", status=FunctionStatus.ACTIVE),
                Function(name="Legacy", status=FunctionStatus.INACTIVE),
            ]),
        ]
        response = self.client.get("/ministries/registration-fields")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            "id": 7, "name": "Producción", "description": None,
            "functions": ["Sound"],
            "extra_fields": [{
                "name": "technical_area", "label": "Área técnica",
                "type": "text", "required": True,
            }],
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
