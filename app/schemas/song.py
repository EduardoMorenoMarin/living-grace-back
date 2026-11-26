from datetime import datetime
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.membership import DatabaseId


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SongUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    artist: str | None = None
    musical_key: str | None = None
    lyrics: str | None = None
    reference_url: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one editable field must be provided.")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title must not be null.")
        return self


class SongCreate(SongUpdate):
    title: Title


class SongDetails(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    artist: str | None
    musical_key: str | None
    lyrics: str | None
    reference_url: str | None


class SongResponse(SongDetails):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime


class SongPage(BaseModel):
    items: list[SongResponse]
    total: int


class SongAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: DatabaseId
    position: Annotated[int, Field(strict=True, gt=0, le=2147483647)]


class SongAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    songs: Annotated[list[SongAssignment], Field(min_length=1)]


class SongOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_ids: list[DatabaseId]


class AssignedSong(SongDetails):
    song_id: int
    position: int
