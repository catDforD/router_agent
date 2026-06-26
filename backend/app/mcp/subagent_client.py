"""HTTP SSE client for remote PLC subagent workers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import difflib
import json
from pathlib import PurePath
from time import sleep as default_sleep
from typing import Any

import httpx

from app.mcp.draft import (
    LlmArtifactWriteDraft,
    LlmWorkerDraftOutput,
    McpInputArtifactSnapshot,
)
from app.models.router_schema import (
    ArtifactType,
    NextRecommendedAction,
    WorkerInput,
    WorkerMetrics,
    WorkerOutcome,
    WorkerOutcomeStatus,
    WorkerType,
)


SUBAGENT_AGENT_BY_WORKER: dict[str, str] = {
    WorkerType.PLC_DEV.value: "retrieval_planning_coding_agent",
    WorkerType.PLC_TEST.value: "fuzz_testing_agent",
    WorkerType.PLC_FORMAL.value: "formal_validation_agent",
    WorkerType.PLC_REPAIR.value: "compilation_debugging_agent",
}

CONTEXT_FIELDS_BY_WORKER: dict[str, set[str]] = {
    WorkerType.PLC_DEV.value: {
        "target_language",
        "template",
        "language_hint",
        "enable_socratic_spec",
        "socratic_skip",
        "compiler_type",
        "rpc_pipeline",
    },
    WorkerType.PLC_TEST.value: {
        "fuzz_method",
        "case_count",
        "enable_fuzz_test",
    },
    WorkerType.PLC_FORMAL.value: {
        "compiler_type",
        "properties",
        "natural_language_requirements",
    },
    WorkerType.PLC_REPAIR.value: {
        "repair_source",
        "repair_targets",
        "repair_failure_notes",
        "compiler_type",
    },
}

CODE_EVENT_TYPES = {
    "st_code_json",
    "code_json",
    "plc_code_json",
    "final_code_json",
    "repaired_code_json",
}
REPORT_ARTIFACT_CONTENT_TYPES = {
    ArtifactType.PLC_CODE.value,
    ArtifactType.TEST_REPORT.value,
    ArtifactType.FORMAL_REPORT.value,
    ArtifactType.FAILING_TRACE.value,
    ArtifactType.COUNTEREXAMPLE.value,
    ArtifactType.REPAIR_SUMMARY.value,
    ArtifactType.PATCH.value,
}
RETRYABLE_HTTP_STATUS_CODES = (429, 502, 503, 504)


class SubagentClientError(Exception):
    """Base class for remote subagent client failures."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class SubagentConnectionError(SubagentClientError):
    """Raised when the remote subagent API cannot be reached."""


class SubagentTimeoutError(SubagentClientError):
    """Raised when a remote subagent call times out."""


class SubagentExecutionError(SubagentClientError):
    """Raised when the remote subagent emits an execution error event."""


class SubagentInvalidResponseError(SubagentClientError):
    """Raised when the remote subagent stream cannot be parsed."""


SubagentSseEvent = dict[str, Any]


