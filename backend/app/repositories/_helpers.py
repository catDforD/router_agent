"""Shared repository helpers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import RepositoryConflictError


def enum_value(value: Any) -> Any:
    """Return raw enum values while leaving non-enum values unchanged."""

    if isinstance(value, Enum):
        return value.value
    return value


def dump_model(model: BaseModel) -> dict[str, Any]:
    """Serialize a Pydantic model into JSON-compatible data."""

    return model.model_dump(mode="json")


def flush_or_raise_conflict(session: Session, message: str) -> None:
    """Flush pending changes and translate integrity errors to repository conflicts."""

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise RepositoryConflictError(message) from exc
