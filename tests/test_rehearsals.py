"""BEFORE-LSP contract: real persistence, JWT authentication, and substitution."""

import os
import secrets
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from uuid import uuid4

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")

import jwt
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, MetaData, create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, engine as postgres_engine
from app.dependencies.database import get_db
from app.dependencies.rehearsals import get_rehearsal_creator
from app.main import app
from app.models.ministry import Ministry
from app.models.rehearsal import Rehearsal
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.rehearsal import RehearsalRepository
from app.services.rehearsal_creation import RehearsalCreator, RestrictedRehearsalCreator
from app.services.token import TokenService


class RehearsalContract:
    def prepare(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        with self.session_factory() as db:
            unique = uuid4().hex
            user = User(email=f"rehearsal-{unique}@example.com", username=unique,
                        password_hash="unused", first_name="Test", last_name="Creator")
            ministry = Ministry(name="Rehearsal test")
            db.add_all([user, ministry])
            db.flush()
            self.user_id, self.ministry_id = user.id, ministry.id
            db.commit()

        def override_db():
            with self.session_factory() as db:
                yield db

        stack.enter_context(patch.dict(app.dependency_overrides, {get_db: override_db}))
        self.client = stack.enter_context(TestClient(app))
        self.headers = {"Authorization": f"Bearer {TokenService(settings).create_access_token(self.user_id)}"}
        self.payload = {
            "ministry_id": self.ministry_id, "name": "Worship Team Rehearsal",
            "start_at": "2026-10-03T19:00:00-05:00", "end_at": "2026-10-03T21:00:00-05:00",
        }

    def post(self, **changes):
        return self.client.post("/rehearsals", headers=self.headers, json={**self.payload, **changes})

    def rows(self):
        with self.session_factory() as db:
            return list(db.scalars(select(Rehearsal).where(Rehearsal.ministry_id == self.ministry_id)))

    def test_success_persists_authenticated_creator_and_server_fields(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["created_by"], self.user_id)
        self.assertEqual(body["ministry_id"], self.ministry_id)
        self.assertEqual(body["name"], self.payload["name"])
        self.assertIsNone(body["description"])
        self.assertEqual(body["tolerance_minutes"], 0)
        for field in ("id", "created_at", "updated_at"):
            self.assertTrue(body[field])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, body["id"])
        self.assertEqual(rows[0].created_by, self.user_id)

    def test_nonexistent_ministry(self):
        response = self.post(ministry_id=9223372036854775807)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Ministry does not exist."})
        self.assertEqual(self.rows(), [])

    def test_unauthenticated(self):
        response = self.client.post("/rehearsals", json=self.payload)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.rows(), [])

    def test_invalid_expired_and_unknown_user_tokens(self):
        key = settings.JWT_SECRET_KEY.get_secret_value()
        algorithm = settings.JWT_ALGORITHM
        tokens = [
            "invalid",
            jwt.encode({"sub": str(self.user_id), "exp": 1}, key, algorithm=algorithm),
            jwt.encode({"sub": str(self.user_id)}, key, algorithm=algorithm),
            jwt.encode({"sub": "not-an-id", "exp": 9999999999}, key, algorithm=algorithm),
            TokenService(settings).create_access_token(9223372036854775807),
            jwt.encode({"sub": str(self.user_id), "exp": 9999999999}, secrets.token_urlsafe(48), algorithm=algorithm),
        ]
        for token in tokens:
            with self.subTest(token=tokens.index(token)):
                response = self.client.post("/rehearsals", json=self.payload,
                                            headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.rows(), [])

    def test_invalid_request_and_server_owned_fields(self):
        invalid = [
            {"ministry_id": 0}, {"ministry_id": "1"}, {"ministry_id": True},
            {"name": "  "}, {"start_at": "not-a-date"}, {"start_at": "2026-10-03T19:00:00"},
            {"end_at": self.payload["start_at"]}, {"tolerance_minutes": -1},
            {"id": 99}, {"created_by": 99}, {"created_at": "2026-01-01T00:00:00Z"},
            {"updated_at": "2026-01-01T00:00:00Z"}, {"location": "Auditorium"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(**changes).status_code, 422)
        for field in ("ministry_id", "name", "start_at", "end_at"):
            payload = {key: value for key, value in self.payload.items() if key != field}
            self.assertEqual(self.client.post("/rehearsals", headers=self.headers, json=payload).status_code, 422)
        self.assertEqual(self.rows(), [])

    def test_commit_failure_rolls_back_and_allows_retry(self):
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("private details")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to create rehearsal."})
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.post().status_code, 201)

    def test_foreign_key_failure_rolls_back(self):
        with patch.object(MembershipRepository, "get_ministry", return_value=Ministry(id=9223372036854775807)):
            response = self.post(ministry_id=9223372036854775807)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to create rehearsal."})
        self.assertEqual(self.rows(), [])

    def test_lsp_substitution_rejects_optional_description(self):
        # The same client and valid request succeed with the declared base type.
        self.assertEqual(self.post().status_code, 201)

        def restricted(db: Session = Depends(get_db)) -> RehearsalCreator:
            return RestrictedRehearsalCreator(RehearsalRepository(db), MembershipRepository(db))

        with patch.dict(app.dependency_overrides, {get_rehearsal_creator: restricted}):
            for description in (None, "", "   "):
                response = self.post(description=description)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {"detail": "Restricted creator requires a description."})
            self.assertEqual(len(self.rows()), 1)
            self.assertEqual(self.post(description="Weekly rehearsal").status_code, 201)
        self.assertEqual(len(self.rows()), 2)


class SQLiteRehearsalTests(RehearsalContract, unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            copy = table.to_metadata(metadata)
            for column in copy.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        metadata.create_all(engine)
        self.session_factory = lambda: Session(engine, autoflush=False)
        self.prepare()


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "opt-in PostgreSQL tests")
class PostgreSQLRehearsalTests(RehearsalContract, unittest.TestCase):
    def setUp(self):
        connection = postgres_engine.connect()
        self.addCleanup(connection.close)
        transaction = connection.begin()
        self.addCleanup(transaction.rollback)
        self.session_factory = lambda: Session(bind=connection, autoflush=False, join_transaction_mode="create_savepoint")
        self.prepare()


if __name__ == "__main__":
    unittest.main()