@dataclass
class SubagentWorkerClient:
    """Synchronous Router-facing wrapper over the remote subagent SSE API."""

    base_url: str
    timeout_seconds: int = 300
    api_token: str | None = None
    artifact_max_chars: int = 12_000
    http_client: httpx.Client | None = None
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    retry_status_codes: tuple[int, ...] = RETRYABLE_HTTP_STATUS_CODES
    sleep: Callable[[float], None] = default_sleep

    def call_worker(
        self,
        worker_input: WorkerInput,
        input_artifacts: list[McpInputArtifactSnapshot],
    ) -> LlmWorkerDraftOutput:
        """Call a remote subagent and convert its stream to a worker draft."""

        request = build_subagent_request(
            worker_input,
            input_artifacts,
            artifact_max_chars=self.artifact_max_chars,
        )
        events = self._stream_events(request)
        error_event = _first_error_event(events)
        if error_event is not None:
            raise SubagentExecutionError(
                _error_message(error_event),
                details={"event": _json_safe(error_event)},
            )
        return draft_from_subagent_events(worker_input, input_artifacts, events)

    def _stream_events(self, request: dict[str, Any]) -> list[SubagentSseEvent]:
        url = _join_url(self.base_url, "/api/chat/stream")
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            if self.http_client is not None:
                return self._stream_with_retries(
                    self.http_client,
                    url,
                    headers,
                    request,
                )
            timeout = httpx.Timeout(float(self.timeout_seconds))
            with httpx.Client(timeout=timeout) as client:
                return self._stream_with_retries(client, url, headers, request)
        except SubagentConnectionError:
            raise
        except httpx.TimeoutException as exc:
            raise SubagentTimeoutError(
                "Subagent worker call timed out",
                details={"url": url},
            ) from exc
        except httpx.HTTPError as exc:
            raise SubagentConnectionError(
                "Subagent API cannot be reached",
                details={"url": url, "exception_type": type(exc).__name__},
            ) from exc

    def _stream_with_retries(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        request: dict[str, Any],
    ) -> list[SubagentSseEvent]:
        attempts = max(1, self.max_retries + 1)
        last_status_error: httpx.HTTPStatusError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._stream_with_client(client, url, headers, request)
            except httpx.HTTPStatusError as exc:
                last_status_error = exc
                status_code = exc.response.status_code
                retryable = status_code in self.retry_status_codes
                if not retryable or attempt >= attempts:
                    raise SubagentConnectionError(
                        "Subagent API returned an HTTP error",
                        details={
                            "url": url,
                            "status_code": status_code,
                            "attempts": attempt,
                            "max_retries": max(0, self.max_retries),
                            "retryable_status_code": retryable,
                        },
                    ) from exc
                self._sleep_before_retry(attempt)

        raise SubagentConnectionError(
            "Subagent API returned an HTTP error",
            details={
                "url": url,
                "status_code": (
                    last_status_error.response.status_code
                    if last_status_error is not None
                    else None
                ),
                "attempts": attempts,
                "max_retries": max(0, self.max_retries),
                "retryable_status_code": True,
            },
        )

    def _stream_with_client(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        request: dict[str, Any],
    ) -> list[SubagentSseEvent]:
        with client.stream("POST", url, headers=headers, json=request) as response:
            response.raise_for_status()
            return parse_sse_events(response.iter_lines())

    def _sleep_before_retry(self, failed_attempt: int) -> None:
        delay = self.retry_backoff_seconds * (2 ** max(failed_attempt - 1, 0))
        if delay > 0:
            self.sleep(delay)


def build_subagent_request(
    worker_input: WorkerInput,
    input_artifacts: list[McpInputArtifactSnapshot],
    *,
    artifact_max_chars: int = 12_000,
) -> dict[str, Any]:
    """Build the POST /api/chat/stream body expected by remote subagents."""

    worker = _value(worker_input.worker_type)
    agent_id = SUBAGENT_AGENT_BY_WORKER.get(worker)
    if agent_id is None:
        raise SubagentInvalidResponseError(
            f"unsupported worker type for subagent dispatch: {worker!r}",
            details={"worker_type": worker},
        )
    return {
        "message": build_subagent_message(
            worker_input,
            input_artifacts,
            artifact_max_chars=artifact_max_chars,
        ),
        "agent_id": agent_id,
        "context": build_subagent_context(worker_input),
    }


def build_subagent_context(worker_input: WorkerInput) -> dict[str, Any]:
    """Map Router WorkerConfig fields directly into subagent context."""

    worker = _value(worker_input.worker_type)
    worker_config = worker_input.worker_config
    if worker_config is None:
        return {}

    dumped = worker_config.model_dump(mode="json", exclude_none=True)
    allowed = CONTEXT_FIELDS_BY_WORKER.get(worker, set())
    context = {
        field: dumped[field]
        for field in sorted(allowed)
        if field in dumped
    }
    llm = dumped.get("llm")
    if isinstance(llm, dict):
        llm = {key: value for key, value in llm.items() if value is not None}
        if llm:
            context["llm"] = llm
    return context


