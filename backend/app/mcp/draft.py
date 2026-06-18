"""Internal draft contracts for LLM-backed PLC MCP worker responses."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.models.router_schema import (
    ArtifactType,
    ArtifactVisibility,
    Assumption,
    ClarificationRequest,
    Diagnostic,
    Failure,
    McpToolName,
    NextRecommendedAction,
    WORKER_TOOL_BY_TYPE,
    WorkerInput,
    WorkerMetrics,
    WorkerOutcome,
    WorkerOutcomeStatus,
    WorkerType,
)


class McpDraftValidationError(ValueError):
    """Raised when an MCP worker draft cannot be accepted."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class McpDraftBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class McpInputArtifactSnapshot(McpDraftBaseModel):
    """Bounded artifact content sent with a WorkerInput to an MCP worker."""

    artifact_id: str
    type: ArtifactType
    version: int = Field(ge=1)
    summary: str | None = None
    uri: str | None = None
    content: str | None = None
    content_truncated: bool = False
    content_chars: int | None = Field(default=None, ge=0)
    mime_type: str | None = None


class McpWorkerRequest(McpDraftBaseModel):
    """Envelope passed from Router to one MCP worker tool."""

    worker_input: WorkerInput
    input_artifacts: list[McpInputArtifactSnapshot] = Field(default_factory=list)


class LlmArtifactWriteDraft(McpDraftBaseModel):
    """Artifact content proposed by an LLM-backed MCP worker."""

    artifact_type: ArtifactType
    version: int = Field(ge=1)
    name: str
    content: JsonValue
    summary: str
    visibility: ArtifactVisibility = ArtifactVisibility.USER
    metadata: dict[str, JsonValue] | None = None
    parent_artifact_ids: list[str] = Field(default_factory=list)
    mime_type: str | None = None


class LlmWorkerDraftOutput(McpDraftBaseModel):
    """MCP worker output before Router persists artifacts and builds WorkerResult."""

    outcome: WorkerOutcome
    summary: str
    artifact_writes: list[LlmArtifactWriteDraft] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    clarification_request: ClarificationRequest | None = None
    metrics: WorkerMetrics = Field(default_factory=WorkerMetrics)
    next_recommended_action: NextRecommendedAction = NextRecommendedAction.NONE
    metadata: dict[str, JsonValue] | None = None


REQUIRED_PASSED_ARTIFACTS_BY_WORKER: dict[str, set[str]] = {
    WorkerType.PLC_DEV.value: {
        ArtifactType.REQUIREMENTS_IR.value,
        ArtifactType.PLC_CODE.value,
        ArtifactType.IO_CONTRACT.value,
    },
    WorkerType.PLC_TEST.value: {ArtifactType.TEST_REPORT.value},
    WorkerType.PLC_FORMAL.value: {ArtifactType.FORMAL_REPORT.value},
    WorkerType.PLC_REPAIR.value: {
        ArtifactType.PATCH.value,
        ArtifactType.PLC_CODE.value,
        ArtifactType.REPAIR_SUMMARY.value,
    },
}

EXPECTED_WORKER_BY_TOOL: dict[str, str] = {
    tool: worker
    for worker, tool in WORKER_TOOL_BY_TYPE.items()
}


def parse_worker_draft_output(raw_output: Any) -> LlmWorkerDraftOutput:
    """Parse MCP response content into an internal worker draft output."""

    if isinstance(raw_output, LlmWorkerDraftOutput):
        return raw_output
    if isinstance(raw_output, str):
        raw_output = _json_object_from_text(raw_output)
    try:
        return LlmWorkerDraftOutput.model_validate(raw_output)
    except ValidationError as exc:
        raise McpDraftValidationError(
            "MCP worker output is not a valid LLM worker draft",
            details={"validation_error": str(exc)},
        ) from exc


def validate_worker_request_tool(
    request: McpWorkerRequest,
    tool_name: McpToolName | str,
) -> None:
    """Ensure a request is being sent to the MCP tool matching its worker type."""

    tool = _value(tool_name)
    expected_worker = EXPECTED_WORKER_BY_TOOL.get(tool)
    actual_worker = _value(request.worker_input.worker_type)
    if expected_worker is None:
        raise McpDraftValidationError(
            f"unsupported MCP tool: {tool!r}",
            details={"mcp_tool": tool},
        )
    if actual_worker != expected_worker:
        raise McpDraftValidationError(
            "WorkerInput worker_type does not match MCP tool",
            details={
                "mcp_tool": tool,
                "expected_worker_type": expected_worker,
                "actual_worker_type": actual_worker,
            },
        )


def validate_worker_draft_output(
    draft: LlmWorkerDraftOutput,
    worker_input: WorkerInput,
) -> None:
    """Validate worker-specific draft rules before artifact persistence."""

    status = _value(draft.outcome.status)
    if status == WorkerOutcomeStatus.PASSED.value:
        _validate_required_artifacts(draft, worker_input)
    if status == WorkerOutcomeStatus.NEED_CLARIFICATION.value:
        if draft.clarification_request is None:
            raise McpDraftValidationError(
                "need_clarification outcome requires clarification_request",
                details={"worker_type": _value(worker_input.worker_type)},
            )


def _validate_required_artifacts(
    draft: LlmWorkerDraftOutput,
    worker_input: WorkerInput,
) -> None:
    worker_type = _value(worker_input.worker_type)
    required = REQUIRED_PASSED_ARTIFACTS_BY_WORKER.get(worker_type, set())
    produced = {
        _value(write.artifact_type)
        for write in draft.artifact_writes
        if _has_content(write.content)
    }
    missing = sorted(required - produced)
    if missing:
        raise McpDraftValidationError(
            "MCP worker passed outcome is missing required artifact drafts",
            details={
                "worker_type": worker_type,
                "missing_artifact_types": missing,
                "produced_artifact_types": sorted(produced),
            },
        )


def _json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise McpDraftValidationError("MCP worker output is empty")
    if stripped.startswith("```"):
        stripped = _strip_json_fence(stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise McpDraftValidationError(
            "MCP worker output is not valid JSON",
            details={"json_error": str(exc)},
        ) from exc
    if not isinstance(parsed, dict):
        raise McpDraftValidationError(
            "MCP worker output JSON must be an object",
            details={"parsed_type": type(parsed).__name__},
        )
    return parsed


def _strip_json_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _has_content(content: JsonValue) -> bool:
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list | dict):
        return bool(content)
    return True


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
