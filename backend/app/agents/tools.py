"""Main Agent function tools backed by deterministic Router runtime services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import fnmatch
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

try:  # Keep core service tests independent from the SDK import boundary.
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover - exercised only when SDK is absent locally.
    RunContextWrapper = Any  # type: ignore[assignment]

    def function_tool(func: Any | None = None, **_: Any) -> Any:
        if func is None:
            return lambda wrapped: wrapped
        return func

from app.core.errors import RepositoryNotFoundError
from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.output_schema import (
    MainAgentDecision,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
    MainAgentPlanStep,
)
from app.core.ids import new_event_id, prefixed_id
from app.core.time import utc_now
from app.mcp.adapter import McpAdapter
from app.mcp.mock_worker import DEFAULT_MOCK_SCENARIO
from app.models.router_schema import (
    AgentToolCallRecord,
    Artifact,
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactMetadata,
    ArtifactRef,
    ArtifactType,
    DEFAULT_SCHEMA_VERSION,
    ClarificationQuestion,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    Failure,
    FailureSource,
    FailureStatus,
    RouterEvent,
    Severity,
    TaskPhase,
    TaskState,
    TaskStatus,
    TaskType,
    TraceContext,
    WorkerCompilerType,
    WorkerExecutionStatus,
    WorkerConfig,
    WorkerFuzzMethod,
    WorkerInput,
    WorkerJobRef,
    WorkerJobStatus,
    WorkerOutcomeStatus,
    WorkerResult,
    WorkerPipelineStage,
    WorkerRepairSource,
    WorkerRepairTarget,
    WorkerTargetLanguage,
    WorkerType,
)
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRepository
from app.services.artifact_store import (
    ArtifactContentWrite,
    ArtifactStore,
    ArtifactStoreContentError,
    ArtifactStoreInvalidStorageError,
    ArtifactStoreUnsupportedProviderError,
)
from app.services.event_service import EventService
from app.services.quality_gate import QualityGateService
from app.services.scheduler_guard import (
    ProposedWorkerJob,
    SchedulerGuardViolation,
    validate_parallel_jobs,
    validate_worker_call,
)
from app.workers.worker_input_builder import (
    WorkerInputBuildError,
    build_worker_input,
)
from app.workers.worker_result_handler import handle_worker_result


DEFAULT_READ_ARTIFACT_MAX_CHARS = 12_000
DEFAULT_AGENT_TOOL_OUTPUT_MAX_CHARS = 12_000
DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS = 120
CheckpointCallback = Callable[[], None]
TERMINAL_EVENT_BY_STATUS = {
    TaskStatus.SUCCEEDED.value: EventType.TASK_SUCCEEDED,
    TaskStatus.PARTIAL_FAILED.value: EventType.TASK_PARTIAL_FAILED,
    TaskStatus.FAILED.value: EventType.TASK_FAILED,
    TaskStatus.CANCELLED.value: EventType.TASK_CANCELLED,
}
TERMINAL_STATUS_VALUES = tuple(TERMINAL_EVENT_BY_STATUS)


@dataclass(frozen=True)
class AgentToolContext:
    """Runtime resources passed to SDK tool calls through agent context."""

    session: Session
    artifact_root: Path
    workspace_root: Path | None = None
    execution_mode: str = "disabled"
    command_timeout_seconds: int = DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS
    tool_output_max_chars: int = DEFAULT_AGENT_TOOL_OUTPUT_MAX_CHARS
    mcp_mode: str = "mock"
    mock_scenario: str = DEFAULT_MOCK_SCENARIO
    read_artifact_max_chars: int = DEFAULT_READ_ARTIFACT_MAX_CHARS
    report_first_finalization: bool = False
    checkpoint: CheckpointCallback | None = None
    observability_recorder: Any | None = None


@dataclass(frozen=True)
class MainAgentToolDefinition:
    """Registry metadata for one generic Main Agent tool."""

    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    sdk_tool: Any
    executor_method: str


class ToolStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    NOOP = "no-op"


class ToolBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ArtifactRefSummary(ToolBaseModel):
    artifact_id: str
    type: str
    version: int
    uri: str | None = None
    summary: str | None = None
    content_hash: str | None = None


class FailureSummary(ToolBaseModel):
    failure_id: str
    source: str
    severity: str
    status: str
    title: str
    evidence_paths: list[str] = Field(default_factory=list)


class GateStateSummary(ToolBaseModel):
    test_required: bool
    formal_required: bool
    regression_required: bool
    formal_regression_required: bool
    latest_test_passed: bool | None
    latest_formal_passed: bool | None
    has_blocking_failure: bool
    can_finish_as_success: bool


class ToolViolation(ToolBaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolError(ToolBaseModel):
    error_code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactReadSummary(ToolBaseModel):
    artifact_id: str
    task_id: str
    type: str
    version: int
    name: str
    summary: str
    uri: str
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    content: str | None = None
    content_truncated: bool = False
    content_chars: int | None = None


class AgentToolResult(ToolBaseModel):
    """Compact tool output intended for Main Agent context."""

    tool: str
    task_id: str | None = None
    status: ToolStatus
    summary: str
    artifact_refs: list[ArtifactRefSummary] = Field(default_factory=list)
    failures: list[FailureSummary] = Field(default_factory=list)
    gate_state: GateStateSummary | None = None
    next_recommended_action: str | None = None
    worker_job_id: str | None = None
    worker_type: str | None = None
    execution_status: str | None = None
    outcome_status: str | None = None
    read_paths: list[str] = Field(default_factory=list)
    written_paths: list[str] = Field(default_factory=list)
    report_paths: list[str] = Field(default_factory=list)
    violation: ToolViolation | None = None
    error: ToolError | None = None
    artifact: ArtifactReadSummary | None = None
    results: list[AgentToolResult] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ParallelWorkerRequest:
    worker_type: str
    objective: str | None = None
    worker_config: dict[str, Any] | None = None


class AgentToolService:
    """SDK-independent implementation behind Main Agent tools."""

    def __init__(self, context: AgentToolContext) -> None:
        self.context = context
        self.task_repository = TaskRepository(context.session)
        self.artifact_repository = ArtifactRepository(context.session)
        self.artifact_store = ArtifactStore(
            session=context.session,
            artifact_root=context.artifact_root,
        )
        self.event_service = EventService(context.session)
        self.worker_job_repository = WorkerJobRepository(context.session)

    def list_files(
        self,
        task_id: str,
        *,
        path: str = ".",
        recursive: bool = False,
        max_entries: int = 200,
    ) -> AgentToolResult:
        tool_name = "list_files"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"List workspace files under {path}.",
            arguments={
                "task_id": task_id,
                "path": path,
                "recursive": recursive,
                "max_entries": max_entries,
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_read_only", "local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        if max_entries < 1:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_max_entries",
                message="max_entries must be greater than zero",
            )
            self._record_tool_result(tool_name, result)
            return result

        try:
            target = self._resolve_workspace_path(path)
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except FileNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_not_found",
                message=f"path does not exist: {path}",
            )
            self._record_tool_result(tool_name, result)
            return result
        if not target.exists():
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_not_found",
                message=f"path does not exist: {path}",
            )
            self._record_tool_result(tool_name, result)
            return result

        if target.is_file():
            entries = [
                {
                    "path": self._workspace_relative_path(target),
                    "type": "file",
                    "size_bytes": target.stat().st_size,
                }
            ]
            truncated = False
        elif target.is_dir():
            iterator = target.rglob("*") if recursive else target.iterdir()
            entries = []
            truncated = False
            for index, item in enumerate(
                sorted(iterator, key=lambda value: value.as_posix())
            ):
                if index >= max_entries:
                    truncated = True
                    break
                entries.append(
                    {
                        "path": self._workspace_relative_path(item),
                        "type": "directory" if item.is_dir() else "file",
                        "size_bytes": item.stat().st_size if item.is_file() else None,
                    }
                )
        else:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="unsupported_path_type",
                message=f"path is not a regular file or directory: {path}",
            )
            self._record_tool_result(tool_name, result)
            return result

        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Listed {len(entries)} workspace entr{'y' if len(entries) == 1 else 'ies'}.",
            details={
                "path": path,
                "recursive": recursive,
                "entries": entries,
                "truncated": truncated,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def read_file(
        self,
        task_id: str,
        *,
        path: str,
        max_chars: int | None = None,
    ) -> AgentToolResult:
        tool_name = "read_file"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Read workspace file {path}.",
            arguments={"task_id": task_id, "path": path, "max_chars": max_chars},
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_read_only", "local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        limit = max_chars or self.context.tool_output_max_chars
        if limit < 1:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_max_chars",
                message="max_chars must be greater than zero",
            )
            self._record_tool_result(tool_name, result)
            return result

        try:
            target = self._resolve_workspace_path(path)
            content = target.read_text(encoding="utf-8")
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
        except FileNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="file_not_found",
                message=f"file does not exist: {path}",
            )
        except IsADirectoryError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_is_directory",
                message=f"path is a directory: {path}",
            )
        except UnicodeDecodeError:
            result = self._failed_result(
                tool_name=tool_name,
                task_id=task_id,
                message=f"file is not UTF-8 text: {path}",
                error_code="file_not_utf8",
            )
        else:
            truncated = len(content) > limit
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Read {self._workspace_relative_path(target)}.",
            read_paths=[self._workspace_relative_path(target)],
            details={
                "path": self._workspace_relative_path(target),
                "content": content[:limit],
                    "content_truncated": truncated,
                    "content_chars": min(len(content), limit),
                    "size_chars": len(content),
                },
            )
        self._record_tool_result(tool_name, result)
        return result

    def glob(
        self,
        task_id: str,
        *,
        pattern: str,
        path: str = ".",
        max_entries: int = 200,
    ) -> AgentToolResult:
        tool_name = "glob"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Find workspace files matching {pattern}.",
            arguments={
                "task_id": task_id,
                "pattern": pattern,
                "path": path,
                "max_entries": max_entries,
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_read_only", "local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        if max_entries < 1:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_max_entries",
                message="max_entries must be greater than zero",
            )
            self._record_tool_result(tool_name, result)
            return result
        try:
            root = self._resolve_workspace_path(path)
        except (ValueError, FileNotFoundError) as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        matches: list[dict[str, Any]] = []
        for item in root.glob(pattern):
            if len(matches) >= max_entries:
                break
            if not item.exists():
                continue
            matches.append(
                {
                    "path": self._workspace_relative_path(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                }
            )
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Found {len(matches)} workspace path match{'es' if len(matches) != 1 else ''}.",
            details={
                "pattern": pattern,
                "path": path,
                "entries": matches,
                "truncated": len(matches) >= max_entries,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def grep(
        self,
        task_id: str,
        *,
        pattern: str,
        path: str = ".",
        include: str | None = None,
        max_matches: int = 200,
    ) -> AgentToolResult:
        tool_name = "grep"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Search workspace files for {pattern}.",
            arguments={
                "task_id": task_id,
                "pattern": pattern,
                "path": path,
                "include": include,
                "max_matches": max_matches,
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_read_only", "local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        if max_matches < 1:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_max_matches",
                message="max_matches must be greater than zero",
            )
            self._record_tool_result(tool_name, result)
            return result
        try:
            root = self._resolve_workspace_path(path)
        except (ValueError, FileNotFoundError) as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        files = [root] if root.is_file() else [item for item in root.rglob("*") if item.is_file()]
        matches: list[dict[str, Any]] = []
        for file_path in files:
            rel = self._workspace_relative_path(file_path)
            if include and not fnmatch.fnmatch(rel, include):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(lines, start=1):
                if pattern not in line:
                    continue
                matches.append({"path": rel, "line": line_no, "text": line[:500]})
                if len(matches) >= max_matches:
                    result = AgentToolResult(
                        tool=tool_name,
                        task_id=task_id,
                        status=ToolStatus.APPLIED,
                        summary=f"Found {len(matches)} text match{'es' if len(matches) != 1 else ''}.",
                        details={
                            "pattern": pattern,
                            "path": path,
                            "include": include,
                            "matches": matches,
                            "truncated": True,
                        },
                    )
                    self._record_tool_result(tool_name, result)
                    return result
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Found {len(matches)} text match{'es' if len(matches) != 1 else ''}.",
            details={
                "pattern": pattern,
                "path": path,
                "include": include,
                "matches": matches,
                "truncated": False,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def write_file(
        self,
        task_id: str,
        *,
        path: str,
        content: str,
        create_dirs: bool = False,
    ) -> AgentToolResult:
        tool_name = "write_file"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Write workspace file {path}.",
            arguments={
                "task_id": task_id,
                "path": path,
                "content_chars": len(content),
                "create_dirs": create_dirs,
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        try:
            target = self._resolve_workspace_path(path, allow_missing=True)
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except FileNotFoundError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="parent_directory_missing",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except OSError as exc:
            result = self._failed_result(
                tool_name=tool_name,
                task_id=task_id,
                message=str(exc),
                error_code=type(exc).__name__,
            )
            self._record_tool_result(tool_name, result)
            return result

        rel_path = self._workspace_relative_path(target)
        self._record_workspace_file_path(task_id=task_id, path=rel_path)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Wrote {rel_path}.",
            written_paths=[rel_path],
            report_paths=[rel_path] if _is_report_path(rel_path) else [],
            details={
                "path": rel_path,
                "size_bytes": target.stat().st_size,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def apply_patch(
        self,
        task_id: str,
        *,
        patch: str,
        cwd: str = ".",
    ) -> AgentToolResult:
        tool_name = "apply_patch"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary="Apply a unified patch in the workspace.",
            arguments={
                "task_id": task_id,
                "cwd": cwd,
                "patch_chars": len(patch),
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        try:
            target_cwd = self._resolve_workspace_path(cwd)
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except FileNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_not_found",
                message=f"cwd does not exist: {cwd}",
            )
            self._record_tool_result(tool_name, result)
            return result
        if not target_cwd.is_dir():
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="cwd_not_directory",
                message=f"cwd is not a directory: {cwd}",
            )
            self._record_tool_result(tool_name, result)
            return result

        started = time.monotonic()
        try:
            completed = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                input=patch,
                cwd=target_cwd,
                text=True,
                capture_output=True,
                timeout=self.context.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            result = self._failed_result(
                tool_name=tool_name,
                task_id=task_id,
                message=f"patch command timed out after {self.context.command_timeout_seconds} seconds",
                error_code="patch_timeout",
                details={
                    "cwd": self._workspace_relative_path(target_cwd),
                    "duration_ms": duration_ms,
                    "stdout": _bounded_output(exc.stdout),
                    "stderr": _bounded_output(exc.stderr),
                },
            )
            self._record_tool_result(tool_name, result)
            return result
        duration_ms = int((time.monotonic() - started) * 1000)
        patch_path = self._write_run_output_file(
            task_id=task_id,
            name="applied_patch.diff",
            content=patch,
        )
        status = ToolStatus.APPLIED if completed.returncode == 0 else ToolStatus.FAILED
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=status,
            summary=(
                "Patch applied."
                if completed.returncode == 0
                else "Patch failed to apply."
            ),
            written_paths=[patch_path],
            report_paths=[patch_path],
            error=(
                None
                if completed.returncode == 0
                else ToolError(
                    error_code="patch_apply_failed",
                    message=_bounded_output(completed.stderr or completed.stdout),
                    retryable=True,
                )
            ),
            details={
                "cwd": self._workspace_relative_path(target_cwd),
                "exit_code": completed.returncode,
                "stdout": _bounded_output(completed.stdout),
                "stderr": _bounded_output(completed.stderr),
                "duration_ms": duration_ms,
                "patch_path": patch_path,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def exec_command(
        self,
        task_id: str,
        *,
        command: str,
        cwd: str = ".",
        timeout_seconds: int | None = None,
    ) -> AgentToolResult:
        tool_name = "exec_command"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Run command in workspace: {command}",
            arguments={
                "task_id": task_id,
                "command": command,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        try:
            target_cwd = self._resolve_workspace_path(cwd)
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except FileNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_not_found",
                message=f"cwd does not exist: {cwd}",
            )
            self._record_tool_result(tool_name, result)
            return result
        if not target_cwd.is_dir():
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="cwd_not_directory",
                message=f"cwd is not a directory: {cwd}",
            )
            self._record_tool_result(tool_name, result)
            return result
        timeout = timeout_seconds or self.context.command_timeout_seconds
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=target_cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
                executable="/bin/bash",
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            result = self._failed_result(
                tool_name=tool_name,
                task_id=task_id,
                message=f"command timed out after {timeout} seconds",
                error_code="command_timeout",
                details={
                    "command": command,
                    "cwd": self._workspace_relative_path(target_cwd),
                    "duration_ms": duration_ms,
                    "stdout": _bounded_output(exc.stdout),
                    "stderr": _bounded_output(exc.stderr),
                },
            )
            self._record_tool_result(tool_name, result)
            return result

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        combined = stdout + stderr
        output_path: str | None = None
        if len(combined) > self.context.tool_output_max_chars:
            output_path = self._write_run_output_file(
                task_id=task_id,
                name="command_output.txt",
                content=f"$ {command}\n\n# stdout\n{stdout}\n\n# stderr\n{stderr}",
            )

        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=(
                ToolStatus.APPLIED
                if completed.returncode == 0
                else ToolStatus.FAILED
            ),
            summary=(
                f"Command exited with code {completed.returncode}."
            ),
            written_paths=[output_path] if output_path else [],
            report_paths=[output_path] if output_path else [],
            error=(
                None
                if completed.returncode == 0
                else ToolError(
                    error_code="command_failed",
                    message=f"command exited with code {completed.returncode}",
                    retryable=True,
                )
            ),
            details={
                "command": command,
                "cwd": self._workspace_relative_path(target_cwd),
                "exit_code": completed.returncode,
                "stdout": _bounded_output(stdout, self.context.tool_output_max_chars),
                "stderr": _bounded_output(stderr, self.context.tool_output_max_chars),
                "stdout_truncated": len(stdout) > self.context.tool_output_max_chars,
                "stderr_truncated": len(stderr) > self.context.tool_output_max_chars,
                "output_path": output_path,
                "duration_ms": duration_ms,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def git_status(
        self,
        task_id: str,
        *,
        cwd: str = ".",
    ) -> AgentToolResult:
        tool_name = "git_status"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary="Inspect git working tree status.",
            arguments={"task_id": task_id, "cwd": cwd},
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_read_only", "local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        try:
            target_cwd = self._resolve_workspace_path(cwd)
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except FileNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_not_found",
                message=f"cwd does not exist: {cwd}",
            )
            self._record_tool_result(tool_name, result)
            return result
        if not target_cwd.is_dir():
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="cwd_not_directory",
                message=f"cwd is not a directory: {cwd}",
            )
            self._record_tool_result(tool_name, result)
            return result
        try:
            completed = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=target_cwd,
                text=True,
                capture_output=True,
                timeout=self.context.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            result = self._failed_result(
                tool_name=tool_name,
                task_id=task_id,
                message=f"git status timed out after {self.context.command_timeout_seconds} seconds",
                error_code="git_status_timeout",
                details={
                    "cwd": self._workspace_relative_path(target_cwd),
                    "stdout": _bounded_output(exc.stdout),
                    "stderr": _bounded_output(exc.stderr),
                },
            )
            self._record_tool_result(tool_name, result)
            return result
        output = completed.stdout or completed.stderr
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=(
                ToolStatus.APPLIED
                if completed.returncode == 0
                else ToolStatus.FAILED
            ),
            summary=(
                "Git status read."
                if completed.returncode == 0
                else "Git status failed."
            ),
            details={
                "cwd": self._workspace_relative_path(target_cwd),
                "exit_code": completed.returncode,
                "status": output.splitlines(),
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def write_artifact(
        self,
        task_id: str,
        *,
        name: str,
        content: Any,
        summary: str,
        artifact_type: str = ArtifactType.MISC.value,
        mime_type: str | None = None,
    ) -> AgentToolResult:
        tool_name = "write_artifact"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Write artifact {name}.",
            arguments={
                "task_id": task_id,
                "name": name,
                "artifact_type": artifact_type,
                "summary": summary,
            },
        )
        try:
            artifact = self._write_tool_artifact(
                task_id=task_id,
                name=name,
                content=content,
                summary=summary,
                artifact_type=artifact_type,
                mime_type=mime_type,
            )
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_artifact_type",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=summary,
            artifact_refs=[_artifact_ref_summary(artifact)],
            details={"artifact_id": artifact.artifact_id, "type": _value(artifact.type)},
        )
        self._record_tool_result(tool_name, result)
        return result

    def register_workspace_file(
        self,
        task_id: str,
        *,
        path: str,
        artifact_type: str,
        summary: str,
        file_role: str | None = None,
        mime_type: str | None = None,
    ) -> AgentToolResult:
        tool_name = "register_workspace_file"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Register workspace file {path} as {artifact_type}.",
            arguments={
                "task_id": task_id,
                "path": path,
                "artifact_type": artifact_type,
                "summary": summary,
                "file_role": file_role,
            },
        )
        rejected = self._require_execution_mode(
            tool_name,
            task_id,
            allowed={"local_read_only", "local_full_access"},
        )
        if rejected is not None:
            self._record_tool_result(tool_name, rejected)
            return rejected
        try:
            resolved_type = _artifact_type_from_tool(artifact_type)
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_artifact_type",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        try:
            target = self._resolve_workspace_path(path)
        except ValueError as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="workspace_path_rejected",
                message=str(exc),
            )
            self._record_tool_result(tool_name, result)
            return result
        except FileNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="file_not_found",
                message=f"file does not exist: {path}",
            )
            self._record_tool_result(tool_name, result)
            return result
        if not target.is_file():
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="path_is_not_file",
                message=f"path is not a file: {path}",
            )
            self._record_tool_result(tool_name, result)
            return result
        rel_path = self._workspace_relative_path(target)
        artifact = self.artifact_store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task_id,
                artifact_type=resolved_type,
                version=1,
                name=target.name,
                content=target.read_bytes(),
                summary=summary,
                visibility="user",
                created_by=ArtifactCreator(type=ArtifactCreatorType.MAIN_AGENT),
                metadata=ArtifactMetadata(
                    workspace_path=rel_path,
                    file_role=file_role or _value(resolved_type),
                    source_task_id=task_id,
                    tags=["workspace_file", rel_path],
                ),
                mime_type=mime_type,
            )
        ).artifact
        artifact_ref = self.artifact_store.get_artifact_ref(artifact.artifact_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Registered {rel_path} as {resolved_type.value}.",
            artifact_refs=[_artifact_ref_summary(artifact_ref)],
            details={
                "path": rel_path,
                "artifact_id": artifact.artifact_id,
                "type": _value(artifact.type),
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def _prepare_domain_worker_task(
        self,
        *,
        task_id: str,
        worker_type: str,
    ) -> TaskState:
        task = self._get_task(task_id)
        gate_updates = _gate_updates_for_worker(worker_type)
        if not _is_intake_or_unknown_task(task):
            if not gate_updates:
                return task
            changed_updates = {
                key: value
                for key, value in gate_updates.items()
                if getattr(task.gates, key) != value
            }
            if not changed_updates:
                return task
            updated = task.model_copy(
                deep=True,
                update={
                    "gates": task.gates.model_copy(update=changed_updates),
                    "updated_at": utc_now(),
                },
            )
            self.task_repository.update_task_state(updated)
            self._checkpoint()
            return self._get_task(task_id)

        if not gate_updates and worker_type == WorkerType.PLC_DEV.value:
            gate_updates = {}
        elif not gate_updates:
            return task

        now = utc_now()
        task_type = _domain_task_type_for_worker(worker_type)
        updated = task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.RUNNING.value,
                "phase": TaskPhase.PLANNING.value,
                "task_type": task_type,
                "gates": task.gates.model_copy(update=gate_updates),
                "normalized_goal": task.normalized_goal or task.raw_user_request,
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            _build_task_event(
                task=updated,
                event_type=EventType.TASK_UPDATED,
                title="Domain tool task context prepared",
                message=(
                    "Task context was prepared for MCP/domain worker dispatch."
                ),
                payload={
                    "task_id": task_id,
                    "worker_type": worker_type,
                    "task_type": task_type,
                    "phase": TaskPhase.PLANNING.value,
                    "status": TaskStatus.RUNNING.value,
                },
                created_at=now,
            )
        )
        self._checkpoint()
        return self._get_task(task_id)

    def update_plan(
        self,
        task_id: str,
        *,
        summary: str,
        plan: list[dict[str, Any]] | None = None,
        normalized_goal: str | None = None,
        task_type: str | None = None,
        requires_test: bool | None = None,
        requires_formal: bool | None = None,
    ) -> AgentToolResult:
        tool_name = "update_plan"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=summary,
            arguments={
                "task_id": task_id,
                "summary": summary,
                "plan": plan or [],
                "normalized_goal": normalized_goal,
                "task_type": task_type,
                "requires_test": requires_test,
                "requires_formal": requires_formal,
            },
        )
        task = self._get_task(task_id)
        if _value(task.status) in TERMINAL_EVENT_BY_STATUS:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="terminal_task",
                message=f"cannot update plan for terminal task: {task_id}",
            )
            self._record_tool_result(tool_name, result)
            return result

        now = utc_now()
        selected_task_type = _normalized_task_type_from_tool(
            task_type,
            current_task_type=_value(task.task_type),
        )
        difficulty = task.difficulty.model_copy(
            update={
                "requires_test": (
                    requires_test
                    if requires_test is not None
                    else task.difficulty.requires_test
                ),
                "requires_formal": (
                    requires_formal
                    if requires_formal is not None
                    else task.difficulty.requires_formal
                ),
                "need_clarification": False,
            }
        )
        gates = task.gates.model_copy(
            update={
                "test_required": (
                    requires_test
                    if requires_test is not None
                    else task.gates.test_required
                ),
                "formal_required": (
                    requires_formal
                    if requires_formal is not None
                    else task.gates.formal_required
                ),
                "can_finish_as_success": False,
            }
        )
        updated = task.model_copy(
            deep=True,
            update={
                "normalized_goal": normalized_goal or task.normalized_goal or task.raw_user_request,
                "task_type": selected_task_type,
                "difficulty": difficulty,
                "gates": gates,
                "status": TaskStatus.RUNNING.value,
                "phase": TaskPhase.PLANNING.value,
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            _build_main_agent_event(
                task=updated,
                event_type=EventType.MAIN_AGENT_PLAN_UPDATED,
                title="Main Agent plan updated",
                message=summary,
                payload={
                    "task_id": task_id,
                    "summary": summary,
                    "plan": plan or [],
                },
                created_at=now,
            )
        )
        self._checkpoint()
        persisted = self._get_task(task_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=summary,
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            details={"plan": plan or []},
        )
        self._record_tool_result(tool_name, result)
        return result

    def request_clarification(
        self,
        task_id: str,
        *,
        questions: list[dict[str, Any]] | list[str],
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        tool_name = "request_clarification"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={"task_id": task_id, "questions": questions},
        )
        task = self._get_task(task_id)
        if not questions:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="missing_clarification_questions",
                message="request_clarification requires at least one question",
            )
            self._record_tool_result(tool_name, result)
            return result
        now = utc_now()
        clarification_questions = [
            _clarification_question_from_tool(item, now=now)
            for item in questions
        ]
        updated = task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.WAITING_USER.value,
                "phase": TaskPhase.CLARIFYING.value,
                "difficulty": task.difficulty.model_copy(
                    update={"need_clarification": True}
                ),
                "unresolved_questions": [
                    *task.unresolved_questions,
                    *clarification_questions,
                ],
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        question_ids = [question.question_id for question in clarification_questions]
        self.event_service.append_event(
            _build_main_agent_event(
                task=updated,
                event_type=EventType.MAIN_AGENT_CLARIFICATION_REQUESTED,
                title="Main Agent requested clarification",
                message=rationale_summary or "Main Agent paused for user clarification.",
                payload={"task_id": task_id, "question_ids": question_ids},
                created_at=now,
            )
        )
        self.event_service.append_event(
            _build_task_event(
                task=updated,
                event_type=EventType.TASK_WAITING_USER,
                title="Task waiting for user",
                message="The task needs user clarification before workers can run.",
                payload={
                    "task_id": task_id,
                    "status": TaskStatus.WAITING_USER.value,
                    "phase": TaskPhase.CLARIFYING.value,
                    "question_ids": question_ids,
                },
                created_at=now,
            )
        )
        self._checkpoint()
        persisted = self._get_task(task_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary="Task paused for user clarification.",
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            next_recommended_action="ask_user",
            details={"question_ids": question_ids},
        )
        self._record_tool_result(tool_name, result)
        return result

    def write_final_report(
        self,
        task_id: str,
        *,
        final_status: str,
        summary: str,
        rationale_summary: str | None = None,
        decisions: list[dict[str, Any]] | None = None,
        plan: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        tool_name = "write_final_report"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary or summary,
            arguments={
                "task_id": task_id,
                "final_status": final_status,
                "summary": summary,
                "decisions": decisions or [],
                "plan": plan or [],
                "metadata": metadata or {},
            },
        )
        task = self._get_task(task_id)
        output = MainAgentEpisodeOutput(
            task_id=task_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            final_task_status=final_status,
            phase=_value(task.phase),
            decisions=_main_agent_decisions_from_tool(decisions),
            plan=_main_agent_plan_from_tool(plan),
            artifact_refs=[],
            gate_summary=MainAgentGateSummary.model_validate(
                task.gates.model_dump(mode="json")
            ),
            open_clarification_question_ids=[
                question.question_id
                for question in task.unresolved_questions
                if _value(question.status) == "open"
            ],
            summary=summary,
            metadata=metadata or {},
        )
        recorder = self.context.observability_recorder or MainAgentObservabilityRecorder(
            session=self.context.session,
            artifact_root=self.context.artifact_root,
            task_id=task_id,
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
            checkpoint=self.context.checkpoint,
        )
        final_report = recorder.write_final_report(output)
        replay_log = recorder.write_replay_log(final_output=output)
        recorder.record_completed(
            output=output,
            final_report=final_report,
            replay_log=replay_log,
        )
        self._checkpoint()
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary="Final report written.",
            written_paths=[final_report, replay_log],
            report_paths=[final_report, replay_log],
            failures=_failure_summaries(task.failures),
            gate_state=_gate_state_summary(task),
            details={
                "final_status": final_status,
                "final_report_path": final_report,
                "main_agent_log_path": replay_log,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def plc_dev(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        target_language: str | None = None,
        template: str | None = None,
        language_hint: str | None = None,
        enable_socratic_spec: bool | None = None,
        socratic_skip: bool | None = None,
        compiler_type: str | None = None,
        rpc_pipeline: list[str] | None = None,
        llm: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        return self._call_prepared_worker_tool(
            tool_name="plc_dev",
            task_id=task_id,
            worker_type=WorkerType.PLC_DEV.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=_worker_config_from_fields(
                target_language=target_language,
                template=template,
                language_hint=language_hint,
                enable_socratic_spec=enable_socratic_spec,
                socratic_skip=socratic_skip,
                compiler_type=compiler_type,
                rpc_pipeline=rpc_pipeline,
                llm=llm,
            ),
        )

    def plc_test(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        fuzz_method: str | None = None,
        case_count: int | None = None,
        enable_fuzz_test: bool | None = None,
        llm: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        return self._call_prepared_worker_tool(
            tool_name="plc_test",
            task_id=task_id,
            worker_type=WorkerType.PLC_TEST.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=_worker_config_from_fields(
                fuzz_method=fuzz_method,
                case_count=case_count,
                enable_fuzz_test=enable_fuzz_test,
                llm=llm,
            ),
        )

    def plc_formal(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        compiler_type: str | None = None,
        properties: Any | None = None,
        natural_language_requirements: str | None = None,
        llm: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        return self._call_prepared_worker_tool(
            tool_name="plc_formal",
            task_id=task_id,
            worker_type=WorkerType.PLC_FORMAL.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=_worker_config_from_fields(
                compiler_type=compiler_type,
                properties=properties,
                natural_language_requirements=natural_language_requirements,
                llm=llm,
            ),
        )

    def plc_repair(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        repair_source: str | None = None,
        repair_targets: list[str] | None = None,
        repair_failure_notes: str | None = None,
        compiler_type: str | None = None,
        llm: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        return self._call_prepared_worker_tool(
            tool_name="plc_repair",
            task_id=task_id,
            worker_type=WorkerType.PLC_REPAIR.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=_worker_config_from_fields(
                repair_source=repair_source,
                repair_targets=repair_targets,
                repair_failure_notes=repair_failure_notes,
                compiler_type=compiler_type,
                llm=llm,
            ),
        )

    def call_plc_dev(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        worker_config: dict[str, Any] | WorkerConfig | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_dev",
            task_id=task_id,
            worker_type=WorkerType.PLC_DEV.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=worker_config,
        )

    def call_plc_test(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        worker_config: dict[str, Any] | WorkerConfig | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_test",
            task_id=task_id,
            worker_type=WorkerType.PLC_TEST.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=worker_config,
        )

    def call_plc_formal(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        worker_config: dict[str, Any] | WorkerConfig | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_formal",
            task_id=task_id,
            worker_type=WorkerType.PLC_FORMAL.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=worker_config,
        )

    def call_plc_repair(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        rationale_summary: str | None = None,
        worker_config: dict[str, Any] | WorkerConfig | None = None,
    ) -> AgentToolResult:
        return self._call_worker_tool(
            tool_name="call_plc_repair",
            task_id=task_id,
            worker_type=WorkerType.PLC_REPAIR.value,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=worker_config,
        )

    def _call_prepared_worker_tool(
        self,
        *,
        tool_name: str,
        task_id: str,
        worker_type: str,
        objective: str | None,
        rationale_summary: str | None,
        worker_config: dict[str, Any] | WorkerConfig | None,
    ) -> AgentToolResult:
        self._prepare_domain_worker_task(task_id=task_id, worker_type=worker_type)
        return self._call_worker_tool(
            tool_name=tool_name,
            task_id=task_id,
            worker_type=worker_type,
            objective=objective,
            rationale_summary=rationale_summary,
            worker_config=worker_config,
        )

    def run_parallel_workers(
        self,
        task_id: str,
        requests: list[ParallelWorkerRequest],
        *,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        tool_name = "run_parallel_workers"
        if not requests:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="empty_parallel_batch",
                message="parallel worker batch must not be empty",
            )
            self._record_tool_result(tool_name, result)
            return result

        task = self._get_task(task_id)
        proposed_jobs: list[ProposedWorkerJob] = []
        proposed_paths: list[list[str]] = []
        for request in requests:
            paths = _proposed_worker_input_paths(task, request.worker_type)
            proposed_paths.append(paths)
            proposed_jobs.append(
                ProposedWorkerJob(
                    worker_type=request.worker_type,
                    input_paths=paths,
                )
            )
        worker_configs = [request.worker_config for request in requests]
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={
                "task_id": task_id,
                "workers": [request.worker_type for request in requests],
                "objectives": [request.objective for request in requests],
                "worker_configs": worker_configs,
            },
            input_paths=[path for paths in proposed_paths for path in paths],
        )

        try:
            validate_parallel_jobs(task, proposed_jobs)
        except SchedulerGuardViolation as exc:
            result = self._guard_rejected_result(tool_name, task, exc)
            self._record_tool_result(tool_name, result)
            return result

        worker_inputs: list[WorkerInput] = []
        for request, paths in zip(requests, proposed_paths, strict=True):
            try:
                worker_inputs.append(
                    build_worker_input(
                        task,
                        request.worker_type,
                        objective=request.objective,
                        input_paths=paths,
                        trace_context=_trace_context_for_task(task),
                        worker_config=request.worker_config,
                        metadata={"source": "main_agent_function_tools"},
                    )
                )
            except (WorkerInputBuildError, ValidationError) as exc:
                result = self._rejected_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    task=task,
                    code="worker_input_build_error",
                    message=str(exc),
                    details={"worker_type": request.worker_type},
                )
                self._record_tool_result(tool_name, result)
                return result

        for worker_input in worker_inputs:
            debounced = self._debounced_worker_retry_result(
                tool_name=tool_name,
                task=task,
                worker_input=worker_input,
            )
            if debounced is not None:
                self._record_tool_result(tool_name, debounced)
                return debounced

        results = [
            self._dispatch_worker_input(tool_name=tool_name, worker_input=worker_input)
            for worker_input in worker_inputs
        ]
        latest = self._get_task(task_id)
        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=(
                ToolStatus.FAILED
                if any(result.status == ToolStatus.FAILED.value for result in results)
                else ToolStatus.APPLIED
            ),
            summary=f"Dispatched {len(results)} worker(s).",
            gate_state=_gate_state_summary(latest),
            failures=_failure_summaries(latest.failures),
            results=results,
        )
        self._record_tool_result(tool_name, result)
        return result

    def read_artifact(
        self,
        task_id: str,
        artifact_id: str,
        *,
        mode: str = "summary",
        max_chars: int | None = None,
    ) -> AgentToolResult:
        tool_name = "read_artifact"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=f"Read artifact {artifact_id}.",
            arguments={
                "task_id": task_id,
                "artifact_id": artifact_id,
                "mode": mode,
                "max_chars": max_chars,
            },
        )
        limit = max_chars or self.context.read_artifact_max_chars
        if limit < 1:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_max_chars",
                message="max_chars must be greater than zero",
            )
            self._record_tool_result(tool_name, result)
            return result

        try:
            artifact = self.artifact_repository.get_artifact(artifact_id)
        except RepositoryNotFoundError:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="artifact_not_found",
                message=f"artifact not found: {artifact_id}",
            )
            self._record_tool_result(tool_name, result)
            return result

        if artifact.task_id != task_id:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="foreign_artifact",
                message="artifact does not belong to requested task",
                details={"artifact_id": artifact_id, "artifact_task_id": artifact.task_id},
            )
            self._record_tool_result(tool_name, result)
            return result

        if mode not in {"summary", "full"}:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_read_mode",
                message="read_artifact mode must be 'summary' or 'full'",
                details={"mode": mode},
            )
            self._record_tool_result(tool_name, result)
            return result

        read_summary = _artifact_read_summary(artifact)
        if mode == "full":
            try:
                stored = self.artifact_store.read_artifact_content(artifact_id)
                decoded = stored.content.decode("utf-8")
            except UnicodeDecodeError:
                result = self._failed_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    message=f"artifact content is not UTF-8 text: {artifact_id}",
                    error_code="artifact_not_utf8",
                )
                self._record_tool_result(tool_name, result)
                return result
            except (
                ArtifactStoreContentError,
                ArtifactStoreInvalidStorageError,
                ArtifactStoreUnsupportedProviderError,
            ) as exc:
                result = self._failed_result(
                    tool_name=tool_name,
                    task_id=task_id,
                    message=str(exc),
                    error_code=type(exc).__name__,
                )
                self._record_tool_result(tool_name, result)
                return result
            truncated = len(decoded) > limit
            read_summary = read_summary.model_copy(
                update={
                    "content": decoded[:limit],
                    "content_truncated": truncated,
                    "content_chars": min(len(decoded), limit),
                }
            )

        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=(
                "Artifact metadata read."
                if mode == "summary"
                else "Artifact content read with bounded output."
            ),
            artifact_refs=[_artifact_ref_summary_from_artifact(artifact)],
            artifact=read_summary,
        )
        self._record_tool_result(tool_name, result)
        return result

    def run_quality_gate(
        self,
        task_id: str,
        *,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        self._record_tool_call(
            tool_name="run_quality_gate",
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={"task_id": task_id},
        )
        result = QualityGateService(
            session=self.context.session,
            artifact_root=self.context.artifact_root,
        ).run_quality_gate(task_id)
        self._checkpoint()
        failed_gates = [
            outcome.gate_type
            for outcome in result.assessment.outcomes
            if outcome.blocking
        ]
        failure_ids = (
            _blocking_failure_ids(result.task.failures)
            if result.assessment.blocking
            else []
        )
        tool_result = AgentToolResult(
            tool="run_quality_gate",
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=result.assessment.message,
            report_paths=[result.gate_report_path],
            failures=_failure_summaries(result.task.failures),
            gate_state=_gate_state_summary(result.task),
            details={
                "assessment_status": result.assessment.status,
                "blocking": result.assessment.blocking,
                "gate_report_path": result.gate_report_path,
                "evidence_paths": list(result.assessment.evidence_paths),
                "failed_gates": failed_gates,
                "failure_ids": failure_ids,
            },
        )
        self._record_tool_result("run_quality_gate", tool_result)
        return tool_result

    def record_validation_report(
        self,
        task_id: str,
        validation_type: str,
        status: str,
        summary: str,
        *,
        read_paths: list[str] | None = None,
        failure_ids: list[str] | None = None,
        details: dict[str, Any] | None = None,
        command: str | None = None,
        rationale_summary: str | None = None,
    ) -> AgentToolResult:
        tool_name = "record_validation_report"
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={
                "task_id": task_id,
                "validation_type": validation_type,
                "status": status,
                "summary": summary,
                "read_paths": read_paths,
                "failure_ids": failure_ids,
                "details": details,
                "command": command,
            },
            input_paths=read_paths or [],
        )
        normalized_type = validation_type.strip().lower()
        normalized_status = status.strip().lower()
        if normalized_type not in {"test", "compile", "formal"}:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_validation_type",
                message="validation_type must be one of: test, compile, formal",
            )
            self._record_tool_result(tool_name, result)
            return result
        if normalized_status not in {"passed", "failed"}:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                code="invalid_validation_status",
                message="status must be one of: passed, failed",
            )
            self._record_tool_result(tool_name, result)
            return result

        task = self._get_task(task_id)
        now = utc_now()
        report_path = (
            f".router/reports/main-agent-validation/{task_id}/"
            f"validation_report_{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )
        updated = _task_with_validation_report(
            task,
            validation_type=normalized_type,
            status=normalized_status,
            report_path=report_path,
            failure_ids=list(failure_ids or []),
            now=now,
        )
        resolved_failure_ids = _resolved_failure_ids(task, updated)
        report_payload = {
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "kind": "main_agent_validation_report",
            "task_id": task_id,
            "validation_type": normalized_type,
            "status": normalized_status,
            "summary": summary,
            "read_paths": list(read_paths or []),
            "failure_ids": list(failure_ids or []),
            "resolved_failure_ids": resolved_failure_ids,
            "command": command,
            "details": _json_object(details or {}),
            "created_at": now.isoformat(),
            "created_by": {
                "type": EventSourceType.MAIN_AGENT.value,
                "main_agent_run_id": task.trace.latest_main_agent_run_id,
            },
        }
        target = self._resolve_workspace_path(report_path, allow_missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report_payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        persisted = self.task_repository.update_task_state(updated)
        self._checkpoint()

        result = AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.APPLIED,
            summary=f"Recorded {normalized_type} validation report: {normalized_status}.",
            failures=_failure_summaries(persisted.failures),
            gate_state=_gate_state_summary(persisted),
            read_paths=list(read_paths or []),
            written_paths=[report_path],
            report_paths=[report_path],
            details={
                "validation_type": normalized_type,
                "validation_status": normalized_status,
                "failure_ids": list(failure_ids or []),
                "report_path": report_path,
                "resolved_failure_ids": resolved_failure_ids,
            },
        )
        self._record_tool_result(tool_name, result)
        return result

    def _has_final_report_artifact(self, task_id: str) -> bool:
        return any(
            _value(artifact.type) == ArtifactType.FINAL_REPORT.value
            for artifact in self.artifact_repository.list_task_artifacts(task_id)
        )

    def _call_worker_tool(
        self,
        *,
        tool_name: str,
        task_id: str,
        worker_type: str,
        objective: str | None,
        rationale_summary: str | None,
        worker_config: dict[str, Any] | WorkerConfig | None,
    ) -> AgentToolResult:
        task = self._get_task(task_id)
        proposed_paths = _proposed_worker_input_paths(task, worker_type)
        self._record_tool_call(
            tool_name=tool_name,
            task_id=task_id,
            rationale_summary=rationale_summary,
            arguments={
                "task_id": task_id,
                "worker_type": worker_type,
                "objective": objective,
                "worker_config": worker_config,
            },
            input_paths=proposed_paths,
        )
        try:
            validate_worker_call(task, worker_type, proposed_paths)
        except SchedulerGuardViolation as exc:
            result = self._guard_rejected_result(tool_name, task, exc)
            self._record_tool_result(tool_name, result)
            return result

        try:
            worker_input = build_worker_input(
                task,
                worker_type,
                objective=objective,
                input_paths=proposed_paths,
                trace_context=_trace_context_for_task(task),
                worker_config=worker_config,
                metadata={"source": "main_agent_function_tools"},
            )
        except (WorkerInputBuildError, ValidationError) as exc:
            result = self._rejected_result(
                tool_name=tool_name,
                task_id=task_id,
                task=task,
                code="worker_input_build_error",
                message=str(exc),
                details={"worker_type": worker_type},
            )
            self._record_tool_result(tool_name, result)
            return result

        debounced = self._debounced_worker_retry_result(
            tool_name=tool_name,
            task=task,
            worker_input=worker_input,
        )
        if debounced is not None:
            self._record_tool_result(tool_name, debounced)
            return debounced

        result = self._dispatch_worker_input(
            tool_name=tool_name,
            worker_input=worker_input,
        )
        self._record_tool_result(tool_name, result)
        return result

    def _debounced_worker_retry_result(
        self,
        *,
        tool_name: str,
        task: TaskState,
        worker_input: WorkerInput,
        max_failures: int = 2,
    ) -> AgentToolResult | None:
        signature = _worker_input_signature(worker_input)
        failure_counts: dict[str, int] = {}
        for job in self.worker_job_repository.list_task_jobs(task.task_id):
            if _value(job.worker_type) != _value(worker_input.worker_type):
                continue
            if _worker_input_signature(job.input) != signature:
                continue
            if job.result is None:
                continue
            for failure_type in _worker_failure_types(job.result):
                failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1

        blocked = [
            failure_type
            for failure_type, count in failure_counts.items()
            if count >= max_failures
        ]
        if not blocked:
            return None

        return self._rejected_result(
            tool_name=tool_name,
            task_id=task.task_id,
            task=task,
            code="worker_retry_debounce",
            message=(
                "worker retry debounce rejected dispatch after repeated failures "
                "for the same worker and input paths"
            ),
            details={
                "worker_type": _value(worker_input.worker_type),
                "input_signature": list(signature),
                "blocked_failure_types": sorted(blocked),
                "max_failures": max_failures,
            },
        )

    def _dispatch_worker_input(
        self,
        *,
        tool_name: str,
        worker_input: WorkerInput,
    ) -> AgentToolResult:
        predispatch = self._record_active_worker(worker_input)
        try:
            result = McpAdapter(
                session=self.context.session,
                artifact_root=self.context.artifact_root,
                mcp_mode=self.context.mcp_mode,
                mock_scenario=self.context.mock_scenario,
                checkpoint=self.context.checkpoint,
            ).call_worker(worker_input)
            handled = handle_worker_result(result, session=self.context.session)
            final_task = self._decrement_active_worker_counter(worker_input.task_id)
            self._checkpoint()
            return _worker_result_to_tool_result(
                tool_name=tool_name,
                result=result,
                task=final_task,
                applied=handled.applied,
            )
        except Exception as exc:
            self._restore_active_worker(
                task_id=worker_input.task_id,
                worker_job_id=worker_input.worker_job_id,
                previous_task=predispatch,
            )
            self._checkpoint()
            return self._failed_result(
                tool_name=tool_name,
                task_id=worker_input.task_id,
                message=str(exc),
                error_code=type(exc).__name__,
            )

    def _record_active_worker(self, worker_input: WorkerInput) -> TaskState:
        task = self._get_task(worker_input.task_id)
        active_jobs = [
            job
            for job in task.active_worker_jobs
            if job.worker_job_id != worker_input.worker_job_id
        ]
        active_jobs.append(
            WorkerJobRef(
                worker_job_id=worker_input.worker_job_id,
                worker_type=worker_input.worker_type,
                status=WorkerJobStatus.RUNNING,
                objective=worker_input.objective,
                started_at=worker_input.created_at,
            )
        )
        updated = task.model_copy(
            deep=True,
            update={
                "active_worker_jobs": active_jobs,
                "runtime_limits": task.runtime_limits.model_copy(
                    update={
                        "active_parallel_workers": (
                            task.runtime_limits.active_parallel_workers + 1
                        ),
                        "worker_calls_used": task.runtime_limits.worker_calls_used + 1,
                    }
                ),
                "updated_at": worker_input.created_at,
            },
        )
        self.task_repository.update_task_state(updated)
        return task

    def _decrement_active_worker_counter(self, task_id: str) -> TaskState:
        task = self._get_task(task_id)
        active_workers = max(task.runtime_limits.active_parallel_workers - 1, 0)
        updated = task.model_copy(
            deep=True,
            update={
                "runtime_limits": task.runtime_limits.model_copy(
                    update={"active_parallel_workers": active_workers}
                )
            },
        )
        return self.task_repository.update_task_state(updated)

    def _restore_active_worker(
        self,
        *,
        task_id: str,
        worker_job_id: str,
        previous_task: TaskState,
    ) -> TaskState:
        current = self._get_task(task_id)
        restored_jobs = [
            job
            for job in previous_task.active_worker_jobs
            if job.worker_job_id != worker_job_id
        ]
        updated = current.model_copy(
            deep=True,
            update={
                "active_worker_jobs": restored_jobs,
                "runtime_limits": current.runtime_limits.model_copy(
                    update={
                        "active_parallel_workers": (
                            previous_task.runtime_limits.active_parallel_workers
                        ),
                        "worker_calls_used": previous_task.runtime_limits.worker_calls_used,
                    }
                ),
            },
        )
        return self.task_repository.update_task_state(updated)

    def _require_execution_mode(
        self,
        tool_name: str,
        task_id: str | None,
        *,
        allowed: set[str],
    ) -> AgentToolResult | None:
        if self.context.execution_mode in allowed:
            return None
        return self._rejected_result(
            tool_name=tool_name,
            task_id=task_id,
            code="execution_mode_rejected",
            message=(
                f"{tool_name} requires execution mode "
                f"{', '.join(sorted(allowed))}; current mode is "
                f"{self.context.execution_mode!r}"
            ),
            details={
                "execution_mode": self.context.execution_mode,
                "allowed_modes": sorted(allowed),
            },
        )

    def _workspace_root(self) -> Path:
        return (self.context.workspace_root or Path.cwd()).expanduser().resolve()

    def _resolve_workspace_path(
        self,
        path: str,
        *,
        allow_missing: bool = False,
    ) -> Path:
        root = self._workspace_root()
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else root / raw
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"path is outside configured workspace root: {path}"
            ) from exc
        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(path)
        return resolved

    def _workspace_relative_path(self, path: Path) -> str:
        root = self._workspace_root()
        try:
            return path.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()

    def _write_run_output_file(
        self,
        *,
        task_id: str,
        name: str,
        content: str,
    ) -> str:
        safe_name = Path(name).name or "output.txt"
        output_dir = self._resolve_workspace_path(
            f".router/runs/{task_id}/outputs",
            allow_missing=True,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{int(time.time() * 1000)}_{safe_name}"
        output_file.write_text(content, encoding="utf-8")
        return self._workspace_relative_path(output_file)

    def _record_workspace_file_path(
        self,
        *,
        task_id: str,
        path: str,
        role: str | None = None,
    ) -> None:
        try:
            task = self.task_repository.get_task(task_id)
        except RepositoryNotFoundError:
            return
        field_name = role or _current_file_field_for_path(path)
        all_paths = _append_unique_path(task.current_files.all_paths, path)
        updates: dict[str, Any] = {"all_paths": all_paths}
        if field_name is not None:
            updates[field_name] = path
        self.task_repository.update_task_state(
            task.model_copy(
                deep=True,
                update={
                    "current_files": task.current_files.model_copy(update=updates),
                    "updated_at": utc_now(),
                },
            )
        )

    def _write_tool_artifact(
        self,
        *,
        task_id: str,
        name: str,
        content: Any,
        summary: str,
        artifact_type: str = ArtifactType.MISC.value,
        mime_type: str | None = None,
    ) -> ArtifactRef:
        try:
            resolved_type = _artifact_type_from_tool(artifact_type)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        artifact = self.artifact_store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task_id,
                artifact_type=resolved_type,
                version=1,
                name=name,
                content=content,
                summary=summary,
                visibility="internal",
                created_by={
                    "type": ArtifactCreatorType.MAIN_AGENT,
                },
                metadata={"tags": ["agent_tool", name]},
                mime_type=mime_type,
            )
        ).artifact
        return self.artifact_store.get_artifact_ref(artifact.artifact_id)

    def _get_task(self, task_id: str) -> TaskState:
        if self.context.checkpoint is not None:
            self.context.session.expire_all()
        return self.task_repository.get_task(task_id)

    def _checkpoint(self) -> None:
        if self.context.checkpoint is not None:
            self.context.checkpoint()

    def _record_tool_call(
        self,
        *,
        tool_name: str,
        task_id: str,
        rationale_summary: str | None,
        arguments: dict[str, Any],
        input_paths: list[str] | None = None,
    ) -> None:
        self._append_agent_tool_call_record(
            tool_name=tool_name,
            task_id=task_id,
            arguments=arguments,
            summary=rationale_summary,
        )
        recorder = self.context.observability_recorder
        if recorder is None:
            return
        recorder.record_tool_call(
            tool_name=tool_name,
            rationale_summary=rationale_summary,
            arguments=arguments,
            input_paths=input_paths or [],
        )

    def _record_tool_result(self, tool_name: str, result: AgentToolResult) -> None:
        self._complete_agent_tool_call_record(tool_name=tool_name, result=result)
        recorder = self.context.observability_recorder
        if recorder is None:
            return
        recorder.record_tool_result(tool_name=tool_name, result=result)

    def _append_agent_tool_call_record(
        self,
        *,
        tool_name: str,
        task_id: str,
        arguments: dict[str, Any],
        summary: str | None,
    ) -> None:
        try:
            task = self.task_repository.get_task(task_id)
        except RepositoryNotFoundError:
            return
        latest_run_id = task.trace.latest_main_agent_run_id
        if latest_run_id is None:
            return

        now = utc_now()
        updated_runs = []
        for run in task.agent_runs:
            if run.agent_run_id != latest_run_id:
                updated_runs.append(run)
                continue
            updated_runs.append(
                run.model_copy(
                    deep=True,
                    update={
                        "tool_calls": [
                            *run.tool_calls,
                            AgentToolCallRecord(
                                tool_call_id=prefixed_id("tool-call"),
                                tool_name=tool_name,
                                arguments=_json_safe_mapping(arguments),
                                status="running",
                                summary=summary,
                                started_at=now,
                            ),
                        ]
                    },
                )
            )
        if updated_runs == task.agent_runs:
            return
        self.task_repository.update_task_state(
            task.model_copy(
                deep=True,
                update={"agent_runs": updated_runs, "updated_at": now},
            )
        )

    def _complete_agent_tool_call_record(
        self,
        *,
        tool_name: str,
        result: AgentToolResult,
    ) -> None:
        task_id = result.task_id
        if task_id is None:
            return
        try:
            task = self.task_repository.get_task(task_id)
        except RepositoryNotFoundError:
            return
        latest_run_id = task.trace.latest_main_agent_run_id
        if latest_run_id is None:
            return

        now = utc_now()
        updated_runs = []
        changed = False
        for run in task.agent_runs:
            if run.agent_run_id != latest_run_id:
                updated_runs.append(run)
                continue
            tool_calls = list(run.tool_calls)
            for index in range(len(tool_calls) - 1, -1, -1):
                record = tool_calls[index]
                if record.tool_name == tool_name and record.status == "running":
                    tool_calls[index] = record.model_copy(
                        update={
                            "status": _value(result.status),
                            "summary": result.summary,
                            "completed_at": now,
                        }
                    )
                    changed = True
                    break
            updated_runs.append(
                run.model_copy(deep=True, update={"tool_calls": tool_calls})
            )
        if not changed:
            return
        self.task_repository.update_task_state(
            task.model_copy(
                deep=True,
                update={"agent_runs": updated_runs, "updated_at": now},
            )
        )

    def _guard_rejected_result(
        self,
        tool_name: str,
        task: TaskState,
        violation: SchedulerGuardViolation,
    ) -> AgentToolResult:
        return self._rejected_result(
            tool_name=tool_name,
            task_id=task.task_id,
            task=task,
            code=_value(violation.code),
            message=violation.message,
            details=violation.details,
        )

    def _rejected_result(
        self,
        *,
        tool_name: str,
        task_id: str | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        task: TaskState | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.REJECTED,
            summary=message,
            failures=_failure_summaries(task.failures) if task is not None else [],
            gate_state=_gate_state_summary(task) if task is not None else None,
            violation=ToolViolation(
                code=code,
                message=message,
                details=dict(details or {}),
            ),
        )

    def _failed_result(
        self,
        *,
        tool_name: str,
        task_id: str | None,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(
            tool=tool_name,
            task_id=task_id,
            status=ToolStatus.FAILED,
            summary=message,
            error=ToolError(
                error_code=error_code,
                message=message,
                retryable=False,
                details=dict(details or {}),
            ),
        )


@function_tool(strict_mode=False)
def update_plan(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    summary: str,
    plan: list[dict[str, Any]] | None = None,
    normalized_goal: str | None = None,
    task_type: str | None = None,
    requires_test: bool | None = None,
    requires_formal: bool | None = None,
) -> AgentToolResult:
    """Persist a public Main Agent plan and move the task into planning."""

    return AgentToolService(ctx.context).update_plan(
        task_id=task_id,
        summary=summary,
        plan=plan,
        normalized_goal=normalized_goal,
        task_type=task_type,
        requires_test=requires_test,
        requires_formal=requires_formal,
    )


@function_tool(strict_mode=False)
def request_clarification(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    questions: list[dict[str, Any]] | list[str],
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Pause a task and persist user clarification questions."""

    return AgentToolService(ctx.context).request_clarification(
        task_id=task_id,
        questions=questions,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def write_final_report(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    final_status: str,
    summary: str,
    rationale_summary: str | None = None,
    decisions: list[dict[str, Any]] | None = None,
    plan: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentToolResult:
    """Write final report and replay artifacts before terminal status."""

    return AgentToolService(ctx.context).write_final_report(
        task_id=task_id,
        final_status=final_status,
        summary=summary,
        rationale_summary=rationale_summary,
        decisions=decisions,
        plan=plan,
        metadata=metadata,
    )


@function_tool(strict_mode=False)
def call_plc_dev(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    worker_config: dict[str, Any] | WorkerConfig | None = None,
) -> AgentToolResult:
    """Generate or update PLC implementation artifacts for a classified task."""

    return AgentToolService(ctx.context).call_plc_dev(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        worker_config=worker_config,
    )


@function_tool(strict_mode=False)
def call_plc_test(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    worker_config: dict[str, Any] | WorkerConfig | None = None,
) -> AgentToolResult:
    """Run PLC test worker for the task's current code and requirements."""

    return AgentToolService(ctx.context).call_plc_test(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        worker_config=worker_config,
    )


@function_tool(strict_mode=False)
def call_plc_formal(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    worker_config: dict[str, Any] | WorkerConfig | None = None,
) -> AgentToolResult:
    """Run formal verification worker for the current PLC code."""

    return AgentToolService(ctx.context).call_plc_formal(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        worker_config=worker_config,
    )


@function_tool(strict_mode=False)
def call_plc_repair(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    worker_config: dict[str, Any] | WorkerConfig | None = None,
) -> AgentToolResult:
    """Run PLC repair worker using current code and latest failure evidence."""

    return AgentToolService(ctx.context).call_plc_repair(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        worker_config=worker_config,
    )


@function_tool(strict_mode=False)
def run_parallel_workers(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    workers: list[str],
    objectives: list[str] | None = None,
    worker_configs: list[dict[str, Any] | None] | None = None,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Dispatch a guarded parallel batch of non-repair PLC workers."""

    if worker_configs is not None and len(worker_configs) != len(workers):
        return AgentToolService(ctx.context)._rejected_result(
            tool_name="run_parallel_workers",
            task_id=task_id,
            code="worker_config_count_mismatch",
            message="worker_configs must match workers length",
            details={
                "workers": len(workers),
                "worker_configs": len(worker_configs),
            },
        )

    requests = [
        ParallelWorkerRequest(
            worker_type=worker,
            objective=objectives[index] if objectives and index < len(objectives) else None,
            worker_config=(
                worker_configs[index] if worker_configs and index < len(worker_configs) else None
            ),
        )
        for index, worker in enumerate(workers)
    ]
    return AgentToolService(ctx.context).run_parallel_workers(
        task_id=task_id,
        requests=requests,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def read_artifact(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    artifact_id: str,
    mode: str = "summary",
    max_chars: int | None = None,
) -> AgentToolResult:
    """Read artifact metadata or bounded UTF-8 content for one task artifact."""

    return AgentToolService(ctx.context).read_artifact(
        task_id=task_id,
        artifact_id=artifact_id,
        mode=mode,
        max_chars=max_chars,
    )


@function_tool(strict_mode=False)
def run_quality_gate(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Run and persist Quality Gate assessment for a task."""

    return AgentToolService(ctx.context).run_quality_gate(
        task_id=task_id,
        rationale_summary=rationale_summary,
    )


@function_tool(strict_mode=False)
def record_validation_report(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    validation_type: str,
    status: str,
    summary: str,
    read_paths: list[str] | None = None,
    failure_ids: list[str] | None = None,
    details: dict[str, Any] | None = None,
    command: str | None = None,
    rationale_summary: str | None = None,
) -> AgentToolResult:
    """Record a Main Agent validation report and update task validation state."""

    return AgentToolService(ctx.context).record_validation_report(
        task_id=task_id,
        validation_type=validation_type,
        status=status,
        summary=summary,
        read_paths=read_paths,
        failure_ids=failure_ids,
        details=details,
        command=command,
        rationale_summary=rationale_summary,
    )


def get_main_agent_tools() -> list[Any]:
    """Return SDK function tools for Main Agent registration."""

    return [
        update_plan,
        request_clarification,
        call_plc_dev,
        call_plc_test,
        call_plc_formal,
        call_plc_repair,
        run_parallel_workers,
        run_quality_gate,
        write_final_report,
    ]


def get_main_agent_tool_specs() -> list[dict[str, Any]]:
    """Return OpenAI-compatible Chat Completions tool definitions."""

    return [
        _tool_spec(
            "update_plan",
            "Persist a public execution plan and move the task into planning.",
            {
                "task_id": {"type": "string"},
                "summary": {"type": "string"},
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "normalized_goal": {"type": "string"},
                "task_type": {
                    "type": "string",
                    "enum": [
                        TaskType.QA.value,
                        TaskType.NEW_PLC_DEVELOPMENT.value,
                        TaskType.MODIFY_EXISTING_CODE.value,
                        TaskType.TEST_EXISTING_CODE.value,
                        TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
                        TaskType.REPAIR_EXISTING_CODE.value,
                        TaskType.PROJECT_LEVEL_DEVELOPMENT.value,
                    ],
                },
                "requires_test": {"type": "boolean"},
                "requires_formal": {"type": "boolean"},
            },
            ["task_id", "summary"],
        ),
        _tool_spec(
            "request_clarification",
            "Persist required user clarification questions and pause the task.",
            {
                "task_id": {"type": "string"},
                "questions": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string"},
                                    "reason": {"type": "string"},
                                    "required": {"type": "boolean"},
                                },
                                "required": ["question"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                },
                "rationale_summary": {"type": "string"},
            },
            ["task_id", "questions"],
        ),
        _worker_tool_spec("call_plc_dev", "Generate or update PLC workspace files."),
        _worker_tool_spec("call_plc_test", "Run PLC tests for current code."),
        _worker_tool_spec("call_plc_formal", "Run formal verification for current code."),
        _worker_tool_spec("call_plc_repair", "Repair current code using failure evidence."),
        _tool_spec(
            "run_parallel_workers",
            "Dispatch a guarded batch of non-repair PLC workers.",
            {
                "task_id": {"type": "string"},
                "workers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "objectives": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "worker_configs": {
                    "type": "array",
                    "items": _worker_config_schema(),
                },
                "rationale_summary": {"type": "string"},
            },
            ["task_id", "workers"],
        ),
        _tool_spec(
            "run_quality_gate",
            "Run and persist Quality Gate assessment.",
            {
                "task_id": {"type": "string"},
                "rationale_summary": {"type": "string"},
            },
            ["task_id"],
        ),
        _tool_spec(
            "write_final_report",
            "Write final report and Main Agent replay files.",
            {
                "task_id": {"type": "string"},
                "final_status": {"type": "string", "enum": list(TERMINAL_STATUS_VALUES)},
                "summary": {"type": "string"},
                "rationale_summary": {"type": "string"},
                "decisions": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "plan": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "metadata": {"type": "object", "additionalProperties": True},
            },
            ["task_id", "final_status", "summary"],
        ),
    ]


def call_main_agent_tool(
    context: AgentToolContext,
    tool_name: str,
    arguments: dict[str, Any],
) -> AgentToolResult:
    """Invoke a Main Agent tool by Chat Completions tool-call name."""

    service = AgentToolService(context)
    tool_arguments = dict(arguments)
    if tool_name == "update_plan":
        return service.update_plan(**tool_arguments)
    if tool_name == "request_clarification":
        return service.request_clarification(**tool_arguments)
    if tool_name == "call_plc_dev":
        return service.call_plc_dev(**tool_arguments)
    if tool_name == "call_plc_test":
        return service.call_plc_test(**tool_arguments)
    if tool_name == "call_plc_formal":
        return service.call_plc_formal(**tool_arguments)
    if tool_name == "call_plc_repair":
        return service.call_plc_repair(**tool_arguments)
    if tool_name == "run_parallel_workers":
        workers = tool_arguments.pop("workers")
        objectives = tool_arguments.pop("objectives", None)
        worker_configs = tool_arguments.pop("worker_configs", None)
        if worker_configs is not None and len(worker_configs) != len(workers):
            return service._rejected_result(
                tool_name="run_parallel_workers",
                task_id=tool_arguments["task_id"],
                task=service._get_task(tool_arguments["task_id"]),
                code="worker_config_count_mismatch",
                message="worker_configs must match workers length",
                details={
                    "workers": len(workers),
                    "worker_configs": len(worker_configs),
                },
            )
        return service.run_parallel_workers(
            requests=[
                ParallelWorkerRequest(
                    worker_type=worker,
                    objective=objectives[index] if objectives and index < len(objectives) else None,
                    worker_config=(
                        worker_configs[index] if worker_configs and index < len(worker_configs) else None
                    ),
                )
                for index, worker in enumerate(workers)
            ],
            **tool_arguments,
        )
    if tool_name == "run_quality_gate":
        return service.run_quality_gate(**tool_arguments)
    if tool_name == "write_final_report":
        return service.write_final_report(**tool_arguments)
    return AgentToolResult(
        tool=tool_name,
        status=ToolStatus.REJECTED,
        summary=f"Unknown Main Agent tool: {tool_name}",
        violation=ToolViolation(
            code="unknown_tool",
            message=f"Unknown Main Agent tool: {tool_name}",
        ),
    )


@function_tool(strict_mode=False)
def list_files(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    path: str = ".",
    recursive: bool = False,
    max_entries: int = 200,
) -> AgentToolResult:
    """List files in the configured workspace."""

    return AgentToolService(ctx.context).list_files(
        task_id=task_id,
        path=path,
        recursive=recursive,
        max_entries=max_entries,
    )


@function_tool(strict_mode=False)
def read_file(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    path: str,
    max_chars: int | None = None,
) -> AgentToolResult:
    """Read bounded UTF-8 text from a workspace file."""

    return AgentToolService(ctx.context).read_file(
        task_id=task_id,
        path=path,
        max_chars=max_chars,
    )


@function_tool(strict_mode=False)
def glob(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    pattern: str,
    path: str = ".",
    max_entries: int = 200,
) -> AgentToolResult:
    """Find workspace files matching a glob pattern."""

    return AgentToolService(ctx.context).glob(
        task_id=task_id,
        pattern=pattern,
        path=path,
        max_entries=max_entries,
    )


@function_tool(strict_mode=False)
def grep(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    pattern: str,
    path: str = ".",
    include: str | None = None,
    max_matches: int = 200,
) -> AgentToolResult:
    """Search UTF-8 workspace files for literal text."""

    return AgentToolService(ctx.context).grep(
        task_id=task_id,
        pattern=pattern,
        path=path,
        include=include,
        max_matches=max_matches,
    )


@function_tool(strict_mode=False)
def write_file(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    path: str,
    content: str,
    create_dirs: bool = False,
) -> AgentToolResult:
    """Write UTF-8 text to a workspace file."""

    return AgentToolService(ctx.context).write_file(
        task_id=task_id,
        path=path,
        content=content,
        create_dirs=create_dirs,
    )


@function_tool(strict_mode=False)
def apply_patch(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    patch: str,
    cwd: str = ".",
) -> AgentToolResult:
    """Apply a unified patch in the configured workspace."""

    return AgentToolService(ctx.context).apply_patch(
        task_id=task_id,
        patch=patch,
        cwd=cwd,
    )


@function_tool(strict_mode=False)
def exec_command(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    command: str,
    cwd: str = ".",
    timeout_seconds: int | None = None,
) -> AgentToolResult:
    """Run a shell command in the configured workspace."""

    return AgentToolService(ctx.context).exec_command(
        task_id=task_id,
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )


@function_tool(strict_mode=False)
def git_status(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    cwd: str = ".",
) -> AgentToolResult:
    """Read git status for the configured workspace."""

    return AgentToolService(ctx.context).git_status(task_id=task_id, cwd=cwd)


@function_tool(strict_mode=False)
def write_artifact(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    name: str,
    content: Any,
    summary: str,
    artifact_type: str = ArtifactType.MISC.value,
    mime_type: str | None = None,
) -> AgentToolResult:
    """Write a Router artifact for durable agent evidence."""

    return AgentToolService(ctx.context).write_artifact(
        task_id=task_id,
        name=name,
        content=content,
        summary=summary,
        artifact_type=artifact_type,
        mime_type=mime_type,
    )


@function_tool(strict_mode=False)
def register_workspace_file(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    path: str,
    artifact_type: str,
    summary: str,
    file_role: str | None = None,
    mime_type: str | None = None,
) -> AgentToolResult:
    """Register an existing workspace file as a Router artifact."""

    return AgentToolService(ctx.context).register_workspace_file(
        task_id=task_id,
        path=path,
        artifact_type=artifact_type,
        summary=summary,
        file_role=file_role,
        mime_type=mime_type,
    )


@function_tool(strict_mode=False)
def plc_dev(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    target_language: str | None = None,
    template: str | None = None,
    language_hint: str | None = None,
    enable_socratic_spec: bool | None = None,
    socratic_skip: bool | None = None,
    compiler_type: str | None = None,
    rpc_pipeline: list[str] | None = None,
    llm: dict[str, Any] | None = None,
) -> AgentToolResult:
    """Generate or update PLC workspace files with direct worker controls."""

    return AgentToolService(ctx.context).plc_dev(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        target_language=target_language,
        template=template,
        language_hint=language_hint,
        enable_socratic_spec=enable_socratic_spec,
        socratic_skip=socratic_skip,
        compiler_type=compiler_type,
        rpc_pipeline=rpc_pipeline,
        llm=llm,
    )


@function_tool(strict_mode=False)
def plc_test(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    fuzz_method: str | None = None,
    case_count: int | None = None,
    enable_fuzz_test: bool | None = None,
    llm: dict[str, Any] | None = None,
) -> AgentToolResult:
    """Run PLC tests with direct worker controls."""

    return AgentToolService(ctx.context).plc_test(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        fuzz_method=fuzz_method,
        case_count=case_count,
        enable_fuzz_test=enable_fuzz_test,
        llm=llm,
    )


@function_tool(strict_mode=False)
def plc_formal(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    compiler_type: str | None = None,
    properties: Any | None = None,
    natural_language_requirements: str | None = None,
    llm: dict[str, Any] | None = None,
) -> AgentToolResult:
    """Run PLC formal verification with direct worker controls."""

    return AgentToolService(ctx.context).plc_formal(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        compiler_type=compiler_type,
        properties=properties,
        natural_language_requirements=natural_language_requirements,
        llm=llm,
    )


@function_tool(strict_mode=False)
def plc_repair(
    ctx: RunContextWrapper[AgentToolContext],
    task_id: str,
    objective: str | None = None,
    rationale_summary: str | None = None,
    repair_source: str | None = None,
    repair_targets: list[str] | None = None,
    repair_failure_notes: str | None = None,
    compiler_type: str | None = None,
    llm: dict[str, Any] | None = None,
) -> AgentToolResult:
    """Run PLC repair with direct worker controls."""

    return AgentToolService(ctx.context).plc_repair(
        task_id=task_id,
        objective=objective,
        rationale_summary=rationale_summary,
        repair_source=repair_source,
        repair_targets=repair_targets,
        repair_failure_notes=repair_failure_notes,
        compiler_type=compiler_type,
        llm=llm,
    )


def _worker_config_from_fields(**fields: Any) -> dict[str, Any] | None:
    values = {
        key: value
        for key, value in fields.items()
        if key != "llm" and value is not None
    }
    llm = fields.get("llm")
    if llm is not None:
        if isinstance(llm, BaseModel):
            llm_payload: Any = llm.model_dump(mode="json", exclude_none=True)
        elif isinstance(llm, dict):
            llm_payload = {
                key: value
                for key, value in llm.items()
                if value is not None
            }
        else:
            llm_payload = llm
        if not isinstance(llm_payload, dict) or llm_payload:
            values["llm"] = llm_payload
    return values or None


def _llm_config_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "model": {"type": "string"},
            "base_url": {"type": "string"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "timeout_seconds": {"type": "integer", "minimum": 1},
            "max_retries": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    }


def _direct_worker_tool_properties(worker: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "task_id": {"type": "string"},
        "objective": {"type": "string"},
        "rationale_summary": {"type": "string"},
        "llm": _llm_config_tool_schema(),
    }
    if worker == WorkerType.PLC_DEV.value:
        properties.update(
            {
                "target_language": {
                    "type": "string",
                    "enum": [item.value for item in WorkerTargetLanguage],
                },
                "template": {"type": "string"},
                "language_hint": {"type": "string"},
                "enable_socratic_spec": {"type": "boolean"},
                "socratic_skip": {"type": "boolean"},
                "compiler_type": {
                    "type": "string",
                    "enum": [item.value for item in WorkerCompilerType],
                },
                "rpc_pipeline": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [item.value for item in WorkerPipelineStage],
                    },
                },
            }
        )
    elif worker == WorkerType.PLC_TEST.value:
        properties.update(
            {
                "fuzz_method": {
                    "type": "string",
                    "enum": [item.value for item in WorkerFuzzMethod],
                },
                "case_count": {"type": "integer", "minimum": 1},
                "enable_fuzz_test": {"type": "boolean"},
            }
        )
    elif worker == WorkerType.PLC_FORMAL.value:
        properties.update(
            {
                "compiler_type": {
                    "type": "string",
                    "enum": [item.value for item in WorkerCompilerType],
                },
                "properties": {
                    "type": ["object", "array", "string", "number", "boolean", "null"],
                },
                "natural_language_requirements": {"type": "string"},
            }
        )
    elif worker == WorkerType.PLC_REPAIR.value:
        properties.update(
            {
                "repair_source": {
                    "type": "string",
                    "enum": [item.value for item in WorkerRepairSource],
                },
                "repair_targets": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [item.value for item in WorkerRepairTarget],
                    },
                },
                "repair_failure_notes": {"type": "string"},
                "compiler_type": {
                    "type": "string",
                    "enum": [item.value for item in WorkerCompilerType],
                },
            }
        )
    return properties


