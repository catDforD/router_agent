"""Database engine and session helpers for backend persistence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


@lru_cache
def get_engine_for_url(database_url: str) -> Engine:
    """Return a cached synchronous SQLAlchemy engine for a database URL."""

    return create_engine(database_url, pool_pre_ping=True)


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the configured database engine without opening a connection."""

    app_settings = settings or get_settings()
    return get_engine_for_url(app_settings.database_url)


@lru_cache
def get_session_factory_for_url(database_url: str) -> sessionmaker[Session]:
    """Return a cached session factory for a database URL."""

    return sessionmaker(
        bind=get_engine_for_url(database_url),
        autoflush=False,
        expire_on_commit=False,
    )


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the configured synchronous session factory."""

    app_settings = settings or get_settings()
    return get_session_factory_for_url(app_settings.database_url)


def get_db_session(settings: Settings | None = None) -> Iterator[Session]:
    """Yield a database session for future FastAPI dependency wiring."""

    session_factory = get_session_factory(settings)
    with session_factory() as session:
        yield session


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Provide a transactional session scope for scripts and tests."""

    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
