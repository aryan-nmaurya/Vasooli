"""Database engine and session dependency.

No create_all() anywhere in app code — schema comes from Alembic only, so local and
deployed schemas cannot silently diverge.
"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,  # Neon/Railway drop idle connections; revalidate before use
    pool_size=5,
    max_overflow=10,
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def check_database() -> tuple[bool, str | None]:
    """Cheap liveness probe. Returns (ok, error_message)."""
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        return True, None
    except Exception as exc:  # noqa: BLE001 - health check reports, never raises
        return False, f"{type(exc).__name__}: {exc}"


def has_active_operator() -> bool:
    """Whether production has at least one independently authenticated human."""
    from sqlmodel import select

    from app.models import OperatorAccount

    with Session(engine) as session:
        return (
            session.exec(
                select(OperatorAccount.id).where(OperatorAccount.is_active.is_(True))  # type: ignore[union-attr]
            ).first()
            is not None
        )