GENERIC_MAIN_AGENT_TOOL_REGISTRY = (
    MainAgentToolDefinition(
        name="list_files",
        description="List files or directories inside the configured workspace root.",
        properties={
            "task_id": {"type": "string"},
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
            "max_entries": {"type": "integer", "minimum": 1},
        },
        required=("task_id",),
        sdk_tool=list_files,
        executor_method="list_files",
    ),
    MainAgentToolDefinition(
        name="read_file",
        description="Read bounded UTF-8 text from a file inside the workspace.",
        properties={
            "task_id": {"type": "string"},
            "path": {"type": "string"},
            "max_chars": {"type": "integer", "minimum": 1},
        },
        required=("task_id", "path"),
        sdk_tool=read_file,
        executor_method="read_file",
    ),
    MainAgentToolDefinition(
        name="glob",
        description="Find workspace files matching a glob pattern.",
        properties={
            "task_id": {"type": "string"},
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "max_entries": {"type": "integer", "minimum": 1},
        },
        required=("task_id", "pattern"),
        sdk_tool=glob,
        executor_method="glob",
    ),
    MainAgentToolDefinition(
        name="grep",
        description="Search UTF-8 workspace files for literal text.",
        properties={
            "task_id": {"type": "string"},
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "include": {"type": "string"},
            "max_matches": {"type": "integer", "minimum": 1},
        },
        required=("task_id", "pattern"),
        sdk_tool=grep,
        executor_method="grep",
    ),
    MainAgentToolDefinition(
        name="write_file",
        description="Write UTF-8 text to a file inside the workspace.",
        properties={
            "task_id": {"type": "string"},
            "path": {"type": "string"},
            "content": {"type": "string"},
            "create_dirs": {"type": "boolean"},
        },
        required=("task_id", "path", "content"),
        sdk_tool=write_file,
        executor_method="write_file",
    ),
    MainAgentToolDefinition(
        name="apply_patch",
        description="Apply a unified patch in the configured workspace.",
        properties={
            "task_id": {"type": "string"},
            "patch": {"type": "string"},
            "cwd": {"type": "string"},
        },
        required=("task_id", "patch"),
        sdk_tool=apply_patch,
        executor_method="apply_patch",
    ),
    MainAgentToolDefinition(
        name="exec_command",
        description=(
            "Run a shell command in the configured workspace and return bounded "
            "output."
        ),
        properties={
            "task_id": {"type": "string"},
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1},
        },
        required=("task_id", "command"),
        sdk_tool=exec_command,
        executor_method="exec_command",
    ),
    MainAgentToolDefinition(
        name="git_status",
        description="Read git branch and short working tree status.",
        properties={
            "task_id": {"type": "string"},
            "cwd": {"type": "string"},
        },
        required=("task_id",),
        sdk_tool=git_status,
        executor_method="git_status",
    ),
    MainAgentToolDefinition(
        name="plc_dev",
        description="Generate or update PLC workspace files with direct worker controls.",
        properties=_direct_worker_tool_properties(WorkerType.PLC_DEV.value),
        required=("task_id",),
        sdk_tool=plc_dev,
        executor_method="plc_dev",
    ),
    MainAgentToolDefinition(
        name="plc_test",
        description="Run PLC tests with direct worker controls.",
        properties=_direct_worker_tool_properties(WorkerType.PLC_TEST.value),
        required=("task_id",),
        sdk_tool=plc_test,
        executor_method="plc_test",
    ),
    MainAgentToolDefinition(
        name="plc_formal",
        description="Run PLC formal verification with direct worker controls.",
        properties=_direct_worker_tool_properties(WorkerType.PLC_FORMAL.value),
        required=("task_id",),
        sdk_tool=plc_formal,
        executor_method="plc_formal",
    ),
    MainAgentToolDefinition(
        name="plc_repair",
        description="Run PLC repair with direct worker controls.",
        properties=_direct_worker_tool_properties(WorkerType.PLC_REPAIR.value),
        required=("task_id",),
        sdk_tool=plc_repair,
        executor_method="plc_repair",
    ),
    MainAgentToolDefinition(
        name="run_quality_gate",
        description="Run and persist the Quality Gate assessment for final delivery.",
        properties={
            "task_id": {"type": "string"},
            "rationale_summary": {"type": "string"},
        },
        required=("task_id",),
        sdk_tool=run_quality_gate,
        executor_method="run_quality_gate",
    ),
    MainAgentToolDefinition(
        name="record_validation_report",
        description=(
            "Record Main Agent fallback validation evidence and update task "
            "validation state."
        ),
        properties={
            "task_id": {"type": "string"},
            "validation_type": {
                "type": "string",
                "enum": ["test", "compile", "formal"],
            },
            "status": {"type": "string", "enum": ["passed", "failed"]},
            "summary": {"type": "string"},
            "read_paths": {
                "type": "array",
                "items": {"type": "string"},
            },
            "failure_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "details": {"type": "object"},
            "command": {"type": "string"},
            "rationale_summary": {"type": "string"},
        },
        required=("task_id", "validation_type", "status", "summary"),
        sdk_tool=record_validation_report,
        executor_method="record_validation_report",
    ),
)
GENERIC_MAIN_AGENT_TOOL_BY_NAME = {
    definition.name: definition for definition in GENERIC_MAIN_AGENT_TOOL_REGISTRY
}


