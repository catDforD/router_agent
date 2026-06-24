"""Local artifact content store service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.core.errors import (
    ArtifactStoreConflictError,
    ArtifactStoreContentError,
    ArtifactStoreInvalidStorageError,
    ArtifactStoreUnsupportedProviderError,
)
from app.models.router_schema import (
    Artifact,
    ArtifactCreator,
    ArtifactCreatorType,
    DEFAULT_SCHEMA_VERSION,
    ArtifactMetadata,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStorage,
    ArtifactStorageProvider,
    ArtifactType,
    ArtifactVisibility,
    TaskStatus,
)
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository


LOCAL_URI_PREFIX = "local://artifacts/"
TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}

ARTIFACT_POINTER_FIELD_BY_TYPE: dict[str, str] = {
    ArtifactType.RAW_USER_REQUEST.value: "raw_user_request",
    ArtifactType.REQUIREMENTS_IR.value: "requirements_ir",
    ArtifactType.PLC_CODE.value: "current_code",
    ArtifactType.IO_CONTRACT.value: "current_io_contract",
    ArtifactType.TEST_CASES.value: "latest_test_cases",
    ArtifactType.TEST_REPORT.value: "latest_test_report",
    ArtifactType.FAILING_TRACE.value: "latest_failing_trace",
    ArtifactType.FORMAL_PROPERTIES.value: "latest_formal_properties",
    ArtifactType.FORMAL_REPORT.value: "latest_formal_report",
    ArtifactType.COUNTEREXAMPLE.value: "latest_counterexample",
    ArtifactType.PATCH.value: "latest_patch",
    ArtifactType.REPAIR_SUMMARY.value: "latest_repair_summary",
    ArtifactType.GATE_REPORT.value: "latest_gate_report",
    ArtifactType.FINAL_REPORT.value: "final_report",
}


@dataclass(frozen=True)
class ArtifactContentWrite:
    """Request for creating local artifact content and metadata."""

    task_id: str
    artifact_type: ArtifactType | str
    version: int
    name: str
    content: bytes | bytearray | memoryview | str | JsonValue
    summary: str
    visibility: ArtifactVisibility | str = ArtifactVisibility.USER
    created_by: ArtifactCreator | dict[str, Any] | None = None
    metadata: ArtifactMetadata | dict[str, Any] | None = None
    display_name: str | None = None
    parent_artifact_ids: tuple[str, ...] = ()
    derived_from_worker_job_id: str | None = None
    derived_from_artifact_ids: tuple[str, ...] | None = None
    mime_type: str | None = None
    artifact_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class StoredArtifactContent:
    """Artifact metadata and bytes loaded from local storage."""

    artifact: Artifact
    content: bytes


@dataclass(frozen=True)
class ArtifactWriteResult:
    """Result of a local artifact content write."""

    artifact: Artifact
    content_path: Path


class ArtifactStore:
    """Filesystem-backed artifact content store plus metadata persistence."""

    def __init__(self, session: Session, artifact_root: Path) -> None:
        self.session = session
        self.artifact_root = artifact_root
        self.artifact_repository = ArtifactRepository(session)
        self.task_repository = TaskRepository(session)

    def write_artifact_content(self, request: ArtifactContentWrite) -> ArtifactWriteResult:
        self.task_repository.get_task(request.task_id)
        artifact_type = _artifact_type(request.artifact_type)
        artifact_id = request.artifact_id or f"artifact-{uuid4().hex}"
        content = _normalize_content(request.content)
        content_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
        safe_name = _safe_filename(request.name)
        mime_type = request.mime_type or mimetypes.guess_type(safe_name)[0]
        relative_path = self._build_relative_path(
            task_id=request.task_id,
            artifact_type=artifact_type,
            version=request.version,
            artifact_id=artifact_id,
            safe_name=safe_name,
        )
        content_path = self._resolve_local_path(relative_path)
        self._write_immutable_file(content_path, content)

        now = request.created_at or datetime.now(UTC)
        artifact = Artifact(
            schema_version=DEFAULT_SCHEMA_VERSION,
            artifact_id=artifact_id,
            task_id=request.task_id,
            type=artifact_type,
            version=request.version,
            name=request.name,
            display_name=request.display_name,
            status=ArtifactStatus.AVAILABLE,
            visibility=request.visibility,
            storage=ArtifactStorage(
                provider=ArtifactStorageProvider.LOCAL,
                uri=_local_uri(relative_path),
                path=relative_path.as_posix(),
                mime_type=mime_type,
                size_bytes=len(content),
                content_hash=content_hash,
            ),
            summary=request.summary,
            parent_artifact_ids=list(request.parent_artifact_ids),
            derived_from_worker_job_id=request.derived_from_worker_job_id,
            derived_from_artifact_ids=(
                list(request.derived_from_artifact_ids)
                if request.derived_from_artifact_ids is not None
                else None
            ),
            created_by=_artifact_creator(request.created_by),
            created_at=now,
            updated_at=now,
            metadata=_artifact_metadata(request.metadata),
            inline_content=None,
        )

        try:
            stored = self.create_artifact_record(artifact)
        except Exception:
            content_path.unlink(missing_ok=True)
            raise

        return ArtifactWriteResult(artifact=stored, content_path=content_path)

    def read_artifact_content(self, artifact_id: str) -> StoredArtifactContent:
        artifact = self.artifact_repository.get_artifact(artifact_id)
        content_path = self._content_path_for_artifact(artifact)
        try:
            content = content_path.read_bytes()
        except OSError as exc:
            raise ArtifactStoreContentError(
                f"artifact content cannot be read: {artifact_id}"
            ) from exc
        return StoredArtifactContent(artifact=artifact, content=content)

    def create_artifact_record(
        self,
        artifact: Artifact,
        *,
        update_task_state: bool = True,
    ) -> Artifact:
        stored = self.artifact_repository.create_artifact(artifact)
        if update_task_state:
            self._update_task_artifacts(stored)
        return stored

    def get_artifact_ref(self, artifact_id: str) -> ArtifactRef:
        return self.artifact_repository.get_artifact_ref(artifact_id)

    def list_task_artifacts(self, task_id: str) -> list[Artifact]:
        self.task_repository.get_task(task_id)
        return self.artifact_repository.list_task_artifacts(task_id)

    def _build_relative_path(
        self,
        *,
        task_id: str,
        artifact_type: ArtifactType,
        version: int,
        artifact_id: str,
        safe_name: str,
    ) -> Path:
        return (
            Path(_safe_path_segment(task_id))
            / _safe_path_segment(artifact_type.value)
            / f"v{version}"
            / f"{_safe_path_segment(artifact_id)}__{safe_name}"
        )

    def _write_immutable_file(self, target_path: Path, content: bytes) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            raise ArtifactStoreConflictError(
                f"artifact content already exists: {target_path}"
            )

        temp_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if target_path.exists():
                raise ArtifactStoreConflictError(
                    f"artifact content already exists: {target_path}"
                )
            os.replace(temp_path, target_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _content_path_for_artifact(self, artifact: Artifact) -> Path:
        provider = _value(artifact.storage.provider)
        if provider != ArtifactStorageProvider.LOCAL.value:
            raise ArtifactStoreUnsupportedProviderError(
                f"unsupported artifact storage provider: {provider}"
            )

        relative_path = artifact.storage.path
        if relative_path is None:
            uri = artifact.storage.uri
            if not uri.startswith(LOCAL_URI_PREFIX):
                raise ArtifactStoreInvalidStorageError(
                    f"invalid local artifact URI: {uri}"
                )
            relative_path = uri[len(LOCAL_URI_PREFIX) :]
        return self._resolve_local_path(Path(relative_path))

    def _resolve_local_path(self, relative_path: Path) -> Path:
        if relative_path.is_absolute():
            raise ArtifactStoreInvalidStorageError(
                f"local artifact path must be relative: {relative_path}"
            )

        root = self.artifact_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ArtifactStoreInvalidStorageError(
                f"local artifact path escapes artifact root: {relative_path}"
            ) from exc
        return candidate

    def _update_task_artifacts(self, artifact: Artifact) -> None:
        task_state = self.task_repository.get_task(artifact.task_id)
        if _value(task_state.status) in TERMINAL_STATUSES:
            return
        artifact_ref = self.get_artifact_ref(artifact.artifact_id)
        current_artifacts = task_state.current_artifacts
        all_artifact_ids = list(current_artifacts.all_artifact_ids)
        if artifact.artifact_id not in all_artifact_ids:
            all_artifact_ids.append(artifact.artifact_id)

        updates: dict[str, Any] = {"all_artifact_ids": all_artifact_ids}
        pointer_field = ARTIFACT_POINTER_FIELD_BY_TYPE.get(_value(artifact.type))
        if pointer_field is not None:
            updates[pointer_field] = artifact_ref

        updated_current_artifacts = current_artifacts.model_copy(update=updates)
        updated_task_state = task_state.model_copy(
            update={
                "current_artifacts": updated_current_artifacts,
                "updated_at": artifact.updated_at,
            }
        )
        self.task_repository.update_task_state(updated_task_state)


def _normalize_content(content: bytes | bytearray | memoryview | str | JsonValue) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    if isinstance(content, memoryview):
        return content.tobytes()
    if isinstance(content, str):
        return content.encode("utf-8")
    return json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _artifact_type(value: ArtifactType | str) -> ArtifactType:
    if isinstance(value, ArtifactType):
        return value
    return ArtifactType(value)


def _artifact_creator(value: ArtifactCreator | dict[str, Any] | None) -> ArtifactCreator:
    if value is None:
        return ArtifactCreator(type=ArtifactCreatorType.RUNTIME)
    if isinstance(value, ArtifactCreator):
        return value
    return ArtifactCreator.model_validate(value)


def _artifact_metadata(value: ArtifactMetadata | dict[str, Any] | None) -> ArtifactMetadata:
    if value is None:
        return ArtifactMetadata()
    if isinstance(value, ArtifactMetadata):
        return value
    return ArtifactMetadata.model_validate(value)


def _safe_filename(value: str) -> str:
    name = Path(value).name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe_name or "artifact"


def _safe_path_segment(value: str) -> str:
    safe_segment = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return safe_segment or "artifact"


def _local_uri(relative_path: Path) -> str:
    return f"{LOCAL_URI_PREFIX}{relative_path.as_posix()}"


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
