from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Provide one session per dependency invocation and always close it."""
    with SessionLocal() as session:
        yield session