def get_main_agent_tool_registry() -> tuple[MainAgentToolDefinition, ...]:
    """Return registry definitions for the generic Main Agent tools."""

    return GENERIC_MAIN_AGENT_TOOL_REGISTRY


def get_main_agent_tool_names() -> tuple[str, ...]:
    """Return the default Codex-like Main Agent tool names."""

    return tuple(definition.name for definition in GENERIC_MAIN_AGENT_TOOL_REGISTRY)


def get_main_agent_tools() -> list[Any]:  # type: ignore[no-redef]
    """Return SDK function tools for the generic Main Agent."""

    return [definition.sdk_tool for definition in GENERIC_MAIN_AGENT_TOOL_REGISTRY]


def get_main_agent_tool_specs() -> list[dict[str, Any]]:  # type: ignore[no-redef]
    """Return OpenAI-compatible Chat Completions tool definitions."""

    return [
        _tool_spec(
            definition.name,
            definition.description,
            definition.properties,
            list(definition.required),
        )
        for definition in GENERIC_MAIN_AGENT_TOOL_REGISTRY
    ]


def call_main_agent_tool(  # type: ignore[no-redef]
    context: AgentToolContext,
    tool_name: str,
    arguments: dict[str, Any],
) -> AgentToolResult:
    """Invoke a generic Main Agent tool by Chat Completions tool-call name."""

    service = AgentToolService(context)
    tool_arguments = dict(arguments)
    definition = GENERIC_MAIN_AGENT_TOOL_BY_NAME.get(tool_name)
    if definition is not None:
        executor = getattr(service, definition.executor_method)
        return executor(**tool_arguments)
    return AgentToolResult(
        tool=tool_name,
        status=ToolStatus.REJECTED,
        summary=f"Unknown Main Agent tool: {tool_name}",
        violation=ToolViolation(
            code="unknown_tool",
            message=f"Unknown Main Agent tool: {tool_name}",
        ),
    )


