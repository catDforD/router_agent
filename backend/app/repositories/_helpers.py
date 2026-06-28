"""Shared repository helpers."""

from __future__ import annotations

from copy import deepcopy
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


def sanitize_legacy_agent_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop removed artifact-era fields from stored session JSON."""

    sanitized = deepcopy(payload)
    _sanitize_project_context(sanitized.get("project_context"))
    return sanitized


def sanitize_legacy_task_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop removed artifact-era fields from stored task state JSON."""

    sanitized = deepcopy(payload)
    _sanitize_project_context(sanitized.get("project_context"))
    sanitized.pop("current_artifacts", None)
    _sanitize_failures(sanitized.get("failures"))
    return sanitized


def sanitize_legacy_worker_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop removed artifact-era fields from stored WorkerInput JSON."""

    sanitized = deepcopy(payload)
    legacy_refs = sanitized.pop("input_artifacts", []) or []
    if not sanitized.get("input_paths") and isinstance(legacy_refs, list):
        sanitized["input_paths"] = [
            path
            for path in (_path_from_legacy_artifact_ref(ref) for ref in legacy_refs)
            if path
        ]
    sanitized.setdefault("output_paths", [])
    sanitized.setdefault("workspace_root", ".")
    sanitized.setdefault("current_directory", ".")
    sanitized.setdefault("expected_outputs", [])
    return sanitized


def sanitize_legacy_worker_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop removed artifact-era fields from stored WorkerResult JSON."""

    sanitized = deepcopy(payload)
    legacy_refs = sanitized.pop("produced_artifacts", []) or []
    legacy_paths = [
        path for path in (_path_from_legacy_artifact_ref(ref) for ref in legacy_refs) if path
    ]
    sanitized.setdefault("read_paths", [])
    sanitized.setdefault("written_paths", legacy_paths)
    sanitized.setdefault(
        "report_paths",
        [path for path in legacy_paths if path.startswith(".router/") or "report" in path],
    )
    _sanitize_failures(sanitized.get("failures"), evidence_paths=sanitized["report_paths"])
    _sanitize_diagnostics(sanitized.get("diagnostics"))
    return sanitized


def flush_or_raise_conflict(session: Session, message: str) -> None:
    """Flush pending changes and translate integrity errors to repository conflicts."""

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise RepositoryConflictError(message) from exc


def _sanitize_project_context(value: Any) -> None:
    if not isinstance(value, dict):
        return
    value.pop("coding_style_artifact_id", None)
    value.pop("project_memory_artifact_ids", None)


def _sanitize_failures(value: Any, *, evidence_paths: list[str] | None = None) -> None:
    if not isinstance(value, list):
        return
    for failure in value:
        if not isinstance(failure, dict):
            continue
        legacy_evidence = failure.pop("evidence_artifact_ids", None)
        failure.pop("resolved_by_artifact_id", None)
        if not failure.get("evidence_paths"):
            failure["evidence_paths"] = list(evidence_paths or [])
            if not failure["evidence_paths"] and isinstance(legacy_evidence, list):
                failure["evidence_paths"] = [
                    f".router/legacy/{item}.json" for item in legacy_evidence if item
                ]
        reproduction = failure.get("reproduction")
        if isinstance(reproduction, dict):
            input_trace = reproduction.pop("input_trace_artifact_id", None)
            counterexample = reproduction.pop("counterexample_artifact_id", None)
            reproduction.setdefault(
                "input_trace_path",
                f".router/legacy/{input_trace}.json" if input_trace else None,
            )
            reproduction.setdefault(
                "counterexample_path",
                f".router/legacy/{counterexample}.json" if counterexample else None,
            )


def _sanitize_diagnostics(value: Any) -> None:
    if not isinstance(value, list):
        return
    for diagnostic in value:
        if not isinstance(diagnostic, dict):
            continue
        diagnostic.pop("related_artifact_ids", None)
        location = diagnostic.get("location")
        if isinstance(location, dict):
            location.pop("artifact_id", None)


def _path_from_legacy_artifact_ref(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    uri = str(value.get("uri") or "")
    if uri.startswith("workspace://"):
        return uri.removeprefix("workspace://")
    artifact_type = str(value.get("type") or "misc")
    artifact_id = str(value.get("artifact_id") or artifact_type)
    suffix = {
        "plc_code": ".st",
        "patch": ".diff",
        "test_report": ".json",
        "formal_report": ".json",
        "gate_report": ".json",
        "requirements_ir": ".json",
        "io_contract": ".json",
        "failing_trace": ".json",
        "counterexample": ".json",
        "repair_summary": ".json",
    }.get(artifact_type, ".json")
    return f".router/legacy/{artifact_id}{suffix}"
