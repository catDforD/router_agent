"""Main Agent turn observability helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.output_schema import MainAgentEpisodeOutput
from app.core.ids import new_event_id
from app.core.time import utc_now
from app.models.router_schema import (
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    ArtifactVisibility,
    DEFAULT_SCHEMA_VERSION,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    RouterEvent,
    TokenUsage,
)
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService
from app.services.final_report import build_final_report_payload


MAX_RATIONALE_CHARS = 800
MAX_SUMMARY_CHARS = 1200
MAX_STRING_CHARS = 2000
MAX_COLLECTION_ITEMS = 20


@dataclass
class MainAgentObservabilityRecorder:
    """Persist compact Main Agent progress events and collect replay entries."""

    session: Session
    artifact_root: Path
    task_id: str
    main_agent_run_id: str | None
    openai_trace_id: str | None = None
    checkpoint: Callable[[], None] | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    turn_index: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    def start_turn(self, *, phase: str = "orchestration") -> int:
        self.turn_index += 1
        payload = {
            "task_id": self.task_id,
            "turn_index": self.turn_index,
            "phase": phase,
        }
        self._record_entry("turn_started", payload)
        self._append_event(
            event_type=EventType.MAIN_AGENT_TURN_STARTED,
            title="Main Agent turn started",
            message=f"Main Agent orchestration turn {self.turn_index} started.",
            payload=payload,
        )
        return self.turn_index

    def record_message(
        self,
        *,
        content: str,
        phase: str = "orchestration",
        turn_index: int | None = None,
    ) -> RouterEvent:
        return self.record_progress_message(
            content=content,
            phase=phase,
            turn_index=turn_index,
        )

    def record_progress_message(
        self,
        *,
        content: str,
        phase: str = "orchestration",
        turn_index: int | None = None,
    ) -> RouterEvent:
        index = turn_index or self._ensure_turn()
        message = _bounded_text(content, limit=MAX_SUMMARY_CHARS) or ""
        payload = {
            "task_id": self.task_id,
            "turn_index": index,
            "phase": phase,
            "visibility": "public",
            "message_kind": "progress",
            "display_region": "process",
            "content": message,
        }
        self._record_entry("message", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_MESSAGE,
            title="Main Agent message",
            message=message,
            payload=payload,
        )

    def record_final_response(
        self,
        *,
        content: str,
        final_status: str,
        source: str,
        turn_index: int | None = None,
    ) -> RouterEvent:
        index = turn_index or self._ensure_turn()
        message = _bounded_text(content, limit=MAX_SUMMARY_CHARS) or ""
        payload = {
            "task_id": self.task_id,
            "main_agent_run_id": self.main_agent_run_id,
            "turn_index": index,
            "content": message,
            "final_status": final_status,
            "source": source,
        }
        self._record_entry("final_response", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_FINAL_RESPONSE,
            title="Main Agent final response",
            message=message,
            payload=payload,
        )

    def record_stop_blocked(
        self,
        *,
        reason: str,
        blocked_count: int,
        max_blocked_count: int,
        turn_index: int | None = None,
    ) -> RouterEvent:
        index = turn_index or self._ensure_turn()
        message = _bounded_text(reason, limit=MAX_SUMMARY_CHARS) or ""
        payload = {
            "task_id": self.task_id,
            "main_agent_run_id": self.main_agent_run_id,
            "turn_index": index,
            "reason": message,
            "blocked_count": blocked_count,
            "max_blocked_count": max_blocked_count,
        }
        self._record_entry("stop_blocked", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_STOP_BLOCKED,
            title="Main Agent stop blocked",
            message=message,
            payload=payload,
            severity=EventSeverity.WARNING,
        )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        rationale_summary: str | None = None,
        input_artifact_ids: list[str] | None = None,
        turn_index: int | None = None,
    ) -> RouterEvent:
        index = turn_index or self._ensure_turn()
        artifact_ids = _clean_string_list(input_artifact_ids)
        payload = {
            "task_id": self.task_id,
            "turn_index": index,
            "tool_name": tool_name,
            "rationale_summary": _bounded_text(
                rationale_summary,
                limit=MAX_RATIONALE_CHARS,
            ),
            "arguments": _sanitize_value(arguments or {}),
            "input_artifact_ids": artifact_ids,
        }
        self._record_entry("tool_called", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_TOOL_CALLED,
            title=f"Main Agent selected {tool_name}",
            message=payload["rationale_summary"] or f"Main Agent selected {tool_name}.",
            payload=payload,
            artifact_ids=artifact_ids or None,
        )

    def record_tool_result(
        self,
        *,
        tool_name: str,
        result: Any,
        turn_index: int | None = None,
    ) -> RouterEvent:
        index = turn_index or self._ensure_turn()
        summary = _extract_result_summary(result)
        artifact_ids = _extract_artifact_ids(result)
        failure_ids = _extract_failure_ids(result)
        payload = {
            "task_id": self.task_id,
            "turn_index": index,
            "tool_name": tool_name,
            "status": _extract_result_field(result, "status"),
            "summary": summary,
            "artifact_ids": artifact_ids,
            "failure_ids": failure_ids,
            "worker_job_id": _extract_result_field(result, "worker_job_id"),
            "worker_type": _extract_result_field(result, "worker_type"),
            "next_recommended_action": _extract_result_field(
                result,
                "next_recommended_action",
            ),
            "details": _sanitize_value(_result_to_dict(result)),
        }
        self._record_entry("tool_result", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_TOOL_RESULT,
            title=f"Main Agent observed {tool_name} result",
            message=summary,
            payload=payload,
            artifact_ids=artifact_ids or None,
            failure_ids=failure_ids or None,
        )

    def record_error(
        self,
        *,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> RouterEvent:
        payload = {
            "task_id": self.task_id,
            "error_code": error_code,
            "message": _bounded_text(message, limit=MAX_SUMMARY_CHARS),
            "details": _sanitize_value(details or {}),
        }
        self._record_entry("error", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_DECISION,
            title="Main Agent observability error",
            message=payload["message"],
            payload=payload,
            severity=EventSeverity.ERROR,
        )

    def add_token_usage(self, usage: TokenUsage | None) -> None:
        if usage is None or not _has_token_usage(usage):
            return
        self.token_usage = _merge_token_usage(self.token_usage, usage)
        self._record_entry(
            "token_usage",
            {
                "task_id": self.task_id,
                "main_agent_run_id": self.main_agent_run_id,
                "turn_index": self.turn_index or None,
                "token_usage_delta": _token_usage_payload(usage),
                "token_usage_total": _token_usage_payload(self.token_usage),
                "token_usage_scope": "main_agent",
            },
        )

    def write_final_report(
        self,
        output: MainAgentEpisodeOutput,
    ) -> ArtifactRef:
        created_at = utc_now()
        content = build_final_report_payload(
            session=self.session,
            task_id=self.task_id,
            output=output,
            main_agent_run_id=self.main_agent_run_id,
            created_at=created_at,
        )
        result = ArtifactStore(
            session=self.session,
            artifact_root=self.artifact_root,
        ).write_artifact_content(
            ArtifactContentWrite(
                task_id=self.task_id,
                artifact_type=ArtifactType.FINAL_REPORT,
                version=1,
                name="main_agent_final_report.json",
                content=content,
                summary=_bounded_text(output.summary, limit=MAX_SUMMARY_CHARS)
                or "Main Agent final report.",
                visibility=ArtifactVisibility.USER,
                created_by={
                    "type": ArtifactCreatorType.MAIN_AGENT,
                    "id": self.main_agent_run_id,
                    "main_agent_run_id": self.main_agent_run_id,
                },
                metadata={"tags": ["main_agent", "final_report"]},
                mime_type="application/json",
                created_at=created_at,
            )
        )
        self._checkpoint()
        return ArtifactStore(
            session=self.session,
            artifact_root=self.artifact_root,
        ).get_artifact_ref(result.artifact.artifact_id)

    def write_replay_log(
        self,
        *,
        final_output: MainAgentEpisodeOutput | None = None,
        error: dict[str, Any] | None = None,
        visibility: ArtifactVisibility | str = ArtifactVisibility.INTERNAL,
    ) -> ArtifactRef:
        content = {
            "kind": "main_agent_replay_log",
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "created_at": utc_now().isoformat(),
            "task_id": self.task_id,
            "main_agent_run_id": self.main_agent_run_id,
            "entries": [_sanitize_value(entry) for entry in self.entries],
            "final_output": (
                final_output.model_dump(mode="json") if final_output is not None else None
            ),
            "error": _sanitize_value(error) if error is not None else None,
        }
        result = ArtifactStore(
            session=self.session,
            artifact_root=self.artifact_root,
        ).write_artifact_content(
            ArtifactContentWrite(
                task_id=self.task_id,
                artifact_type=ArtifactType.MAIN_AGENT_LOG,
                version=1,
                name="main_agent_replay_log.json",
                content=content,
                summary="Main Agent orchestration replay log.",
                visibility=visibility,
                created_by={
                    "type": ArtifactCreatorType.MAIN_AGENT,
                    "id": self.main_agent_run_id,
                    "main_agent_run_id": self.main_agent_run_id,
                },
                metadata={"tags": ["main_agent", "replay_log"]},
                mime_type="application/json",
            )
        )
        self._checkpoint()
        return ArtifactStore(
            session=self.session,
            artifact_root=self.artifact_root,
        ).get_artifact_ref(result.artifact.artifact_id)

    def record_completed(
        self,
        *,
        output: MainAgentEpisodeOutput,
        final_report: ArtifactRef,
        replay_log: ArtifactRef,
    ) -> RouterEvent:
        artifact_ids = [final_report.artifact_id, replay_log.artifact_id]
        payload = {
            "task_id": self.task_id,
            "main_agent_run_id": self.main_agent_run_id,
            "final_task_status": _enum_value(output.final_task_status),
            "summary": _bounded_text(output.summary, limit=MAX_SUMMARY_CHARS),
            "final_report_artifact_id": final_report.artifact_id,
            "main_agent_log_artifact_id": replay_log.artifact_id,
            "decision_count": len(output.decisions),
            "plan_step_count": len(output.plan),
            "next_recommended_action": _enum_value(output.next_recommended_action),
        }
        token_usage = _token_usage_payload(self.token_usage)
        if token_usage:
            payload["token_usage"] = token_usage
            payload["token_usage_scope"] = "main_agent"
        self._record_entry("completed", payload)
        return self._append_event(
            event_type=EventType.MAIN_AGENT_COMPLETED,
            title="Main Agent completed",
            message=payload["summary"],
            payload=payload,
            artifact_ids=artifact_ids,
        )

    def _ensure_turn(self) -> int:
        if self.turn_index == 0:
            return self.start_turn()
        return self.turn_index

    def _record_entry(self, entry_type: str, payload: dict[str, Any]) -> None:
        self.entries.append(
            {
                "type": entry_type,
                "task_id": self.task_id,
                "main_agent_run_id": self.main_agent_run_id,
                "turn_index": payload.get("turn_index"),
                "created_at": utc_now().isoformat(),
                "payload": _sanitize_value(payload),
            }
        )

    def _append_event(
        self,
        *,
        event_type: EventType,
        title: str,
        message: str | None,
        payload: dict[str, Any],
        severity: EventSeverity = EventSeverity.INFO,
        artifact_ids: list[str] | None = None,
        failure_ids: list[str] | None = None,
    ) -> RouterEvent:
        event = RouterEvent(
            schema_version=DEFAULT_SCHEMA_VERSION,
            event_id=new_event_id(),
            task_id=self.task_id,
            seq=0,
            type=event_type,
            source=EventSource(
                type=EventSourceType.MAIN_AGENT,
                id=self.main_agent_run_id,
            ),
            severity=severity,
            visibility=EventVisibility.USER,
            title=title,
            message=message,
            correlation=EventCorrelation(
                openai_trace_id=self.openai_trace_id,
                main_agent_run_id=self.main_agent_run_id,
                artifact_ids=artifact_ids,
                failure_ids=failure_ids,
            ),
            payload=_sanitize_value(payload),
            created_at=utc_now(),
        )
        appended = EventService(self.session).append_event(event)
        self._checkpoint()
        return appended

    def _checkpoint(self) -> None:
        if self.checkpoint is not None:
            self.checkpoint()


def _extract_result_summary(result: Any) -> str:
    summary = _extract_result_field(result, "summary")
    return _bounded_text(str(summary), limit=MAX_SUMMARY_CHARS) if summary else ""


def _extract_result_field(result: Any, field_name: str) -> Any:
    if isinstance(result, dict):
        value = result.get(field_name)
    else:
        value = getattr(result, field_name, None)
    return _enum_value(value)


def _extract_artifact_ids(result: Any) -> list[str]:
    data = _result_to_dict(result)
    artifact_ids: list[str] = []

    for ref in data.get("artifact_refs") or []:
        artifact_id = ref.get("artifact_id") if isinstance(ref, dict) else None
        if artifact_id:
            artifact_ids.append(str(artifact_id))

    artifact = data.get("artifact")
    if isinstance(artifact, dict):
        artifact_id = artifact.get("artifact_id")
        if artifact_id:
            artifact_ids.append(str(artifact_id))

    for child in data.get("results") or []:
        artifact_ids.extend(_extract_artifact_ids(child))

    return _dedupe(artifact_ids)


def _extract_failure_ids(result: Any) -> list[str]:
    data = _result_to_dict(result)
    failure_ids: list[str] = []
    for failure in data.get("failures") or []:
        if isinstance(failure, dict) and failure.get("failure_id"):
            failure_ids.append(str(failure["failure_id"]))
    for child in data.get("results") or []:
        failure_ids.extend(_extract_failure_ids(child))
    return _dedupe(failure_ids)


def _result_to_dict(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return {"value": str(result)}


def _sanitize_value(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, str):
        return _bounded_text(value, limit=MAX_STRING_CHARS)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:MAX_COLLECTION_ITEMS]]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = "[redacted]"
            else:
                sanitized[key_text] = _sanitize_value(item)
        return sanitized
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


def _bounded_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + "... [truncated]"


def _clean_string_list(values: list[str] | None) -> list[str]:
    return _dedupe(str(value) for value in values or [] if value)


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _has_token_usage(usage: TokenUsage) -> bool:
    return (
        usage.input_tokens is not None
        or usage.output_tokens is not None
        or usage.total_tokens is not None
    )


def _merge_token_usage(current: TokenUsage, delta: TokenUsage) -> TokenUsage:
    input_tokens = _sum_optional(current.input_tokens, delta.input_tokens)
    output_tokens = _sum_optional(current.output_tokens, delta.output_tokens)
    total_tokens = _sum_optional(
        _effective_total_tokens(current),
        _effective_total_tokens(delta),
    )
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _effective_total_tokens(usage: TokenUsage) -> int | None:
    if usage.total_tokens is not None:
        return usage.total_tokens
    parts = [
        value
        for value in (usage.input_tokens, usage.output_tokens)
        if value is not None
    ]
    return sum(parts) if parts else None


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _token_usage_payload(usage: TokenUsage) -> dict[str, int]:
    payload = usage.model_dump(mode="json", exclude_none=True)
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, int)
    }


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {
        "token_usage",
        "token_usage_delta",
        "token_usage_total",
        "token_usage_scope",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
    }:
        return False
    return any(token in lowered for token in ("key", "token", "secret", "password"))