def _proposed_worker_input_paths(
    task: TaskState,
    worker_type: WorkerType | str,
) -> list[str]:
    worker = _value(worker_type)
    files = task.current_files
    if worker == WorkerType.PLC_DEV.value:
        return [
            path
            for path in (files.raw_user_request, files.requirements)
            if path is not None
        ][:1]
    if worker in {WorkerType.PLC_TEST.value, WorkerType.PLC_FORMAL.value}:
        return [
            path
            for path in (files.requirements, files.current_code)
            if path is not None
        ]
    if worker == WorkerType.PLC_REPAIR.value:
        return [
            path
            for path in (
                files.current_code,
                files.latest_test_report,
                files.latest_failing_trace,
                files.latest_formal_report,
                files.latest_counterexample,
            )
            if path is not None
        ]
    return []


def _tool_spec(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _worker_tool_spec(name: str, description: str) -> dict[str, Any]:
    return _tool_spec(
        name,
        description,
        {
            "task_id": {"type": "string"},
            "objective": {"type": "string"},
            "rationale_summary": {"type": "string"},
            "worker_config": _worker_config_schema(),
        },
        ["task_id"],
    )


def _worker_config_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target_language": {
                "type": "string",
                "enum": [item.value for item in WorkerTargetLanguage],
            },
            "template": {"type": "string"},
            "language_hint": {"type": "string"},
            "enable_socratic_spec": {"type": "boolean"},
            "socratic_skip": {"type": "boolean"},
            "compiler_type": {
                "type": "string",
                "enum": [item.value for item in WorkerCompilerType],
            },
            "rpc_pipeline": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [item.value for item in WorkerPipelineStage],
                },
            },
            "repair_source": {
                "type": "string",
                "enum": [item.value for item in WorkerRepairSource],
            },
            "repair_targets": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [item.value for item in WorkerRepairTarget],
                },
            },
            "repair_failure_notes": {"type": "string"},
            "properties": {
                "type": ["object", "array", "string", "number", "boolean", "null"],
            },
            "natural_language_requirements": {"type": "string"},
            "fuzz_method": {
                "type": "string",
                "enum": [item.value for item in WorkerFuzzMethod],
            },
            "case_count": {"type": "integer", "minimum": 1},
            "enable_fuzz_test": {"type": "boolean"},
            "llm": {
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "base_url": {"type": "string"},
                    "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                    "timeout_seconds": {"type": "integer", "minimum": 1},
                    "max_retries": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def _artifact_type_from_tool(value: str | ArtifactType) -> ArtifactType:
    aliases = {
        "code": ArtifactType.PLC_CODE,
        "plc": ArtifactType.PLC_CODE,
        "st": ArtifactType.PLC_CODE,
    }
    normalized = _value(value)
    if isinstance(normalized, str):
        normalized = normalized.strip().lower()
        if normalized in aliases:
            return aliases[normalized]
    try:
        return ArtifactType(normalized)
    except ValueError:
        allowed = ", ".join(sorted(item.value for item in ArtifactType))
        raise ValueError(
            f"unsupported artifact_type {value!r}; use one of: {allowed}"
        )


def _worker_input_signature(worker_input: WorkerInput) -> tuple[str, ...]:
    signature = (worker_input.metadata or {}).get("input_signature")
    if isinstance(signature, dict):
        return (
            json.dumps(
                signature,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return tuple(
        sorted(
            [
                _value(worker_input.worker_type),
                *worker_input.input_paths,
            ]
        )
    )


def _worker_failure_types(result: WorkerResult) -> tuple[str, ...]:
    execution_status = _value(result.execution_status)
    outcome_status = _value(result.outcome.status)
    if execution_status != WorkerExecutionStatus.COMPLETED.value:
        if result.error is not None:
            return (result.error.error_code,)
        return ("worker_execution_failed",)
    if outcome_status != WorkerOutcomeStatus.FAILED.value:
        return ()
    sources = sorted({_value(failure.source) for failure in result.failures})
    if sources:
        return tuple(sources)
    if result.error is not None:
        return (result.error.error_code,)
    return (f"{_value(result.worker_type)}_failed",)


def _bounded_output(value: Any, limit: int = DEFAULT_AGENT_TOOL_OUTPUT_MAX_CHARS) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 0)]}..."