def build_subagent_message(
    worker_input: WorkerInput,
    input_artifacts: list[McpInputArtifactSnapshot],
    *,
    artifact_max_chars: int = 12_000,
) -> str:
    """Assemble a bounded natural-language prompt for the remote subagent."""

    parts = [worker_input.objective.strip()]
    user_goal = worker_input.context.user_goal.strip()
    if user_goal and user_goal not in parts[0]:
        parts.append(f"User goal:\n{user_goal}")

    if input_artifacts:
        parts.append("Input artifacts:")
        for artifact in input_artifacts:
            parts.append(_artifact_message_block(artifact, artifact_max_chars))

    return "\n\n".join(part for part in parts if part)


def parse_sse_events(lines: Iterable[str]) -> list[SubagentSseEvent]:
    """Parse SSE lines containing JSON data events."""

    events: list[SubagentSseEvent] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return
        raw_data = "\n".join(data_lines).strip()
        data_lines = []
        if not raw_data or raw_data == "[DONE]":
            event_name = None
            return
        events.append(_parse_sse_data(raw_data, event_name))
        event_name = None

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    flush()
    return events


def draft_from_subagent_events(
    worker_input: WorkerInput,
    input_artifacts: list[McpInputArtifactSnapshot],
    events: list[SubagentSseEvent],
) -> LlmWorkerDraftOutput:
    """Convert remote subagent SSE events into Router worker draft output."""

    worker = _value(worker_input.worker_type)
    if worker == WorkerType.PLC_DEV.value:
        return _dev_draft(worker_input, input_artifacts, events)
    if worker == WorkerType.PLC_TEST.value:
        return _test_draft(worker_input, events)
    if worker == WorkerType.PLC_FORMAL.value:
        return _formal_draft(worker_input, events)
    if worker == WorkerType.PLC_REPAIR.value:
        return _repair_draft(worker_input, input_artifacts, events)
    raise SubagentInvalidResponseError(
        f"unsupported worker type for subagent output: {worker!r}",
        details={"worker_type": worker},
    )


def _dev_draft(
    worker_input: WorkerInput,
    input_artifacts: list[McpInputArtifactSnapshot],
    events: list[SubagentSseEvent],
) -> LlmWorkerDraftOutput:
    code_event = _first_event(events, CODE_EVENT_TYPES)
    if code_event is None:
        return _fallback_draft(
            worker_input,
            events,
            ArtifactType.WORKER_LOG,
            "subagent_dev_output.txt",
            "PLC dev subagent returned text without structured code.",
        )

    code = _extract_code(code_event)
    if not code:
        return _fallback_draft(
            worker_input,
            events,
            ArtifactType.WORKER_LOG,
            "subagent_dev_output.txt",
            "PLC dev subagent returned a structured code event without code content.",
        )

    file_name = _code_file_name(code_event, default_name="plc_code_v1.st")
    artifacts = [
        _artifact(
            ArtifactType.REQUIREMENTS_IR,
            "requirements_ir_v1.json",
            {
                "source": "remote_subagent",
                "objective": worker_input.objective,
                "user_goal": worker_input.context.user_goal,
                "input_artifact_ids": [
                    artifact.artifact_id for artifact in input_artifacts
                ],
                "requirements": [
                    {
                        "id": "REQ-1",
                        "text": worker_input.context.user_goal or worker_input.objective,
                    }
                ],
            },
            "Minimal requirements IR assembled from subagent request context.",
            mime_type="application/json",
        ),
        _artifact(
            ArtifactType.PLC_CODE,
            file_name,
            code,
            "PLC code generated by remote subagent.",
            mime_type="text/plain",
        ),
        _artifact(
            ArtifactType.IO_CONTRACT,
            "io_contract_v1.json",
            _io_contract_from_code(code),
            "Minimal IO contract extracted from generated PLC code.",
            mime_type="application/json",
        ),
    ]
    return LlmWorkerDraftOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
            confidence=0.85,
            reason="Remote PLC dev subagent produced structured code.",
        ),
        summary="Remote PLC dev subagent generated PLC code.",
        artifact_writes=artifacts,
        metrics=WorkerMetrics(),
        next_recommended_action=_dev_next_action(worker_input),
        metadata=_metadata(events, SUBAGENT_AGENT_BY_WORKER[WorkerType.PLC_DEV.value]),
    )


