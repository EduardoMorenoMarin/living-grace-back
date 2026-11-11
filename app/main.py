from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.users import router as users_router
from app.api.routes.ministries import router as ministries_router
from app.api.routes.rehearsals import router as rehearsals_router
from app.api.routes.attendance import router as attendance_router
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


@app.middleware("http")
async def password_change_no_store(request: Request, call_next):
    response = await call_next(request)
    if request.method == "POST" and request.url.path.rstrip("/") == "/auth/change-password":
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