def _current_file_field_for_path(path: str) -> str | None:
    lower = path.lower()
    name = Path(path).name.lower()
    if lower.startswith(".router/requests/"):
        return "raw_user_request"
    if lower.endswith((".st", ".scl", ".fbd")) or (
        lower.endswith(".xml") and "io_contract" not in lower
    ):
        return "current_code"
    if "requirements" in lower:
        return "requirements"
    if "io_contract" in lower:
        return "current_io_contract"
    if "test_cases" in lower:
        return "latest_test_cases"
    if "test_report" in lower:
        return "latest_test_report"
    if "failing_trace" in lower:
        return "latest_failing_trace"
    if "formal_properties" in lower:
        return "latest_formal_properties"
    if "formal_report" in lower:
        return "latest_formal_report"
    if "counterexample" in lower:
        return "latest_counterexample"
    if name.endswith((".diff", ".patch")) or "patch" in lower:
        return "latest_patch"
    if "repair_summary" in lower:
        return "latest_repair_summary"
    if "gate_report" in lower:
        return "latest_gate_report"
    if "final_report" in lower:
        return "final_report"
    if "replay_log" in lower or "main_agent_log" in lower:
        return "main_agent_log"
    return None


def _append_unique_path(paths: list[str], path: str) -> list[str]:
    return paths if path in paths else [*paths, path]