def _test_draft(
    worker_input: WorkerInput,
    events: list[SubagentSseEvent],
) -> LlmWorkerDraftOutput:
    report_event = _first_event(events, {"fuzz_report_json"})
    if report_event is None:
        return _fallback_draft(
            worker_input,
            events,
            ArtifactType.TEST_REPORT,
            "test_report_v1.json",
            "PLC test subagent returned text without structured fuzz report.",
        )

    content = _event_content(report_event)
    status = _test_status(content)
    passed = status == WorkerOutcomeStatus.PASSED
    return LlmWorkerDraftOutput(
        outcome=WorkerOutcome(
            status=status,
            blocking=not passed,
            confidence=0.8 if passed else 0.6,
            reason="Remote fuzz report was parsed.",
        ),
        summary=_summary_from_content(content, "Remote PLC test subagent returned a fuzz report."),
        artifact_writes=[
            _artifact(
                ArtifactType.TEST_REPORT,
                "test_report_v1.json",
                content,
                "Fuzz test report from remote subagent.",
                mime_type="application/json",
            )
        ],
        metrics=_test_metrics(content),
        next_recommended_action=(
            NextRecommendedAction.NONE if passed else NextRecommendedAction.REPAIR
        ),
        metadata=_metadata(events, SUBAGENT_AGENT_BY_WORKER[WorkerType.PLC_TEST.value]),
    )


def _formal_draft(
    worker_input: WorkerInput,
    events: list[SubagentSseEvent],
) -> LlmWorkerDraftOutput:
    report_event = _first_event(events, {"formal_report_json"})
    if report_event is None:
        return _fallback_draft(
            worker_input,
            events,
            ArtifactType.FORMAL_REPORT,
            "formal_report_v1.json",
            "PLC formal subagent returned text without structured formal report.",
        )

    content = _event_content(report_event)
    status = _formal_status(content)
    passed = status == WorkerOutcomeStatus.PASSED
    return LlmWorkerDraftOutput(
        outcome=WorkerOutcome(
            status=status,
            blocking=not passed,
            confidence=0.85 if passed else 0.65,
            reason="Remote formal report was parsed.",
        ),
        summary=_summary_from_content(
            content,
            "Remote PLC formal subagent returned a formal verification report.",
        ),
        artifact_writes=[
            _artifact(
                ArtifactType.FORMAL_REPORT,
                "formal_report_v1.json",
                content,
                "Formal verification report from remote subagent.",
                mime_type="application/json",
            )
        ],
        metrics=_formal_metrics(content),
        next_recommended_action=(
            NextRecommendedAction.NONE if passed else NextRecommendedAction.REPAIR
        ),
        metadata=_metadata(events, SUBAGENT_AGENT_BY_WORKER[WorkerType.PLC_FORMAL.value]),
    )


