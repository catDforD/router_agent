"""Read-only artifact API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.errors import (
    ArtifactStoreContentError,
    ArtifactStoreInvalidStorageError,
    ArtifactStoreUnsupportedProviderError,
    RepositoryNotFoundError,
)
from app.services.artifact_store import ArtifactStore, StoredArtifactContent


router = APIRouter(tags=["artifacts"])


def get_request_db_session(request: Request) -> Iterator[Session]:
    yield from get_db_session(request.app.state.settings)


def get_artifact_store(
    request: Request,
    session: Session = Depends(get_request_db_session),
) -> ArtifactStore:
    return ArtifactStore(
        session=session,
        artifact_root=request.app.state.settings.artifact_root,
    )


@router.get("/api/tasks/{task_id}/artifacts")
def list_task_artifacts(
    task_id: str,
    store: ArtifactStore = Depends(get_artifact_store),
) -> dict[str, Any]:
    try:
        artifacts = store.list_task_artifacts(task_id)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "task_id": task_id,
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
    }


@router.get("/api/artifacts/{artifact_id}")
def get_artifact_content(
    artifact_id: str,
    store: ArtifactStore = Depends(get_artifact_store),
) -> dict[str, Any]:
    try:
        stored = store.read_artifact_content(artifact_id)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        ArtifactStoreInvalidStorageError,
        ArtifactStoreUnsupportedProviderError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ArtifactStoreContentError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    content = _decode_utf8_content(stored)
    artifact = stored.artifact
    return {
        "artifact": artifact.model_dump(mode="json"),
        "content": content,
        "content_encoding": "utf-8",
        "mime_type": artifact.storage.mime_type,
        "size_bytes": artifact.storage.size_bytes,
        "content_hash": artifact.storage.content_hash,
    }


def _decode_utf8_content(stored: StoredArtifactContent) -> str:
    try:
        return stored.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415,
            detail=f"artifact content is not UTF-8 text: {stored.artifact.artifact_id}",
        ) from exc