def _is_report_path(path: str) -> bool:
    return _current_file_field_for_path(path) in {
        "latest_test_report",
        "latest_failing_trace",
        "latest_formal_report",
        "latest_counterexample",
        "latest_patch",
        "latest_repair_summary",
        "latest_gate_report",
        "final_report",
        "main_agent_log",
    }


def _json_safe_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, default=str))


def _is_intake_or_unknown_task(task: TaskState) -> bool:
    return (
        _value(task.status) == TaskStatus.CREATED.value
        or _value(task.phase) == TaskPhase.INTAKE.value
        or _value(task.task_type) == TaskType.UNKNOWN.value
    )


def _gate_updates_for_worker(worker_type: str) -> dict[str, Any]:
    if worker_type == WorkerType.PLC_TEST.value:
        return {"test_required": True}
    if worker_type == WorkerType.PLC_FORMAL.value:
        return {"formal_required": True}
    return {}


def _domain_task_type_for_worker(worker_type: str) -> str:
    if worker_type == WorkerType.PLC_TEST.value:
        return TaskType.TEST_EXISTING_CODE.value
    if worker_type == WorkerType.PLC_FORMAL.value:
        return TaskType.FORMAL_VERIFY_EXISTING_CODE.value
    if worker_type == WorkerType.PLC_REPAIR.value:
        return TaskType.REPAIR_EXISTING_CODE.value
    return TaskType.NEW_PLC_DEVELOPMENT.value