def _repair_draft(
    worker_input: WorkerInput,
    input_artifacts: list[McpInputArtifactSnapshot],
    events: list[SubagentSseEvent],
) -> LlmWorkerDraftOutput:
    report_event = _first_event(events, {"compilation_report_json"})
    code_event = _first_event(events, CODE_EVENT_TYPES)
    report = _event_content(report_event) if report_event is not None else {}
    repaired_code = _extract_code(code_event) if code_event is not None else None
    passed = _repair_passed(report) and bool(repaired_code)
    if not passed and not report:
        return _fallback_draft(
            worker_input,
            events,
            ArtifactType.REPAIR_SUMMARY,
            "repair_summary_v1.json",
            "PLC repair subagent returned text without structured repair report.",
        )

    artifacts = [
        _artifact(
            ArtifactType.REPAIR_SUMMARY,
            "repair_summary_v1.json",
            {
                "source": "remote_subagent",
                "compilation_report": report,
                "token_excerpt": _token_text(events)[:2_000],
            },
            "Repair summary from remote subagent.",
            mime_type="application/json",
        ),
        _artifact(
            ArtifactType.PATCH,
            "patch_v1.diff",
            _patch_from_code(input_artifacts, repaired_code, events),
            "Patch draft from remote repair subagent.",
            mime_type="text/x-diff",
        ),
    ]
    if repaired_code:
        artifacts.append(
            _artifact(
                ArtifactType.PLC_CODE,
                _code_file_name(code_event, default_name="plc_code_repaired_v1.st"),
                repaired_code,
                "Repaired PLC code from remote subagent.",
                version=_next_code_version(input_artifacts),
                mime_type="text/plain",
            )
        )

    status = WorkerOutcomeStatus.PASSED if passed else WorkerOutcomeStatus.FAILED
    return LlmWorkerDraftOutput(
        outcome=WorkerOutcome(
            status=status,
            blocking=not passed,
            confidence=0.8 if passed else 0.55,
            reason=(
                "Remote repair report indicated success and returned repaired code."
                if passed
                else "Remote repair report did not provide enough evidence for a passed repair."
            ),
        ),
        summary=_summary_from_content(report, "Remote PLC repair subagent returned a repair report."),
        artifact_writes=artifacts,
        metrics=_repair_metrics(input_artifacts, repaired_code),
        next_recommended_action=(
            NextRecommendedAction.TEST if passed else NextRecommendedAction.RETRY
        ),
        metadata=_metadata(events, SUBAGENT_AGENT_BY_WORKER[WorkerType.PLC_REPAIR.value]),
    )


def _fallback_draft(
    worker_input: WorkerInput,
    events: list[SubagentSseEvent],
    artifact_type: ArtifactType,
    artifact_name: str,
    reason: str,
) -> LlmWorkerDraftOutput:
    token_text = _token_text(events)
    content: Any
    if artifact_type == ArtifactType.WORKER_LOG:
        content = token_text or reason
    else:
        content = {
            "source": "remote_subagent",
            "status": "unstructured",
            "reason": reason,
            "text": token_text,
        }
    return LlmWorkerDraftOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.FAILED,
            blocking=True,
            confidence=0.35,
            reason=reason,
        ),
        summary=reason,
        artifact_writes=[
            _artifact(
                artifact_type,
                artifact_name,
                content,
                reason,
                mime_type="text/plain" if isinstance(content, str) else "application/json",
            )
        ],
        metrics=WorkerMetrics(),
        next_recommended_action=NextRecommendedAction.RETRY,
        metadata=_metadata(
            events,
            SUBAGENT_AGENT_BY_WORKER.get(_value(worker_input.worker_type), "unknown"),
        ),
    )


def _artifact(
    artifact_type: ArtifactType,
    name: str,
    content: Any,
    summary: str,
    *,
    version: int = 1,
    mime_type: str | None = None,
) -> LlmArtifactWriteDraft:
    return LlmArtifactWriteDraft(
        artifact_type=artifact_type,
        version=version,
        name=name,
        content=content,
        summary=summary,
        mime_type=mime_type,
    )


def _artifact_message_block(
    artifact: McpInputArtifactSnapshot,
    artifact_max_chars: int,
) -> str:
    header = (
        f"- artifact_id={artifact.artifact_id}, type={_value(artifact.type)}, "
        f"version={artifact.version}"
    )
    if artifact.summary:
        header += f", summary={artifact.summary}"
    if artifact.content is None:
        return header

    content = artifact.content[:artifact_max_chars]
    truncated = artifact.content_truncated or len(artifact.content) > artifact_max_chars
    label = "content"
    if _value(artifact.type) in REPORT_ARTIFACT_CONTENT_TYPES:
        label = "bounded content"
    suffix = "\n[truncated]" if truncated else ""
    return f"{header}\n{label}:\n{content}{suffix}"


