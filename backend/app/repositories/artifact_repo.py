"""Artifact metadata persistence repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryConflictError, RepositoryNotFoundError
from app.models.db_models import ArtifactRow
from app.models.router_schema import Artifact, ArtifactRef
from app.repositories._helpers import dump_model, enum_value, flush_or_raise_conflict


class ArtifactRepository:
    """Repository for immutable Router artifact metadata payloads."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_artifact(self, artifact: Artifact) -> Artifact:
        if self.session.get(ArtifactRow, artifact.artifact_id) is not None:
            raise RepositoryConflictError(
                f"artifact already exists: {artifact.artifact_id}"
            )

        row = ArtifactRow(
            id=artifact.artifact_id,
            task_id=artifact.task_id,
            type=enum_value(artifact.type),
            version=artifact.version,
            status=enum_value(artifact.status),
            visibility=enum_value(artifact.visibility),
            storage_provider=enum_value(artifact.storage.provider),
            uri=artifact.storage.uri,
            content_hash=artifact.storage.content_hash,
            summary=artifact.summary,
            artifact_json=dump_model(artifact),
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
        )
        self.session.add(row)
        flush_or_raise_conflict(
            self.session,
            f"artifact already exists: {artifact.artifact_id}",
        )
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact:
        row = self.session.get(ArtifactRow, artifact_id)
        if row is None:
            raise RepositoryNotFoundError(f"artifact not found: {artifact_id}")
        return Artifact.model_validate(row.artifact_json)

    def list_task_artifacts(self, task_id: str) -> list[Artifact]:
        rows = self.session.execute(
            select(ArtifactRow)
            .where(ArtifactRow.task_id == task_id)
            .order_by(ArtifactRow.created_at, ArtifactRow.version, ArtifactRow.id)
        ).scalars()
        return [Artifact.model_validate(row.artifact_json) for row in rows]

    def get_artifact_ref(self, artifact_id: str) -> ArtifactRef:
        artifact = self.get_artifact(artifact_id)
        return ArtifactRef(
            artifact_id=artifact.artifact_id,
            type=artifact.type,
            version=artifact.version,
            uri=artifact.storage.uri,
            summary=artifact.summary,
            content_hash=artifact.storage.content_hash,
        )