def _clarification_question_from_tool(
    value: dict[str, Any] | str,
    *,
    now: Any,
) -> ClarificationQuestion:
    if isinstance(value, str):
        question = value
        reason = "Main Agent requested clarification before continuing."
        required = True
    else:
        question = str(value.get("question") or "").strip()
        reason = str(
            value.get("reason")
            or "Main Agent requested clarification before continuing."
        )
        required = bool(value.get("required", True))
    return ClarificationQuestion(
        question_id=prefixed_id("question"),
        question=question,
        reason=reason,
        required=required,
        status="open",
        asked_at=now,
    )


def _normalized_task_type_from_tool(
    value: str | None,
    *,
    current_task_type: str,
) -> str:
    allowed = {item.value for item in TaskType}
    if value in allowed and value != TaskType.UNKNOWN.value:
        return str(value)

    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "qa": TaskType.QA.value,
        "question_answering": TaskType.QA.value,
        "analysis": TaskType.QA.value,
        "development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "plc_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "new_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l0_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l1_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l2_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l3_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "l4_development": TaskType.NEW_PLC_DEVELOPMENT.value,
        "modify": TaskType.MODIFY_EXISTING_CODE.value,
        "modification": TaskType.MODIFY_EXISTING_CODE.value,
        "test": TaskType.TEST_EXISTING_CODE.value,
        "testing": TaskType.TEST_EXISTING_CODE.value,
        "formal": TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
        "formal_verification": TaskType.FORMAL_VERIFY_EXISTING_CODE.value,
        "repair": TaskType.REPAIR_EXISTING_CODE.value,
        "fix": TaskType.REPAIR_EXISTING_CODE.value,
        "project": TaskType.PROJECT_LEVEL_DEVELOPMENT.value,
    }
    if normalized in aliases:
        return aliases[normalized]
    if "repair" in normalized or "fix" in normalized:
        return TaskType.REPAIR_EXISTING_CODE.value
    if "formal" in normalized:
        return TaskType.FORMAL_VERIFY_EXISTING_CODE.value
    if "test" in normalized:
        return TaskType.TEST_EXISTING_CODE.value
    if "modify" in normalized or "change" in normalized:
        return TaskType.MODIFY_EXISTING_CODE.value
    if "develop" in normalized or "plc" in normalized:
        return TaskType.NEW_PLC_DEVELOPMENT.value
    if current_task_type in allowed and current_task_type != TaskType.UNKNOWN.value:
        return current_task_type
    return TaskType.NEW_PLC_DEVELOPMENT.value


