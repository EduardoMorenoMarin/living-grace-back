from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy import any_, bindparam, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.exceptions import SongConflict, SongPersistenceError
from app.models.rehearsal_song import RehearsalSong
from app.models.song import Song
from app.schemas.song import SongAssignment


class SongRepository:
    def __init__(self, db: Session):
        self.db = db

    @contextmanager
    def transaction(self, *, assignment: bool = False) -> Iterator[None]:
        try:
            yield
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if assignment and getattr(exc.orig, "sqlstate", None) == "23505":
                raise SongConflict("A song or position is already assigned to this rehearsal.") from None
            raise SongPersistenceError from None
        except SQLAlchemyError:
            self.db.rollback()
            raise SongPersistenceError from None
        except Exception:
            self.db.rollback()
            raise

    def add(self, song: Song) -> None:
        self.db.add(song)
        self.db.flush()

    def list_active(self, limit: int, offset: int, search: str | None) -> tuple[Sequence[Song], int]:
        statement = select(Song).where(Song.deleted_at.is_(None))
        if search:
            statement = statement.where(or_(
                Song.title.icontains(search, autoescape=True),
                Song.artist.icontains(search, autoescape=True),
            ))
        total = self.db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.db.scalars(statement.order_by(Song.title, Song.id).limit(limit).offset(offset)).all()
        return rows, total or 0

    def get_active(self, song_id: int, *, lock: bool = False) -> Song | None:
        statement = select(Song).where(Song.id == song_id, Song.deleted_at.is_(None))
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.db.scalar(statement)

    def lock_active_ids(self, song_ids: set[int]) -> set[int]:
        ids = bindparam("song_ids", list(song_ids), type_=ARRAY(BIGINT))
        return set(self.db.scalars(
            select(Song.id).where(Song.id == any_(ids), Song.deleted_at.is_(None))
            .order_by(Song.id).with_for_update(),
        ))

    def is_assigned(self, song_id: int) -> bool:
        return self.db.scalar(select(select(RehearsalSong.id).where(RehearsalSong.song_id == song_id).exists()))

    def assignments(self, rehearsal_id: int, visible_id: Select | None = None) -> Sequence[RowMapping]:
        statement = select(
            Song.id.label("song_id"), Song.title, Song.artist, Song.musical_key,
            Song.lyrics, Song.reference_url, RehearsalSong.position,
        )
        if visible_id is not None:
            visible = visible_id.subquery()
            statement = statement.select_from(visible).outerjoin(
                RehearsalSong, RehearsalSong.rehearsal_id == visible.c.id,
            ).outerjoin(Song, Song.id == RehearsalSong.song_id)
        else:
            statement = statement.join(RehearsalSong, RehearsalSong.song_id == Song.id).where(
                RehearsalSong.rehearsal_id == rehearsal_id,
            )
        return self.db.execute(statement.order_by(RehearsalSong.position)).mappings().all()

    def assigned_ids(self, rehearsal_id: int) -> Sequence[int]:
        return self.db.scalars(
            select(RehearsalSong.song_id).where(RehearsalSong.rehearsal_id == rehearsal_id)
            .order_by(RehearsalSong.position),
        ).all()

    def add_assignments(self, rehearsal_id: int, user_id: int, songs: list[SongAssignment]) -> None:
        self.db.add_all([
            RehearsalSong(rehearsal_id=rehearsal_id, song_id=song.song_id, position=song.position, assigned_by=user_id)
            for song in songs
        ])
        self.db.flush()

    def remove_assignment(self, rehearsal_id: int, song_id: int) -> bool:
        result = self.db.execute(delete(RehearsalSong).where(
            RehearsalSong.rehearsal_id == rehearsal_id, RehearsalSong.song_id == song_id,
        ))
        return bool(result.rowcount)

    def set_order(self, rehearsal_id: int, song_ids: Sequence[int]) -> None:
        # A DEFERRABLE INITIALLY IMMEDIATE unique constraint is checked at the
        # end of this PostgreSQL statement, so swaps need no temporary positions.
        statement = text("""
            UPDATE rehearsal_songs AS assignment
            SET position = ordering.position
            FROM unnest(CAST(:song_ids AS bigint[])) WITH ORDINALITY AS ordering(song_id, position)
            WHERE assignment.rehearsal_id = :rehearsal_id
              AND assignment.song_id = ordering.song_id
              AND assignment.position <> ordering.position
        """)
        self.db.execute(statement, {"rehearsal_id": rehearsal_id, "song_ids": song_ids})
