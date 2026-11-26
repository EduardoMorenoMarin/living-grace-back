from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.repositories.song import SongRepository
from app.services.song import RehearsalSongService, SongService


def get_song_service(db: Annotated[Session, Depends(get_db)]) -> SongService:
    return SongService(SongRepository(db))


def get_rehearsal_song_service(db: Annotated[Session, Depends(get_db)]) -> RehearsalSongService:
    return RehearsalSongService(SongRepository(db), RehearsalReadRepository(db))