def _main_agent_decisions_from_tool(
    decisions: list[dict[str, Any]] | None,
) -> list[MainAgentDecision]:
    output: list[MainAgentDecision] = []
    for index, decision in enumerate(decisions or [], start=1):
        if not isinstance(decision, dict):
            decision = {"summary": str(decision)}
        normalized = {
            "decision_type": str(
                decision.get("decision_type")
                or decision.get("type")
                or "tool_loop_decision"
            ),
            "summary": str(
                decision.get("summary")
                or decision.get("message")
                or decision.get("action")
                or f"Decision {index}"
            ),
            "action": decision.get("action"),
            "tool_name": decision.get("tool_name") or decision.get("tool"),
            "artifact_refs": decision.get("artifact_refs") or [],
            "details": _json_object(decision.get("details") or {}),
        }
        output.append(MainAgentDecision.model_validate(normalized))
    return output


def _main_agent_plan_from_tool(
    plan: list[dict[str, Any]] | None,
) -> list[MainAgentPlanStep]:
    output: list[MainAgentPlanStep] = []
    for index, step in enumerate(plan or [], start=1):
        if not isinstance(step, dict):
            step = {"action": str(step)}
        normalized = {
            "order": step.get("order") or index,
            "action": str(
                step.get("action")
                or step.get("summary")
                or step.get("title")
                or f"Plan step {index}"
            ),
            "status": str(step.get("status") or "planned"),
            "reason": step.get("reason"),
            "worker_type": step.get("worker_type"),
            "tool_name": step.get("tool_name") or step.get("tool"),
        }
        output.append(MainAgentPlanStep.model_validate(normalized))
    return output


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": _json_value(value)}
    return {str(key): _json_value(item) for key, item in value.items()}


def _task_with_validation_report(
    task: TaskState,
    *,
    validation_type: str,
    status: str,
    report_path: str,
    failure_ids: list[str],
    now: Any,
) -> TaskState:
    current_files = task.current_files
    file_updates: dict[str, Any] = {
        "all_paths": _append_unique_path(current_files.all_paths, report_path)
    }
    gate_updates: dict[str, Any] = {}
    failures = task.failures

    if validation_type == "test":
        gate_updates["test_required"] = True
    elif validation_type == "formal":
        gate_updates["formal_required"] = True

    if status == "passed":
        if validation_type == "test":
            file_updates["latest_test_report"] = report_path
            gate_updates.update(
                {
                    "latest_test_passed": True,
                    "regression_required": False,
                }
            )
        elif validation_type == "formal":
            file_updates["latest_formal_report"] = report_path
            gate_updates.update(
                {
                    "latest_formal_passed": True,
                    "formal_regression_required": False,
                }
            )
        failures = _resolve_validation_failures(
            failures,
            validation_type=validation_type,
            failure_ids=set(failure_ids),
            resolved_by_path=report_path,
            resolved_at=now,
        )

    has_blocking_failure = _has_open_blocking_failure(failures)
    gate_updates["has_blocking_failure"] = has_blocking_failure
    gate_updates["can_finish_as_success"] = False
    phase = task.phase
    if (
        not has_blocking_failure
        and _value(task.phase)
        in {
            TaskPhase.TESTING.value,
            TaskPhase.FORMAL_VERIFYING.value,
            TaskPhase.REPAIRING.value,
            TaskPhase.REGRESSION.value,
        }
    ):
        phase = TaskPhase.QUALITY_GATE.value

    return task.model_copy(
        deep=True,
        update={
            "phase": phase,
            "updated_at": now,
            "current_files": current_files.model_copy(update=file_updates),
            "gates": task.gates.model_copy(update=gate_updates),
            "failures": failures,
        },
    )


def _resolve_validation_failures(
    failures: list[Failure],
    *,
    validation_type: str,
    failure_ids: set[str],
    resolved_by_path: str,
    resolved_at: Any,
) -> list[Failure]:
    source = _validation_failure_source(validation_type)
    resolved: list[Failure] = []
    for failure in failures:
        if _should_resolve_validation_failure(failure, source, failure_ids):
            resolved.append(
                failure.model_copy(
                    update={
                        "status": FailureStatus.RESOLVED.value,
                        "resolved_by_path": resolved_by_path,
                        "resolved_at": resolved_at,
                    }
                )
            )
        else:
            resolved.append(failure)
    return resolved


def _should_resolve_validation_failure(
    failure: Failure,
    source: str,
    failure_ids: set[str],
) -> bool:
    if (
        _value(failure.status) != FailureStatus.OPEN.value
        or _value(failure.severity) != "blocking"
    ):
        return False
    if failure_ids:
        return failure.failure_id in failure_ids
    return _value(failure.source) == source


def _validation_failure_source(validation_type: str) -> str:
    if validation_type == "compile":
        return FailureSource.COMPILE.value
    if validation_type == "formal":
        return FailureSource.FORMAL.value
    return FailureSource.TEST.value


def _has_open_blocking_failure(failures: list[Failure]) -> bool:
    return any(
        _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == "blocking"
        for failure in failures
    )


def _resolved_failure_ids(before: TaskState, after: TaskState) -> list[str]:
    before_by_id = {failure.failure_id: failure for failure in before.failures}
    resolved: list[str] = []
    for failure in after.failures:
        previous = before_by_id.get(failure.failure_id)
        if (
            previous is not None
            and _value(previous.status) == FailureStatus.OPEN.value
            and _value(failure.status) == FailureStatus.RESOLVED.value
        ):
            resolved.append(failure.failure_id)
    return resolved


def _build_main_agent_event(
    *,
    task: TaskState,
    event_type: EventType,
    title: str,
    message: str,
    payload: dict[str, Any],
    created_at: Any,
) -> RouterEvent:
    return RouterEvent(
        schema_version=DEFAULT_SCHEMA_VERSION,
        event_id=new_event_id(),
        task_id=task.task_id,
        seq=0,
        type=event_type,
        source=EventSource(
            type=EventSourceType.MAIN_AGENT,
            id=task.trace.latest_main_agent_run_id,
        ),
        severity=EventSeverity.INFO,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
        ),
        payload=payload,
        created_at=created_at,
    )


def _build_task_event(
    *,
    task: TaskState,
    event_type: EventType,
    title: str,
    message: str,
    payload: dict[str, Any],
    created_at: Any,
) -> RouterEvent:
    return RouterEvent(
        schema_version=DEFAULT_SCHEMA_VERSION,
        event_id=new_event_id(),
        task_id=task.task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.RUNTIME),
        severity=EventSeverity.INFO,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
        ),
        payload=payload,
        created_at=created_at,
    )


def _worker_result_to_tool_result(
    *,
    tool_name: str,
    result: WorkerResult,
    task: TaskState,
    applied: bool,
) -> AgentToolResult:
    status = ToolStatus.APPLIED if applied else ToolStatus.NOOP
    return AgentToolResult(
        tool=tool_name,
        task_id=result.task_id,
        status=status,
        summary=result.summary,
        failures=_failure_summaries(task.failures),
        gate_state=_gate_state_summary(task),
        next_recommended_action=_value(result.next_recommended_action),
        worker_job_id=result.worker_job_id,
        worker_type=_value(result.worker_type),
        execution_status=_value(result.execution_status),
        outcome_status=_value(result.outcome.status),
        read_paths=list(result.read_paths),
        written_paths=list(result.written_paths),
        report_paths=list(result.report_paths),
        error=(
            ToolError(
                error_code=result.error.error_code,
                message=result.error.message,
                retryable=result.error.retryable,
                details=dict(result.error.details or {}),
            )
            if result.error is not None
            else None
        ),
    )


def _artifact_ref_summaries(
    artifacts: list[ArtifactRef],
) -> list[ArtifactRefSummary]:
    return [_artifact_ref_summary(artifact) for artifact in artifacts]


def _artifact_ref_summary(artifact: ArtifactRef) -> ArtifactRefSummary:
    return ArtifactRefSummary(
        artifact_id=artifact.artifact_id,
        type=_value(artifact.type),
        version=artifact.version,
        uri=artifact.uri,
        summary=artifact.summary,
        content_hash=artifact.content_hash,
    )


def _artifact_ref_summary_from_artifact(artifact: Artifact) -> ArtifactRefSummary:
    return ArtifactRefSummary(
        artifact_id=artifact.artifact_id,
        type=_value(artifact.type),
        version=artifact.version,
        uri=artifact.storage.uri,
        summary=artifact.summary,
        content_hash=artifact.storage.content_hash,
    )


def _artifact_read_summary(artifact: Artifact) -> ArtifactReadSummary:
    return ArtifactReadSummary(
        artifact_id=artifact.artifact_id,
        task_id=artifact.task_id,
        type=_value(artifact.type),
        version=artifact.version,
        name=artifact.name,
        summary=artifact.summary,
        uri=artifact.storage.uri,
        mime_type=artifact.storage.mime_type,
        size_bytes=artifact.storage.size_bytes,
        content_hash=artifact.storage.content_hash,
    )


def _failure_summaries(failures: list[Failure]) -> list[FailureSummary]:
    return [
        FailureSummary(
            failure_id=failure.failure_id,
            source=_value(failure.source),
            severity=_value(failure.severity),
            status=_value(failure.status),
            title=failure.title,
            evidence_paths=list(failure.evidence_paths),
        )
        for failure in failures
    ]


def _blocking_failure_ids(failures: list[Failure]) -> list[str]:
    return [
        failure.failure_id
        for failure in failures
        if _value(failure.status) == FailureStatus.OPEN.value
        and _value(failure.severity) == Severity.BLOCKING.value
    ]


def _gate_state_summary(task: TaskState) -> GateStateSummary:
    gates = task.gates
    return GateStateSummary(
        test_required=gates.test_required,
        formal_required=gates.formal_required,
        regression_required=gates.regression_required,
        formal_regression_required=gates.formal_regression_required,
        latest_test_passed=gates.latest_test_passed,
        latest_formal_passed=gates.latest_formal_passed,
        has_blocking_failure=gates.has_blocking_failure,
        can_finish_as_success=gates.can_finish_as_success,
    )


def _trace_context_for_task(task: TaskState) -> TraceContext:
    return TraceContext(
        openai_trace_id=task.trace.openai_trace_id,
        main_agent_run_id=task.trace.latest_main_agent_run_id,
    )


def _build_terminal_task_event(
    *,
    task_id: str,
    final_status: str,
    openai_trace_id: str | None = None,
    main_agent_run_id: str | None = None,
    created_at: Any,
) -> RouterEvent:
    event_type = TERMINAL_EVENT_BY_STATUS[final_status]
    return RouterEvent(
        schema_version=DEFAULT_SCHEMA_VERSION,
        event_id=new_event_id(),
        task_id=task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.RUNTIME),
        severity=(
            EventSeverity.INFO
            if final_status == TaskStatus.SUCCEEDED.value
            else EventSeverity.ERROR
        ),
        visibility=EventVisibility.USER,
        title=f"Task {final_status}",
        message=f"The task was marked {final_status}.",
        correlation=EventCorrelation(
            openai_trace_id=openai_trace_id,
            main_agent_run_id=main_agent_run_id,
        ),
        payload={"task_id": task_id, "status": final_status},
        created_at=created_at,
    )


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    try:
        import json

        json.dumps(value)
    except TypeError:
        return str(value)
    return value
