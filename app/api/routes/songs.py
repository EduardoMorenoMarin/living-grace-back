from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.exceptions import RehearsalNotFound, SongConflict, SongNotFound, SongOrderInvalid, SongPersistenceError
from app.dependencies.authorization import require_global_admin
from app.dependencies.rehearsal_read import get_rehearsal_viewer
from app.dependencies.song import get_rehearsal_song_service, get_song_service
from app.models.user import User
from app.schemas.song import AssignedSong, SongAssignments, SongCreate, SongOrder, SongPage, SongResponse, SongUpdate
from app.services.rehearsal_read import RehearsalViewer
from app.services.song import RehearsalSongService, SongService


class SongRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RehearsalNotFound:
                raise HTTPException(404, "Rehearsal does not exist.") from None
            except SongNotFound as exc:
                raise HTTPException(404, str(exc)) from None
            except SongConflict as exc:
                raise HTTPException(409, str(exc)) from None
            except SongOrderInvalid as exc:
                raise HTTPException(422, str(exc)) from None
            except SongPersistenceError:
                raise HTTPException(500, "Unable to complete the song operation.") from None

        return handle


class ErrorResponse(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class OrderErrorResponse(BaseModel):
    detail: str | list[ValidationIssue]


errors = {
    401: {"description": "Missing or invalid bearer token, or ineligible account.", "model": ErrorResponse},
    403: {"description": "Required role missing or password change required.", "model": ErrorResponse},
    500: {"description": "Authentication, authorization or database failure.", "model": ErrorResponse},
}


def error(description: str):
    return {"description": description, "model": ErrorResponse}


router = APIRouter(prefix="/songs", tags=["songs"], route_class=SongRoute, responses=errors)
rehearsal_router = APIRouter(
    prefix="/rehearsals/{rehearsal_id}/songs", tags=["rehearsals"], route_class=SongRoute, responses=errors,
)
PathId = Annotated[int, Path(gt=0, le=9223372036854775807)]
Admin = Annotated[User, Depends(require_global_admin)]
Catalog = Annotated[SongService, Depends(get_song_service)]
Assignments = Annotated[RehearsalSongService, Depends(get_rehearsal_song_service)]

SONG_MISSING = error("Song is missing or soft-deleted.")
REHEARSAL_HIDDEN = error("Rehearsal is missing or outside the caller's authorized scope.")


@router.post("", status_code=201, description="Global ADMIN only. The server records the authenticated creator.")
def create_song(data: SongCreate, user: Admin, service: Catalog) -> SongResponse:
    return service.create(user.id, data)


@router.get("", description="Global ADMIN only. Returns active songs ordered by title and ID.")
def list_songs(
    user: Admin,
    service: Catalog,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    search: Annotated[str | None, Query(description="Case-insensitive substring of title or artist.")] = None,
) -> SongPage:
    return service.list_active(limit, offset, search)


@router.get("/{song_id}", description="Global ADMIN only.", responses={404: SONG_MISSING})
def get_song(song_id: PathId, user: Admin, service: Catalog) -> SongResponse:
    return service.get(song_id)


@router.patch("/{song_id}", description="Global ADMIN only. Submit the editable fields to change.",
              responses={404: SONG_MISSING})
def update_song(song_id: PathId, data: SongUpdate, user: Admin, service: Catalog) -> SongResponse:
    return service.update(song_id, data)


@router.delete(
    "/{song_id}", status_code=204, response_class=Response,
    description="Global ADMIN only. Soft-deletes a catalog song.",
    responses={404: SONG_MISSING, 409: error("The song is assigned to a rehearsal.")},
)
def delete_song(song_id: PathId, user: Admin, service: Catalog) -> None:
    service.delete(song_id)


@rehearsal_router.get(
    "", responses={404: REHEARSAL_HIDDEN},
    description="Requires active membership in the rehearsal's ministry. Global USER callers must also be "
                "selected participants. Returns an array ordered by position.",
)
def list_rehearsal_songs(
    rehearsal_id: PathId, viewer: Annotated[RehearsalViewer, Depends(get_rehearsal_viewer)], service: Assignments,
) -> list[AssignedSong]:
    return service.list_assigned(rehearsal_id, viewer)


@rehearsal_router.post(
    "", status_code=201,
    description="Requires global ADMIN and active membership in the rehearsal's ministry. "
                "Assigns the entire batch atomically, records the authenticated assigner, and returns the full setlist.",
    responses={
        404: error("Rehearsal is missing or outside the caller's authorized scope, or a requested song is unavailable."),
        409: error("Duplicate song ID or conflicting position within the request or existing setlist."),
    },
)
def assign_rehearsal_songs(
    rehearsal_id: PathId, data: SongAssignments, user: Admin, service: Assignments,
) -> list[AssignedSong]:
    return service.assign(rehearsal_id, user.id, data)


@rehearsal_router.put(
    "/order",
    description="Requires global ADMIN and active membership in the rehearsal's ministry. "
                "Supply every assigned song exactly once in performance order.",
    responses={
        404: REHEARSAL_HIDDEN,
        409: error("An assignment uniqueness constraint was violated."),
        422: {"description": "Invalid request or the IDs do not exactly match the assigned songs.",
              "model": OrderErrorResponse},
    },
)
def reorder_rehearsal_songs(
    rehearsal_id: PathId, data: SongOrder, user: Admin, service: Assignments,
) -> list[AssignedSong]:
    return service.reorder(rehearsal_id, user.id, data)


@rehearsal_router.delete(
    "/{song_id}", status_code=204, response_class=Response,
    description="Requires global ADMIN and active membership in the rehearsal's ministry. "
                "Removes the association and renumbers remaining positions from 1.",
    responses={
        404: error("Rehearsal is missing or outside the caller's authorized scope, or the song is not assigned."),
        409: error("An assignment uniqueness constraint was violated."),
    },
)
def remove_rehearsal_song(rehearsal_id: PathId, song_id: PathId, user: Admin, service: Assignments) -> None:
    service.remove(rehearsal_id, user.id, song_id)
