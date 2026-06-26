"""Stable payload builder for Main Agent file-centric final reports."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue
from sqlalchemy.orm import Session

from app.agents.output_schema import MainAgentEpisodeOutput
from app.models.router_schema import (
    DEFAULT_SCHEMA_VERSION,
    Failure,
    TaskState,
)
from app.repositories.gate_repo import GateResultRecord, GateResultRepository
from app.repositories.task_repo import TaskRepository


REPORT_VERSION = 1
MAX_REPORT_STRING_CHARS = 2000
MAX_REPORT_COLLECTION_ITEMS = 100


def build_final_report_payload(
    *,
    session: Session,
    task_id: str,
    output: MainAgentEpisodeOutput,
    main_agent_run_id: str | None,
    created_at: datetime,
) -> dict[str, JsonValue]:
    """Build the compact, stable JSON payload stored as a workspace final report."""

    task = TaskRepository(session).get_task(task_id)
    gate_results = GateResultRepository(session).list_results(task_id)

    payload: dict[str, Any] = {
        "kind": "main_agent_final_report",
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "created_at": created_at.isoformat(),
        "task_id": task.task_id,
        "main_agent_run_id": main_agent_run_id,
        "final_task_status": _value(output.final_task_status),
        "user_goal": _user_goal(task),
        "classification": _classification(task),
        "summary": _bounded_text(output.summary),
        "plan": [_jsonable(step) for step in output.plan],
        "decisions": [_jsonable(decision) for decision in output.decisions],
        "delivery_files": _delivery_files(task),
        "validation_summary": _validation_summary(task=task, gate_results=gate_results),
        "repair_summary": _repair_summary(task),
        "assumptions": [_jsonable(assumption) for assumption in task.assumptions],
        "unresolved_items": _unresolved_items(task),
        "gate_summary": {
            "task_gates": _jsonable(task.gates),
            "main_agent_gate_summary": (
                _jsonable(output.gate_summary)
                if output.gate_summary is not None
                else None
            ),
        },
        "trace_refs": _trace_refs(task),
        "main_agent_output_summary": _main_agent_output_summary(output),
    }
    return _sanitize_report_value(payload)


def _user_goal(task: TaskState) -> dict[str, JsonValue]:
    return {
        "raw_user_request": _bounded_text(task.raw_user_request),
        "normalized_goal": _bounded_text(task.normalized_goal),
        "title": _bounded_text(task.title),
        "project_context": _jsonable(task.project_context),
    }


def _classification(task: TaskState) -> dict[str, JsonValue]:
    return {
        "task_type": _value(task.task_type),
        "difficulty": _jsonable(task.difficulty),
    }


def _delivery_files(task: TaskState) -> dict[str, JsonValue]:
    current_files = task.current_files
    named_paths = {
        "raw_user_request": current_files.raw_user_request,
        "requirements": current_files.requirements,
        "final_plc_code": current_files.current_code,
        "io_contract": current_files.current_io_contract,
        "test_cases": current_files.latest_test_cases,
        "test_report": current_files.latest_test_report,
        "failing_trace": current_files.latest_failing_trace,
        "formal_properties": current_files.latest_formal_properties,
        "formal_report": current_files.latest_formal_report,
        "counterexample": current_files.latest_counterexample,
        "patch": current_files.latest_patch,
        "repair_summary": current_files.latest_repair_summary,
        "gate_report": current_files.latest_gate_report,
        "final_report": current_files.final_report,
        "main_agent_log": current_files.main_agent_log,
    }
    return {
        key: _file_summary(task, path) if path is not None else None
        for key, path in named_paths.items()
    } | {
        "all": [
            summary
            for path in current_files.all_paths
            if (summary := _file_summary(task, path)) is not None
        ]
    }


def _validation_summary(
    *,
    task: TaskState,
    gate_results: list[GateResultRecord],
) -> dict[str, JsonValue]:
    return {
        "test_required": task.gates.test_required,
        "formal_required": task.gates.formal_required,
        "regression_required": task.gates.regression_required,
        "formal_regression_required": task.gates.formal_regression_required,
        "latest_test_passed": task.gates.latest_test_passed,
        "latest_formal_passed": task.gates.latest_formal_passed,
        "has_blocking_failure": task.gates.has_blocking_failure,
        "can_finish_as_success": task.gates.can_finish_as_success,
        "latest_test_report_path": task.current_files.latest_test_report,
        "latest_formal_report_path": task.current_files.latest_formal_report,
        "latest_gate_report_path": task.current_files.latest_gate_report,
        "gate_results": [_gate_result_summary(result) for result in gate_results],
    }


def _repair_summary(task: TaskState) -> dict[str, JsonValue]:
    return {
        "repair_rounds": task.runtime_limits.repair_rounds,
        "max_repair_rounds": task.runtime_limits.max_repair_rounds,
        "repair_budget_exhausted": (
            task.runtime_limits.repair_rounds >= task.runtime_limits.max_repair_rounds
        ),
        "latest_patch_path": task.current_files.latest_patch,
        "latest_repair_summary_path": task.current_files.latest_repair_summary,
        "open_failure_count": _failure_count(task.failures, status="open"),
        "resolved_failure_count": _failure_count(task.failures, status="resolved"),
        "failures": [_failure_summary(failure) for failure in task.failures],
    }


def _unresolved_items(task: TaskState) -> dict[str, JsonValue]:
    open_failures = [
        _failure_summary(failure)
        for failure in task.failures
        if _value(failure.status) == "open"
    ]
    open_questions = [
        _jsonable(question)
        for question in task.unresolved_questions
        if _value(question.status) == "open"
    ]
    return {
        "open_failure_count": len(open_failures),
        "open_question_count": len(open_questions),
        "blocking_failure_count": sum(
            1
            for failure in task.failures
            if _value(failure.status) == "open"
            and _value(failure.severity) == "blocking"
        ),
        "open_failures": open_failures,
        "open_questions": open_questions,
    }


def _trace_refs(task: TaskState) -> dict[str, JsonValue]:
    return {
        "openai_trace_id": task.trace.openai_trace_id,
        "main_agent_run_ids": list(task.trace.main_agent_run_ids),
        "latest_main_agent_run_id": task.trace.latest_main_agent_run_id,
    }


def _main_agent_output_summary(output: MainAgentEpisodeOutput) -> dict[str, JsonValue]:
    return {
        "main_agent_run_id": output.main_agent_run_id,
        "final_task_status": _value(output.final_task_status),
        "phase": output.phase,
        "summary": _bounded_text(output.summary),
        "next_recommended_action": _value(output.next_recommended_action),
        "error_code": output.error_code,
        "error_message": _bounded_text(output.error_message),
        "open_clarification_question_ids": list(output.open_clarification_question_ids),
        "artifact_refs": [_jsonable(ref) for ref in output.artifact_refs],
        "metadata": _jsonable(output.metadata),
    }


def _file_summary(task: TaskState, relative_path: str | None) -> dict[str, JsonValue] | None:
    if not relative_path:
        return None

    summary: dict[str, Any] = {"path": relative_path}
    root = _workspace_root(task)
    if root is None:
        return summary

    try:
        target = (root / relative_path).resolve()
        target.relative_to(root)
    except ValueError:
        summary["exists"] = False
        summary["error"] = "path_outside_workspace"
        return summary

    summary["exists"] = target.exists()
    if target.is_file():
        summary["size_bytes"] = target.stat().st_size
        summary["mime_type"] = _mime_type_for_path(relative_path)
    return summary


def _workspace_root(task: TaskState) -> Path | None:
    if task.workspace is not None and task.workspace.root:
        return Path(task.workspace.root).expanduser().resolve()
    if task.project_context.workspace_root:
        return Path(task.project_context.workspace_root).expanduser().resolve()
    return None


def _mime_type_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix in {".st", ".scl", ".txt", ".diff", ".patch"}:
        return "text/plain"
    return None


def _gate_result_summary(result: GateResultRecord) -> dict[str, JsonValue]:
    result_payload = result.result if isinstance(result.result, dict) else {}
    return {
        "gate_result_id": result.id,
        "gate_type": result.gate_type,
        "status": result.status,
        "blocking": result.blocking,
        "evidence_paths": list(result_payload.get("evidence_paths") or []),
        "result": _jsonable(result.result),
        "created_at": result.created_at.isoformat(),
    }


def _failure_summary(failure: Failure) -> dict[str, JsonValue]:
    return {
        "failure_id": failure.failure_id,
        "source": _value(failure.source),
        "severity": _value(failure.severity),
        "title": _bounded_text(failure.title),
        "description": _bounded_text(failure.description),
        "evidence_paths": list(failure.evidence_paths),
        "status": _value(failure.status),
        "created_by_worker_job_id": failure.created_by_worker_job_id,
        "resolved_by_worker_job_id": failure.resolved_by_worker_job_id,
        "resolved_by_path": failure.resolved_by_path,
        "created_at": failure.created_at.isoformat(),
        "resolved_at": (
            failure.resolved_at.isoformat() if failure.resolved_at is not None else None
        ),
    }


def _failure_count(failures: list[Failure], *, status: str) -> int:
    return sum(1 for failure in failures if _value(failure.status) == status)


def _sanitize_report_value(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, list):
        return [
            _sanitize_report_value(item)
            for item in value[:MAX_REPORT_COLLECTION_ITEMS]
        ]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_report_value(item)
            for key, item in list(value.items())[:MAX_REPORT_COLLECTION_ITEMS]
        }
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= MAX_REPORT_STRING_CHARS:
        return text
    return text[: MAX_REPORT_STRING_CHARS - 15].rstrip() + "... [truncated]"


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
