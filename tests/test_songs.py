"""Song HTTP and PostgreSQL contracts. SONGS_TEST_DATABASE_URL must be disposable."""
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
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import SongConflict, SongNotFound, SongOrderInvalid
from app.dependencies.database import get_db
from app.main import app
from app.models import Ministry, MinistryMember, MinistryMemberRole, Rehearsal, RehearsalAttendance, RehearsalSong, Role, Song, User
from app.models.enums import MembershipStatus, RoleScope, RoleStatus, UserStatus
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.repositories.song import SongRepository
from app.schemas.song import SongAssignments, SongOrder
from app.services.song import RehearsalSongService, SongService
from app.services.token import TokenService


class SongContractTests(unittest.TestCase):
    def test_openapi_documents_all_operations_and_errors(self):
        paths = app.openapi()["paths"]
        for path, methods in {
            "/songs": {"get": "200", "post": "201"},
            "/songs/{song_id}": {"get": "200", "patch": "200", "delete": "204"},
            "/rehearsals/{rehearsal_id}/songs": {"get": "200", "post": "201"},
            "/rehearsals/{rehearsal_id}/songs/order": {"put": "200"},
            "/rehearsals/{rehearsal_id}/songs/{song_id}": {"delete": "204"},
        }.items():
            for method, success in methods.items():
                with self.subTest(path=path, method=method):
                    operation = paths[path][method]
                    self.assertEqual(operation["security"], [{"HTTPBearer": []}])
                    self.assertLessEqual({success, "401", "403", "422", "500"}, operation["responses"].keys())
                    self.assertEqual("content" in operation["responses"][success], success != "204")
                    self.assertEqual("requestBody" in operation, method in {"post", "put", "patch"})

    def test_put_cors_preflight(self):
        with TestClient(app) as client:
            response = client.options("/rehearsals/1/songs/order", headers={
                "Origin": settings.FRONTEND_ORIGIN, "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            })
        self.assertEqual(response.status_code, 200)


@unittest.skipUnless(os.environ.get("SONGS_TEST_DATABASE_URL"), "opt-in disposable PostgreSQL song tests")
class PostgreSQLSongTests(unittest.TestCase):
    def setUp(self):
        url = os.environ["SONGS_TEST_DATABASE_URL"]
        schema = f"songs_test_{uuid4().hex}"
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
        db.add_all([Ministry(id=i, name=f"Ministry {i}") for i in (11, 12)])
        db.add_all([
            User(id=i, email=f"user{i}@example.com", password_hash="test-hash", first_name="Test",
                 last_name="User", status=UserStatus.ACTIVE, must_change_password=i == 4)
            for i in (1, 2, 3, 4)
        ])
        db.add_all([
            Role(id=i, name=name, scope=RoleScope.GLOBAL, status=RoleStatus.ACTIVE, is_system_role=True)
            for i, name in ((1, "ADMIN"), (2, "USER"))
        ])
        db.flush()
        db.add_all([
            MinistryMember(id=user, user_id=user, ministry_id=ministry, status=MembershipStatus.ACTIVE)
            for user, ministry in ((1, 11), (2, 11), (3, 12), (4, 11))
        ])
        db.flush()
        db.add_all([
            MinistryMemberRole(id=user, ministry_member_id=user, role_id=role)
            for user, role in ((1, 1), (2, 2), (3, 1), (4, 1))
        ])
        now = datetime.now(timezone.utc)
        db.add_all([
            Rehearsal(id=i, ministry_id=ministry, created_by=1, name=f"Rehearsal {i}",
                      start_at=now, end_at=now + timedelta(hours=1))
            for i, ministry in ((21, 11), (22, 11), (23, 12))
        ])
        db.flush()
        db.add(RehearsalAttendance(id=31, rehearsal_id=21, ministry_member_id=2))
        db.commit()

    def request(self, method, path, status=200, *, user=1, **kwargs):
        token = TokenService(settings).create_access_token(user, "test-hash") if user else None
        response = self.client.request(
            method, path, headers={"Authorization": f"Bearer {token}"} if token else {}, **kwargs,
        )
        self.assertEqual(response.status_code, status, response.text)
        if status == 204:
            self.assertEqual(response.content, b"")
            return None
        return response.json()

    def song(self, title="Amazing Grace", **fields):
        return self.request("POST", "/songs", 201, json={"title": title, **fields})

    def assign(self, *pairs, status=201, rehearsal=21, user=1):
        return self.request("POST", f"/rehearsals/{rehearsal}/songs", status, user=user, json={
            "songs": [{"song_id": song_id, "position": position} for song_id, position in pairs],
        })

    def test_catalog_crud_filtering_and_audit(self):
        first = self.song(" Amazing Grace ", artist="John Newton", musical_key="G", lyrics="Lyrics",
                          reference_url="https://example.com/reference")
        second = self.song("Amazing Grace")
        self.song("Blessed Assurance", artist="Fanny Crosby")
        song_id = first["id"]
        path = f"/songs/{song_id}"
        self.assertEqual(first["title"], "Amazing Grace")
        self.assertEqual(first["created_by"], 1)
        self.assertEqual(self.request("GET", path), first)
        by_artist = self.request("GET", "/songs", params={"search": "NEWTON"})
        self.assertEqual(by_artist, {"items": [first], "total": 1})
        self.assertEqual(self.request("GET", "/songs", params={"search": "%"})["total"], 0)
        page = self.request("GET", "/songs", params={"search": "grace", "limit": 1, "offset": 1})
        self.assertEqual(page, {"items": [second], "total": 2})
        updated = self.request("PATCH", path, json={"lyrics": None, "musical_key": "A"})
        self.assertEqual(updated["artist"], first["artist"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertGreater(updated["updated_at"], first["updated_at"])
        self.assertIsNone(updated["lyrics"])
        self.request("DELETE", path, 204)
        self.request("GET", path, 404)
        self.request("PATCH", path, 404, json={"title": "Unavailable"})
        self.request("DELETE", path, 404)
        self.assertEqual(self.request("GET", "/songs")["total"], 2)
        with self.session() as db:
            self.assertIsNotNone(db.get(Song, song_id).deleted_at)

    def test_invalid_catalog_inputs(self):
        song_id = self.song()["id"]
        for payload in ({}, {"title": None}, {"title": ""}, {"title": " \t "}, {"title": 4},
                        {"title": "Valid", "reference_url": "ftp://example.com"},
                        {"title": "Valid", "created_by": 3}):
            self.request("POST", "/songs", 422, json=payload)
            self.request("PATCH", f"/songs/{song_id}", 422, json=payload)
        self.request("PATCH", f"/songs/{song_id}", 422, json={"updated_at": "2020-01-01"})
        self.request("GET", "/songs/999", 404)

    def test_authentication_authorization_and_rehearsal_scope(self):
        song_id = self.song(lyrics="Shared lyrics", musical_key="C")["id"]
        assigned = self.assign((song_id, 2))
        self.assertEqual(self.request("GET", "/rehearsals/21/songs", user=2), assigned)
        self.assertEqual(assigned[0]["lyrics"], "Shared lyrics")
        self.assertEqual(self.request("GET", "/rehearsals/22/songs"), [])
        self.assertEqual(self.request("GET", "/rehearsals/23/songs", user=3), [])
        for rehearsal in (22, 23, 999):
            self.request("GET", f"/rehearsals/{rehearsal}/songs", 404, user=2)
        self.request("GET", "/rehearsals/21/songs", 404, user=3)
        for method, path, body in (
            ("POST", "/songs", {"title": "New"}), ("GET", "/songs", None),
            ("GET", f"/songs/{song_id}", None), ("PATCH", f"/songs/{song_id}", {"artist": "New"}),
            ("DELETE", f"/songs/{song_id}", None),
            ("POST", "/rehearsals/21/songs", {"songs": [{"song_id": song_id, "position": 1}]}),
            ("PUT", "/rehearsals/21/songs/order", {"song_ids": [song_id]}),
            ("DELETE", f"/rehearsals/21/songs/{song_id}", None),
        ):
            self.request(method, path, 401, user=None, json=body)
            self.request(method, path, 403, user=2, json=body)
            self.request(method, path, 403, user=4, json=body)
            if path.startswith("/rehearsals"):
                self.request(method, path, 404, user=3, json=body)
        self.request("GET", "/rehearsals/21/songs", 401, user=None)
        self.request("GET", "/rehearsals/21/songs", 403, user=4)
        with self.session() as db:
            db.get(MinistryMember, 2).status = MembershipStatus.INACTIVE
            db.commit()
        self.request("GET", "/rehearsals/21/songs", 401, user=2)

    def test_assignment_conflicts_unavailable_songs_and_rollback(self):
        a, b, c, d = [self.song(title)["id"] for title in "ABCD"]
        self.request("DELETE", f"/songs/{d}", 204)
        self.assign((a, 1))
        self.assign((b, 2), (b, 3), status=409)
        self.assign((b, 2), (c, 2), status=409)
        self.assign((b, 2), (d, 3), status=404)
        self.assign((b, 2), (999, 3), status=404)
        self.assign((b, 2), (c, 1), status=409)
        self.assign((b, 2), (a, 3), status=409)
        self.assertEqual([row["song_id"] for row in self.request("GET", "/rehearsals/21/songs")], [a])
        for payload in ({"songs": []}, {"songs": [{"song_id": b, "position": 0}]},
                        {"songs": [{"song_id": b, "position": 2, "assigned_by": 3}]}):
            self.request("POST", "/rehearsals/21/songs", 422, json=payload)
        self.assertEqual([row["song_id"] for row in self.assign((c, 3), (b, 2))], [a, b, c])
        with self.session() as db:
            self.assertEqual(set(db.scalars(select(RehearsalSong.assigned_by))), {1})

    def test_swap_invalid_order_removal_normalization_and_cascade(self):
        a, b, c, d = [self.song(title)["id"] for title in "ABCD"]
        self.assign((a, 1), (b, 2), (c, 8))
        path = "/rehearsals/21/songs"
        for ids in ([a, b], [a, b, b], [a, b, d], [a, b, c, d], []):
            self.request("PUT", f"{path}/order", 422, json={"song_ids": ids})
        updated = self.request("PUT", f"{path}/order", json={"song_ids": [b, a, c]})
        self.assertEqual([(row["song_id"], row["position"]) for row in updated], [(b, 1), (a, 2), (c, 3)])
        self.request("DELETE", f"/songs/{b}", 409)
        self.request("DELETE", f"{path}/{b}", 204)
        self.request("DELETE", f"{path}/{b}", 404)
        self.assertEqual([(row["song_id"], row["position"]) for row in self.request("GET", path)], [(a, 1), (c, 2)])
        self.request("GET", f"/songs/{b}")
        self.assertEqual(self.request("PUT", "/rehearsals/22/songs/order", json={"song_ids": []}), [])
        self.assign((a, 3), rehearsal=22)
        self.request("DELETE", "/rehearsals/22", 204)
        with self.session() as db:
            self.assertFalse(db.scalars(select(RehearsalSong).where(RehearsalSong.rehearsal_id == 22)).all())
        self.request("GET", f"/songs/{a}")
        self.assertEqual(len(self.request("GET", path)), 2)

    def race(self, lock, winner, contender):
        """Observe the contender waiting on a PostgreSQL lock before committing the winner."""
        started = Event()
        state = {}

        def run_contender():
            with self.session() as db:
                state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                return contender(db)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.session() as db:
                lock(db)
                result = pool.submit(run_contender)
                self.assertTrue(started.wait(3))
                deadline = monotonic() + 3
                while not db.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), state):
                    self.assertLess(monotonic(), deadline, "Contender never blocked on PostgreSQL")
                    sleep(0.01)
                winner(db)
            return result.result(timeout=5)

    def test_concurrent_assignment_and_catalog_deletion_in_both_orders(self):
        first, second = [self.song(title)["id"] for title in ("Assign first", "Delete first")]

        def assign(db, song_id):
            return RehearsalSongService(SongRepository(db), RehearsalReadRepository(db)).assign(
                21, 1, SongAssignments(songs=[{"song_id": song_id, "position": 1}]),
            )

        with self.assertRaises(SongConflict):
            self.race(
                lambda db: SongRepository(db).get_active(first, lock=True),
                lambda db: assign(db, first),
                lambda db: SongService(SongRepository(db)).delete(first),
            )
        with self.assertRaises(SongNotFound):
            self.race(
                lambda db: SongRepository(db).get_active(second, lock=True),
                lambda db: SongService(SongRepository(db)).delete(second),
                lambda db: assign(db, second),
            )
        self.assertEqual([row["song_id"] for row in self.request("GET", "/rehearsals/21/songs")], [first])

    def test_remove_serializes_with_reorder(self):
        a, b = [self.song(title)["id"] for title in "AB"]
        self.assign((a, 1), (b, 2))

        def service(db):
            return RehearsalSongService(SongRepository(db), RehearsalReadRepository(db))

        with self.assertRaises(SongOrderInvalid):
            self.race(
                lambda db: RehearsalReadRepository(db).get_admin_for_update(1, 21),
                lambda db: service(db).remove(21, 1, a),
                lambda db: service(db).reorder(21, 1, SongOrder(song_ids=[b, a])),
            )
        self.assertEqual([(row["song_id"], row["position"]) for row in self.request("GET", "/rehearsals/21/songs")],
                         [(b, 1)])

    def test_concurrent_assignments_cannot_claim_the_same_position(self):
        a, b = [self.song(title)["id"] for title in "AB"]

        def assign(db, song_id):
            return RehearsalSongService(SongRepository(db), RehearsalReadRepository(db)).assign(
                21, 1, SongAssignments(songs=[{"song_id": song_id, "position": 1}]),
            )

        with self.assertRaises(SongConflict):
            self.race(
                lambda db: RehearsalReadRepository(db).get_admin_for_update(1, 21),
                lambda db: assign(db, a),
                lambda db: assign(db, b),
            )
        self.assertEqual([row["song_id"] for row in self.request("GET", "/rehearsals/21/songs")], [a])