def _parse_sse_data(raw_data: str, event_name: str | None) -> SubagentSseEvent:
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise SubagentInvalidResponseError(
            "Subagent SSE data is not valid JSON",
            details={"json_error": str(exc), "data_excerpt": raw_data[:500]},
        ) from exc

    if isinstance(parsed, dict):
        event_type = parsed.get("type") or parsed.get("event") or event_name or "message"
        data = parsed
    else:
        event_type = event_name or "message"
        data = {"content": parsed}

    return {
        "type": str(event_type),
        "event": event_name,
        "data": data,
        "raw": raw_data,
    }


def _first_error_event(events: list[SubagentSseEvent]) -> SubagentSseEvent | None:
    for event in events:
        if event.get("type") == "error" or event.get("event") == "error":
            return event
    return None


def _first_event(
    events: list[SubagentSseEvent],
    event_types: set[str],
) -> SubagentSseEvent | None:
    for event in events:
        if str(event.get("type")) in event_types:
            return event
    return None


def _event_content(event: SubagentSseEvent | None) -> Any:
    if event is None:
        return {}
    data = event.get("data")
    if not isinstance(data, dict):
        return {}
    content = data.get("content")
    return content if content is not None else data


def _extract_code(event: SubagentSseEvent | None) -> str | None:
    if event is None:
        return None
    data = event.get("data")
    candidates = []
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("code"),
                data.get("st_code"),
                data.get("plc_code"),
                data.get("repaired_code"),
            ]
        )
        for key in ("stCode", "content", "result", "output"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.extend(
                    [
                        nested.get("code"),
                        nested.get("st_code"),
                        nested.get("plc_code"),
                        nested.get("repaired_code"),
                    ]
                )
            elif isinstance(nested, str):
                candidates.append(nested)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _code_file_name(event: SubagentSseEvent | None, *, default_name: str) -> str:
    data = event.get("data") if event else None
    if isinstance(data, dict):
        for container in (data, data.get("content"), data.get("stCode")):
            if not isinstance(container, dict):
                continue
            for key in ("file_name", "filename", "file_path", "path"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return PurePath(value.strip()).name
    return default_name


def _io_contract_from_code(code: str) -> dict[str, list[dict[str, str]]]:
    return {
        "inputs": _variables_from_block(code, "VAR_INPUT"),
        "outputs": _variables_from_block(code, "VAR_OUTPUT"),
    }


def _variables_from_block(code: str, block_name: str) -> list[dict[str, str]]:
    lines = code.splitlines()
    in_block = False
    variables: list[dict[str, str]] = []
    for raw_line in lines:
        line = raw_line.strip()
        upper = line.upper()
        if upper == block_name:
            in_block = True
            continue
        if in_block and upper.startswith("END_VAR"):
            break
        if not in_block or ":" not in line:
            continue
        name_part, _, type_part = line.partition(":")
        name = name_part.strip()
        variable_type = type_part.split(";", 1)[0].split(":=", 1)[0].strip()
        if name and variable_type:
            variables.append({"name": name, "type": variable_type})
    return variables


def _test_status(content: Any) -> WorkerOutcomeStatus:
    if _has_failure_signal(content):
        return WorkerOutcomeStatus.FAILED
    passed = _bool_signal(content, {"all_passed", "success", "passed"})
    failed_count = _int_signal(
        content,
        {"failed", "failed_cases", "failure_count", "failed_count"},
    )
    total = _int_signal(content, {"total", "total_cases", "case_count", "cases"})
    passed_count = _int_signal(content, {"passed", "passed_cases", "passed_count"})
    if passed is True and (failed_count is None or failed_count == 0):
        return WorkerOutcomeStatus.PASSED
    if failed_count == 0 and total is not None and total > 0:
        return WorkerOutcomeStatus.PASSED
    if (
        total is not None
        and passed_count is not None
        and total > 0
        and passed_count == total
    ):
        return WorkerOutcomeStatus.PASSED
    return WorkerOutcomeStatus.FAILED


def _formal_status(content: Any) -> WorkerOutcomeStatus:
    all_satisfied = _bool_signal(
        content,
        {"all_satisfied", "verified", "success", "passed"},
    )
    if all_satisfied is True and not _has_failure_signal(content):
        return WorkerOutcomeStatus.PASSED
    if all_satisfied is False or _has_failure_signal(content):
        return WorkerOutcomeStatus.FAILED

    failed = _int_signal(
        content,
        {"failed_properties", "violated_properties", "failed", "violations"},
    )
    total = _int_signal(content, {"total_properties", "total", "properties_count"})
    passed = _int_signal(content, {"passed_properties", "satisfied_properties", "passed"})
    if failed == 0 and total is not None and total > 0:
        return WorkerOutcomeStatus.PASSED
    if total is not None and passed is not None and total > 0 and passed == total:
        return WorkerOutcomeStatus.PASSED
    return WorkerOutcomeStatus.FAILED


def _repair_passed(content: Any) -> bool:
    if _has_failure_signal(content):
        return False
    passed = _bool_signal(
        content,
        {"compilation_success", "compile_success", "success", "passed", "fixed"},
    )
    if passed is not None:
        return passed
    status = _string_signal(content, {"status", "result", "outcome"})
    return _status_is_success(status)


def _has_failure_signal(content: Any) -> bool:
    failed = _int_signal(
        content,
        {
            "failed",
            "failed_count",
            "failed_cases",
            "failed_properties",
            "violations",
            "error_count",
        },
    )
    if failed is not None and failed > 0:
        return True

    success = _bool_signal(
        content,
        {"all_passed", "all_satisfied", "success", "passed", "verified"},
    )
    if success is False:
        return True

    status = _string_signal(content, {"status", "result", "outcome", "state"})
    if status is None:
        return False
    normalized = status.lower()
    return any(
        marker in normalized
        for marker in ("fail", "error", "invalid", "unsatisfied", "violat")
    )


def _test_metrics(content: Any) -> WorkerMetrics:
    return WorkerMetrics(
        test_metrics={
            "total": _int_signal(content, {"total", "total_cases", "case_count", "cases"}),
            "passed": _int_signal(content, {"passed", "passed_cases", "passed_count"}),
            "failed": _int_signal(content, {"failed", "failed_cases", "failed_count"}),
            "skipped": _int_signal(content, {"skipped", "skipped_cases"}),
            "coverage_score": _float_signal(content, {"coverage_score", "coverage"}),
        }
    )


def _formal_metrics(content: Any) -> WorkerMetrics:
    return WorkerMetrics(
        formal_metrics={
            "total_properties": _int_signal(
                content,
                {"total_properties", "total", "properties_count"},
            ),
            "passed_properties": _int_signal(
                content,
                {"passed_properties", "satisfied_properties", "passed"},
            ),
            "failed_properties": _int_signal(
                content,
                {"failed_properties", "violated_properties", "failed", "violations"},
            ),
            "unknown_properties": _int_signal(
                content,
                {"unknown_properties", "unknown"},
            ),
        }
    )


def _repair_metrics(
    input_artifacts: list[McpInputArtifactSnapshot],
    repaired_code: str | None,
) -> WorkerMetrics:
    original = _latest_code_content(input_artifacts)
    changed_lines = None
    if original and repaired_code:
        changed_lines = sum(
            1
            for line in difflib.unified_diff(
                original.splitlines(),
                repaired_code.splitlines(),
                lineterm="",
            )
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )
    return WorkerMetrics(
        repair_metrics={
            "changed_files": 1 if repaired_code else 0,
            "changed_lines": changed_lines,
            "patch_size_bytes": len(_patch_from_code(input_artifacts, repaired_code, [])),
        }
    )


def _summary_from_content(content: Any, default: str) -> str:
    summary = _string_signal(content, {"summary", "message", "description"})
    return summary or default


def _patch_from_code(
    input_artifacts: list[McpInputArtifactSnapshot],
    repaired_code: str | None,
    events: list[SubagentSseEvent],
) -> str:
    original = _latest_code_content(input_artifacts)
    if original and repaired_code:
        return "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                repaired_code.splitlines(),
                fromfile="before.st",
                tofile="after.st",
                lineterm="",
            )
        )
    if repaired_code:
        return "Remote subagent returned repaired code; original code snapshot was unavailable."
    token_text = _token_text(events)
    return token_text or "Remote subagent did not return a patch."


