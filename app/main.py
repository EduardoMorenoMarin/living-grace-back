from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.users import router as users_router
from app.core.config import settings


app = FastAPI(title="Living Grace API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_methods=["POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(users_router)
app.include_router(auth_router)


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
