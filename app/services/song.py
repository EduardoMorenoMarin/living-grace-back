from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import RehearsalNotFound, SongConflict, SongNotFound, SongOrderInvalid, SongPersistenceError
from app.models.song import Song
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.repositories.song import SongRepository
from app.schemas.song import AssignedSong, SongAssignments, SongCreate, SongOrder, SongPage, SongResponse, SongUpdate
from app.services.rehearsal_read import RehearsalViewer


class SongService:
    def __init__(self, repository: SongRepository):
        self.repository = repository

    def _active_song(self, song_id: int, *, lock: bool = False) -> Song:
        song = self.repository.get_active(song_id, lock=lock)
        if song is None:
            raise SongNotFound("Song does not exist.")
        return song

    def create(self, user_id: int, data: SongCreate) -> SongResponse:
        with self.repository.transaction():
            song = Song(created_by=user_id, **data.model_dump(mode="json"))
            self.repository.add(song)
            return SongResponse.model_validate(song)

    def list_active(self, limit: int, offset: int, search: str | None) -> SongPage:
        try:
            songs, total = self.repository.list_active(limit, offset, search.strip() if search else None)
            return SongPage(items=[SongResponse.model_validate(song) for song in songs], total=total)
        except SQLAlchemyError:
            raise SongPersistenceError from None

    def get(self, song_id: int) -> SongResponse:
        try:
            return SongResponse.model_validate(self._active_song(song_id))
        except SQLAlchemyError:
            raise SongPersistenceError from None

    def update(self, song_id: int, data: SongUpdate) -> SongResponse:
        with self.repository.transaction():
            song = self._active_song(song_id, lock=True)
            for field, value in data.model_dump(mode="json", exclude_unset=True).items():
                setattr(song, field, value)
            song.updated_at = datetime.now(timezone.utc)
            return SongResponse.model_validate(song)

    def delete(self, song_id: int) -> None:
        with self.repository.transaction():
            song = self._active_song(song_id, lock=True)
            if self.repository.is_assigned(song_id):
                raise SongConflict("Cannot delete a song assigned to a rehearsal.")
            song.deleted_at = song.updated_at = datetime.now(timezone.utc)


class RehearsalSongService:
    def __init__(self, repository: SongRepository, rehearsals: RehearsalReadRepository):
        self.repository = repository
        self.rehearsals = rehearsals

    def _lock_rehearsal(self, rehearsal_id: int, user_id: int) -> None:
        if self.rehearsals.get_admin_for_update(user_id, rehearsal_id) is None:
            raise RehearsalNotFound

    def list_assigned(self, rehearsal_id: int, viewer: RehearsalViewer) -> list[AssignedSong]:
        try:
            visible = self.rehearsals.visible_id(viewer.user_id, viewer.is_admin, rehearsal_id)
            rows = self.repository.assignments(rehearsal_id, visible)
        except SQLAlchemyError:
            raise SongPersistenceError from None
        if not rows:
            raise RehearsalNotFound
        return [AssignedSong.model_validate(row) for row in rows if row["song_id"] is not None]

    def assign(self, rehearsal_id: int, user_id: int, data: SongAssignments) -> list[AssignedSong]:
        with self.repository.transaction(assignment=True):
            self._lock_rehearsal(rehearsal_id, user_id)
            song_ids = {song.song_id for song in data.songs}
            positions = {song.position for song in data.songs}
            if len(song_ids) != len(data.songs) or len(positions) != len(data.songs):
                raise SongConflict("Song IDs and positions must be unique within the request.")
            # Catalog deletion takes the same song row lock before checking for assignments.
            if self.repository.lock_active_ids(song_ids) != song_ids:
                raise SongNotFound("One or more songs do not exist.")
            self.repository.add_assignments(rehearsal_id, user_id, data.songs)
            return [AssignedSong.model_validate(row) for row in self.repository.assignments(rehearsal_id)]

    def reorder(self, rehearsal_id: int, user_id: int, data: SongOrder) -> list[AssignedSong]:
        with self.repository.transaction(assignment=True):
            self._lock_rehearsal(rehearsal_id, user_id)
            current_ids = self.repository.assigned_ids(rehearsal_id)
            if len(data.song_ids) != len(current_ids) or set(data.song_ids) != set(current_ids):
                raise SongOrderInvalid("Provide every assigned song exactly once.")
            self.repository.set_order(rehearsal_id, data.song_ids)
            return [AssignedSong.model_validate(row) for row in self.repository.assignments(rehearsal_id)]

    def remove(self, rehearsal_id: int, user_id: int, song_id: int) -> None:
        with self.repository.transaction(assignment=True):
            self._lock_rehearsal(rehearsal_id, user_id)
            if not self.repository.remove_assignment(rehearsal_id, song_id):
                raise SongNotFound("Song is not assigned to this rehearsal.")
            self.repository.set_order(rehearsal_id, self.repository.assigned_ids(rehearsal_id))