def _latest_code_content(
    input_artifacts: list[McpInputArtifactSnapshot],
) -> str | None:
    code_artifacts = [
        artifact
        for artifact in input_artifacts
        if _value(artifact.type) == ArtifactType.PLC_CODE.value and artifact.content
    ]
    if not code_artifacts:
        return None
    latest = max(code_artifacts, key=lambda artifact: artifact.version)
    return latest.content


def _next_code_version(input_artifacts: list[McpInputArtifactSnapshot]) -> int:
    versions = [
        artifact.version
        for artifact in input_artifacts
        if _value(artifact.type) == ArtifactType.PLC_CODE.value
    ]
    return max(versions, default=0) + 1


def _token_text(events: list[SubagentSseEvent]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("type") != "token":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        content = data.get("content") or data.get("text") or data.get("message")
        if isinstance(content, str):
            chunks.append(content)
    return "".join(chunks).strip()


def _dev_next_action(worker_input: WorkerInput) -> NextRecommendedAction:
    pipeline = []
    if worker_input.worker_config is not None and worker_input.worker_config.rpc_pipeline:
        pipeline = [_value(stage) for stage in worker_input.worker_config.rpc_pipeline]
    if "fuzz" in pipeline:
        return NextRecommendedAction.TEST
    if "formal" in pipeline:
        return NextRecommendedAction.FORMAL
    return NextRecommendedAction.NONE


def _metadata(events: list[SubagentSseEvent], agent_id: str) -> dict[str, Any]:
    event_types = [str(event.get("type")) for event in events]
    return {
        "worker_backend": "subagent",
        "subagent_agent_id": agent_id,
        "subagent_event_types": event_types,
        "subagent_structured_event_types": [
            event_type
            for event_type in event_types
            if event_type not in {"session_id", "agent_start", "token", "workflow_end"}
        ],
    }


def _bool_signal(content: Any, keys: set[str]) -> bool | None:
    value = _find_first_key(content, keys)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "passed", "success", "satisfied"}:
            return True
        if normalized in {"false", "no", "n", "0", "failed", "failure", "unsatisfied"}:
            return False
    return None


def _int_signal(content: Any, keys: set[str]) -> int | None:
    value = _find_first_key(content, keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _float_signal(content: Any, keys: set[str]) -> float | None:
    value = _find_first_key(content, keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _string_signal(content: Any, keys: set[str]) -> str | None:
    value = _find_first_key(content, keys)
    return value if isinstance(value, str) and value.strip() else None


def _find_first_key(content: Any, keys: set[str]) -> Any:
    normalized_keys = {key.lower() for key in keys}
    if isinstance(content, dict):
        for key, value in content.items():
            if str(key).lower() in normalized_keys:
                return value
        for value in content.values():
            nested = _find_first_key(value, keys)
            if nested is not None:
                return nested
    elif isinstance(content, list):
        for item in content:
            nested = _find_first_key(item, keys)
            if nested is not None:
                return nested
    return None


def _status_is_success(status: str | None) -> bool:
    if status is None:
        return False
    normalized = status.lower()
    if any(marker in normalized for marker in ("fail", "error", "invalid", "violat")):
        return False
    return any(marker in normalized for marker in ("pass", "success", "satisfied", "fixed"))


def _error_message(event: SubagentSseEvent) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "Subagent emitted an error event"


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))
