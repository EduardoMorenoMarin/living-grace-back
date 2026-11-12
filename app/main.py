from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.users import router as users_router
from app.api.routes.ministries import router as ministries_router
from app.api.routes.rehearsals import router as rehearsals_router
from app.api.routes.attendance import router as attendance_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.notification_realtime import router as notification_realtime_router
from app.core.config import settings


app = FastAPI(title="Living Grace API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(ministries_router)
app.include_router(rehearsals_router)
app.include_router(attendance_router)
app.include_router(dashboard_router)
app.include_router(notifications_router)
app.include_router(notification_realtime_router)


@app.middleware("http")
async def authentication_no_store(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path.rstrip("/")
    if request.method == "POST" and (
        path in {"/auth/login", "/auth/change-password"}
        or (path.startswith("/users/") and path.endswith("/reset-password"))
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Default validation errors can echo a plaintext password or the whole body.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]
        },
    )
