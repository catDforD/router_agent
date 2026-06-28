"""LLM-backed PLC worker simulation used by the local MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Protocol

from openai import OpenAI

from app.core.config import Settings, get_settings
from app.core.ids import prefixed_id
from app.core.time import utc_now
from app.mcp.draft import (
    LlmWorkerDraftOutput,
    McpDraftValidationError,
    McpWorkerRequest,
    parse_worker_draft_output,
    validate_worker_draft_output,
    validate_worker_request_tool,
)
from app.models.router_schema import (
    ArtifactType,
    McpToolName,
    WorkerConfig,
    WorkerType,
)


class LlmWorkerError(Exception):
    """Base class for LLM-backed worker simulation failures."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class DeepSeekConfigurationError(LlmWorkerError):
    """Raised when DeepSeek worker simulation settings are incomplete."""


class DeepSeekProviderError(LlmWorkerError):
    """Raised when the DeepSeek-compatible provider request fails."""


class LlmJsonClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> Any:
        """Return JSON text or an already-decoded JSON object."""


@dataclass(frozen=True)
class DeepSeekChatClient:
    """Small OpenAI-compatible client wrapper scoped to worker simulation."""

    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: int = 60
    max_retries: int = 1

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DeepSeekChatClient:
        config = settings or get_settings()
        return cls(
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            model=config.deepseek_model,
            timeout_seconds=config.deepseek_timeout_seconds,
            max_retries=config.deepseek_max_retries,
        )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if not self.api_key:
            raise DeepSeekConfigurationError("DEEPSEEK_API_KEY is required for worker simulation")

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=float(self.timeout_seconds),
                max_retries=self.max_retries,
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
            )
        except Exception as exc:
            raise DeepSeekProviderError(
                "DeepSeek worker simulation request failed",
                details={"exception_type": type(exc).__name__},
            ) from exc

        content = response.choices[0].message.content
        if not content:
            raise DeepSeekProviderError("DeepSeek worker simulation returned empty content")
        return content


@dataclass(frozen=True)
class LlmPlcWorkerService:
    """Runs one simulated PLC worker through an injected JSON-producing LLM client."""

    json_client: LlmJsonClient

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LlmPlcWorkerService:
        return cls(json_client=DeepSeekChatClient.from_settings(settings))

    def run_tool(
        self,
        tool_name: McpToolName | str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = McpWorkerRequest.model_validate(payload)
        validate_worker_request_tool(request, tool_name)

        raw_output = self.json_client.complete_json(
            system_prompt=_system_prompt(tool_name, request),
            user_prompt=_user_prompt(tool_name, request),
        )
        raw_output = _normalize_common_llm_output_shape(raw_output, tool_name, request)
        try:
            draft = parse_worker_draft_output(raw_output)
            validate_worker_draft_output(draft, request.worker_input)
        except McpDraftValidationError as exc:
            draft = _fallback_draft_output(tool_name, request, exc)
            validate_worker_draft_output(draft, request.worker_input)

        return _with_simulation_metadata(
            draft,
            tool_name,
            worker_config=request.worker_input.worker_config,
        ).model_dump(mode="json")


def _system_prompt(tool_name: McpToolName | str, request: McpWorkerRequest) -> str:
    tool = _value(tool_name)
    worker = {
        McpToolName.PLC_DEV_RUN.value: "PLC development",
        McpToolName.PLC_TEST_RUN.value: "PLC testing",
        McpToolName.PLC_FORMAL_RUN.value: "PLC formal verification",
        McpToolName.PLC_REPAIR_RUN.value: "PLC repair",
    }[tool]
    required = ", ".join(sorted(_required_artifact_types(tool)))
    worker_config_summary = _worker_config_summary(request.worker_input.worker_config)
    return (
        f"You are simulating a {worker} subagent for Router local integration tests. "
        "Return only one JSON object matching the Router internal LlmWorkerDraftOutput "
        "shape: outcome, summary, artifact_writes, diagnostics, assumptions, failures, "
        "clarification_request, metrics, next_recommended_action, metadata. "
        "outcome MUST be an object like "
        '{"status":"passed","blocking":false,"confidence":0.9,"reason":"..."}, '
        "never a string. artifact_writes items MUST use artifact_type, version, name, "
        "content, summary, visibility, metadata, parent_artifact_ids, mime_type. "
        "Do not return persisted artifact references: no artifact_id, type, uri, or "
        "content_truncated fields inside artifact_writes. "
        f"For passed outcomes, include non-empty artifact_writes for: {required}. "
        "Treat inputs as workspace files and use router.v2 file-centric context. "
        "Use concise summaries and no markdown. "
        f"Worker config: {worker_config_summary}."
    )


def _user_prompt(tool_name: McpToolName | str, request: McpWorkerRequest) -> str:
    worker_config = (
        request.worker_input.worker_config.model_dump(mode="json")
        if request.worker_input.worker_config is not None
        else None
    )
    return json.dumps(
        {
            "tool_name": _value(tool_name),
            "worker_input": request.worker_input.model_dump(mode="json"),
            "input_files": [
                input_file.model_dump(mode="json")
                for input_file in request.input_files
            ],
            "worker_config": worker_config,
            "worker_guidance": _worker_guidance(tool_name),
        },
        ensure_ascii=False,
    )


def _worker_guidance(tool_name: McpToolName | str) -> dict[str, Any]:
    tool = _value(tool_name)
    if tool == McpToolName.PLC_DEV_RUN.value:
        return {
            "required_outputs": ["requirements_ir", "plc_code", "io_contract"],
            "next_recommended_action": "test",
            "notes": [
                "Emit requirements_ir so downstream plc-test can run without seeded fixtures.",
                "Generate Structured Text when no language is specified.",
                "Keep generated code concise but non-empty.",
            ],
        }
    if tool == McpToolName.PLC_TEST_RUN.value:
        return {
            "required_outputs": ["test_report"],
            "failure_outputs": ["test_report", "failing_trace"],
            "next_recommended_action": "run_quality_gate or repair",
        }
    if tool == McpToolName.PLC_FORMAL_RUN.value:
        return {
            "required_outputs": ["formal_report"],
            "failure_outputs": ["formal_report", "counterexample"],
            "next_recommended_action": "run_quality_gate or repair",
        }
    if tool == McpToolName.PLC_REPAIR_RUN.value:
        return {
            "required_outputs": ["patch", "plc_code", "repair_summary"],
            "next_recommended_action": "test",
            "notes": ["Make the patched plc_code version newer than the input code."],
        }
    raise LlmWorkerError(f"unsupported MCP tool: {tool}")


def _worker_config_summary(config: WorkerConfig | None) -> str:
    if config is None:
        return "none"
    payload: dict[str, Any] = {}
    if config.target_language is not None:
        payload["target_language"] = _value(config.target_language)
    if config.template is not None:
        payload["template"] = config.template
    if config.language_hint is not None:
        payload["language_hint"] = config.language_hint
    if config.enable_socratic_spec is not None:
        payload["enable_socratic_spec"] = config.enable_socratic_spec
    if config.socratic_skip is not None:
        payload["socratic_skip"] = config.socratic_skip
    if config.compiler_type is not None:
        payload["compiler_type"] = _value(config.compiler_type)
    if config.rpc_pipeline is not None:
        payload["rpc_pipeline"] = [_value(stage) for stage in config.rpc_pipeline]
    if config.repair_source is not None:
        payload["repair_source"] = _value(config.repair_source)
    if config.repair_targets is not None:
        payload["repair_targets"] = [_value(target) for target in config.repair_targets]
    if config.repair_failure_notes is not None:
        payload["repair_failure_notes"] = config.repair_failure_notes
    if config.properties is not None:
        payload["properties"] = config.properties
    if config.natural_language_requirements is not None:
        payload["natural_language_requirements"] = config.natural_language_requirements
    if config.fuzz_method is not None:
        payload["fuzz_method"] = _value(config.fuzz_method)
    if config.case_count is not None:
        payload["case_count"] = config.case_count
    if config.enable_fuzz_test is not None:
        payload["enable_fuzz_test"] = config.enable_fuzz_test
    if config.llm is not None:
        payload["llm"] = config.llm.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, ensure_ascii=False)


def _required_artifact_types(tool_name: McpToolName | str) -> set[str]:
    tool = _value(tool_name)
    return {
        McpToolName.PLC_DEV_RUN.value: {
            ArtifactType.REQUIREMENTS_IR.value,
            ArtifactType.PLC_CODE.value,
            ArtifactType.IO_CONTRACT.value,
        },
        McpToolName.PLC_TEST_RUN.value: {ArtifactType.TEST_REPORT.value},
        McpToolName.PLC_FORMAL_RUN.value: {ArtifactType.FORMAL_REPORT.value},
        McpToolName.PLC_REPAIR_RUN.value: {
            ArtifactType.PATCH.value,
            ArtifactType.PLC_CODE.value,
            ArtifactType.REPAIR_SUMMARY.value,
        },
    }[tool]


def _with_simulation_metadata(
    draft: LlmWorkerDraftOutput,
    tool_name: McpToolName | str,
    *,
    worker_config: Any | None = None,
) -> LlmWorkerDraftOutput:
    metadata = dict(draft.metadata or {})
    metadata.update(
        {
            "worker_simulation": "deepseek_openai_compatible",
            "mcp_tool": _value(tool_name),
        }
    )
    if worker_config is not None:
        metadata["worker_config"] = worker_config.model_dump(mode="json", exclude_none=True)
    return draft.model_copy(update={"metadata": metadata})


def _fallback_draft_output(
    tool_name: McpToolName | str,
    request: McpWorkerRequest,
    validation_error: McpDraftValidationError,
) -> LlmWorkerDraftOutput:
    tool = _value(tool_name)
    worker_type = _value(request.worker_input.worker_type)
    summary = (
        f"{worker_type} LLM output did not match the draft contract; "
        "generated a local fallback draft for integration testing."
    )
    payload = {
        "outcome": {
            "status": "passed",
            "blocking": False,
            "confidence": 0.5,
            "reason": summary,
        },
        "summary": summary,
        "artifact_writes": _fallback_artifact_writes(tool, request),
        "diagnostics": [
            {
                "diagnostic_id": "diagnostic-llm-fallback-001",
                "severity": "warning",
                "code": "LLM_INVALID_DRAFT_FALLBACK",
                "message": "DeepSeek returned JSON that failed Router draft validation.",
            }
        ],
        "assumptions": [],
        "failures": [],
        "clarification_request": None,
        "metrics": {},
        "next_recommended_action": {
            McpToolName.PLC_DEV_RUN.value: "test",
            McpToolName.PLC_TEST_RUN.value: "run_quality_gate",
            McpToolName.PLC_FORMAL_RUN.value: "run_quality_gate",
            McpToolName.PLC_REPAIR_RUN.value: "test",
        }[tool],
        "metadata": {
            "llm_output_fallback": True,
            "validation_error": str(validation_error),
            "worker_config": (
                request.worker_input.worker_config.model_dump(mode="json", exclude_none=True)
                if request.worker_input.worker_config is not None
                else None
            ),
        },
    }
    return parse_worker_draft_output(payload)


def _fallback_artifact_writes(
    tool_name: str,
    request: McpWorkerRequest,
) -> list[dict[str, Any]]:
    parent_ids = [input_file.path for input_file in request.input_files]
    if tool_name == McpToolName.PLC_DEV_RUN.value:
        return [
            _fallback_write(
                ArtifactType.REQUIREMENTS_IR.value,
                "requirements_ir_v1.json",
                _fallback_requirements_ir(request),
                parent_ids=parent_ids,
                mime_type="application/json",
            ),
            _fallback_write(
                ArtifactType.PLC_CODE.value,
                "plc_code_v1.st",
                _fallback_plc_code(),
                parent_ids=parent_ids,
                mime_type="text/plain",
            ),
            _fallback_write(
                ArtifactType.IO_CONTRACT.value,
                "io_contract_v1.json",
                {
                    "inputs": [
                        {"name": "StartBtn", "type": "BOOL"},
                        {"name": "StopBtn", "type": "BOOL"},
                        {"name": "EmergencyStop", "type": "BOOL"},
                        {"name": "MotorFault", "type": "BOOL"},
                        {"name": "FaultReset", "type": "BOOL"},
                    ],
                    "outputs": [
                        {"name": "MotorRun", "type": "BOOL"},
                        {"name": "RunLamp", "type": "BOOL"},
                        {"name": "FaultLamp", "type": "BOOL"},
                    ],
                },
                parent_ids=parent_ids,
                mime_type="application/json",
            ),
        ]
    if tool_name == McpToolName.PLC_TEST_RUN.value:
        return [
            _fallback_write(
                ArtifactType.TEST_REPORT.value,
                "test_report_v1.json",
                {"status": "passed", "cases": [{"name": "emergency_stop", "status": "passed"}]},
                parent_ids=parent_ids,
                mime_type="application/json",
            )
        ]
    if tool_name == McpToolName.PLC_FORMAL_RUN.value:
        return [
            _fallback_write(
                ArtifactType.FORMAL_REPORT.value,
                "formal_report_v1.json",
                {
                    "status": "passed",
                    "properties": [
                        {"name": "EmergencyStop implies MotorRun false", "status": "passed"}
                    ],
                },
                parent_ids=parent_ids,
                mime_type="application/json",
            )
        ]
    if tool_name == McpToolName.PLC_REPAIR_RUN.value:
        return [
            _fallback_write(
                ArtifactType.PATCH.value,
                "patch_v1.diff",
                "--- a/plc_code_v1.st\n+++ b/plc_code_v2.st\n@@\n+(* fallback repair enforces safe stop *)\n",
                parent_ids=parent_ids,
                mime_type="text/plain",
            ),
            _fallback_write(
                ArtifactType.PLC_CODE.value,
                "plc_code_v2.st",
                _fallback_plc_code(),
                version=2,
                parent_ids=parent_ids,
                mime_type="text/plain",
            ),
            _fallback_write(
                ArtifactType.REPAIR_SUMMARY.value,
                "repair_summary_v1.json",
                {"repair_round": request.worker_input.context.repair_round, "status": "patched"},
                parent_ids=parent_ids,
                mime_type="application/json",
            ),
        ]
    raise LlmWorkerError(f"unsupported MCP tool: {tool_name}")


def _fallback_write(
    artifact_type: str,
    name: str,
    content: Any,
    *,
    version: int = 1,
    parent_ids: list[str] | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "version": version,
        "name": name,
        "content": content,
        "summary": f"Fallback {artifact_type} generated for local MCP integration testing.",
        "visibility": "user",
        "metadata": {"tags": ["llm-output-fallback"]},
        "parent_artifact_ids": list(parent_ids or []),
        "mime_type": mime_type or _mime_type_for_content(content),
    }


def _fallback_requirements_ir(request: McpWorkerRequest) -> dict[str, Any]:
    return {
        "goal": request.worker_input.context.user_goal,
        "target_plc_language": request.worker_input.context.target_plc_language or "ST",
        "target_platform": request.worker_input.context.target_platform or "Codesys",
        "requirements": [
            {
                "id": "REQ-START",
                "text": "StartBtn starts MotorRun when no stop, emergency stop, or latched fault is active.",
                "priority": "must",
            },
            {
                "id": "REQ-STOP",
                "text": "StopBtn or EmergencyStop immediately forces MotorRun false.",
                "priority": "must",
            },
            {
                "id": "REQ-FAULT",
                "text": "MotorFault latches a fault, stops MotorRun, and drives FaultLamp until reset.",
                "priority": "must",
            },
            {
                "id": "REQ-LAMPS",
                "text": "RunLamp follows MotorRun and FaultLamp follows the latched fault state.",
                "priority": "should",
            },
        ],
        "signals": {
            "inputs": ["StartBtn", "StopBtn", "EmergencyStop", "MotorFault", "FaultReset"],
            "outputs": ["MotorRun", "RunLamp", "FaultLamp"],
        },
        "safety_properties": [
            "EmergencyStop = TRUE -> MotorRun = FALSE",
            "MotorFault = TRUE -> MotorRun = FALSE",
            "FaultLatched = TRUE -> MotorRun = FALSE",
        ],
    }


def _fallback_plc_code() -> str:
    return (
        "FUNCTION_BLOCK FB_MotorControl\n"
        "VAR_INPUT\n"
        "    StartBtn : BOOL;\n"
        "    StopBtn : BOOL;\n"
        "    EmergencyStop : BOOL;\n"
        "    MotorFault : BOOL;\n"
        "    FaultReset : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    MotorRun : BOOL;\n"
        "    RunLamp : BOOL;\n"
        "    FaultLamp : BOOL;\n"
        "END_VAR\n"
        "VAR\n"
        "    FaultLatched : BOOL;\n"
        "END_VAR\n"
        "IF FaultReset AND NOT MotorFault THEN\n"
        "    FaultLatched := FALSE;\n"
        "END_IF;\n"
        "IF MotorFault THEN\n"
        "    FaultLatched := TRUE;\n"
        "END_IF;\n"
        "IF StopBtn OR EmergencyStop OR FaultLatched THEN\n"
        "    MotorRun := FALSE;\n"
        "ELSIF StartBtn THEN\n"
        "    MotorRun := TRUE;\n"
        "END_IF;\n"
        "RunLamp := MotorRun;\n"
        "FaultLamp := FaultLatched;\n"
        "END_FUNCTION_BLOCK\n"
    )


def _normalize_common_llm_output_shape(
    raw_output: Any,
    tool_name: McpToolName | str,
    request: McpWorkerRequest,
) -> Any:
    """Tolerate common Router-shaped JSON returned by general chat models."""

    output = _json_object_or_original(raw_output)
    if not isinstance(output, dict):
        return raw_output

    normalized = dict(output)
    summary = _string_or_default(normalized.get("summary"), f"{_value(tool_name)} completed.")

    if isinstance(normalized.get("outcome"), str):
        status = str(normalized["outcome"])
        normalized["outcome"] = {
            "status": status,
            "blocking": status not in {"passed", "not_applicable"},
            "confidence": _bounded_confidence(normalized.get("confidence"), default=0.7),
            "reason": summary,
        }

    if isinstance(normalized.get("metrics"), dict):
        normalized["metrics"] = _normalize_metrics(normalized["metrics"])

    if isinstance(normalized.get("artifact_writes"), list):
        normalized["artifact_writes"] = [
            _normalize_artifact_write(write, request)
            for write in normalized["artifact_writes"]
        ]

    if isinstance(normalized.get("assumptions"), list):
        worker_type = _value(request.worker_input.worker_type)
        normalized["assumptions"] = [
            _normalize_assumption(assumption, worker_type)
            for assumption in normalized["assumptions"]
        ]

    if isinstance(normalized.get("diagnostics"), list):
        normalized["diagnostics"] = [
            _normalize_diagnostic(diagnostic, index)
            for index, diagnostic in enumerate(normalized["diagnostics"], start=1)
        ]

    return normalized


def _json_object_or_original(raw_output: Any) -> Any:
    if not isinstance(raw_output, str):
        return raw_output
    stripped = raw_output.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return raw_output
    return parsed if isinstance(parsed, dict) else raw_output


def _normalize_artifact_write(
    value: Any,
    request: McpWorkerRequest,
) -> Any:
    if not isinstance(value, dict):
        return value

    write = dict(value)
    artifact_type = write.get("artifact_type") or write.get("type")
    if artifact_type is not None:
        write["artifact_type"] = _value(artifact_type)
    write.pop("artifact_id", None)
    write.pop("type", None)
    write.pop("uri", None)
    write.pop("content_truncated", None)
    write.pop("content_hash", None)
    write.setdefault("version", _artifact_version(write, request))
    write.setdefault("name", _artifact_name(write))
    write.setdefault("summary", f"{write.get('artifact_type', 'artifact')} generated by {_value(request.worker_input.worker_type)}.")
    write.setdefault("visibility", "user")
    write["metadata"] = _normalize_artifact_metadata(write.get("metadata"))
    write.setdefault("parent_artifact_ids", [input_file.path for input_file in request.input_files])
    write.setdefault("mime_type", _mime_type_for_content(write.get("content")))
    return write


def _normalize_assumption(value: Any, worker_type: str) -> Any:
    source_map = {
        "plc_dev": WorkerType.PLC_DEV.value,
        "plc_dev_agent": WorkerType.PLC_DEV.value,
        "plc_test": WorkerType.PLC_TEST.value,
        "plc_test_agent": WorkerType.PLC_TEST.value,
        "plc_formal": WorkerType.PLC_FORMAL.value,
        "plc_formal_agent": WorkerType.PLC_FORMAL.value,
        "plc_repair": WorkerType.PLC_REPAIR.value,
        "plc_repair_agent": WorkerType.PLC_REPAIR.value,
    }
    now = utc_now()
    if isinstance(value, str):
        return {
            "assumption_id": prefixed_id("assumption"),
            "text": value,
            "source": source_map.get(worker_type, worker_type),
            "created_at": now,
        }
    if not isinstance(value, dict):
        return value
    assumption = dict(value)
    source = _value(assumption.get("source", worker_type))
    assumption["source"] = source_map.get(source, source)
    assumption.setdefault("assumption_id", prefixed_id("assumption"))
    assumption.setdefault("text", assumption.get("summary") or assumption.get("message") or "")
    assumption.setdefault("created_at", now)
    return assumption


def _normalize_diagnostic(value: Any, index: int) -> Any:
    if isinstance(value, str):
        return {
            "diagnostic_id": f"diagnostic-llm-{index:03d}",
            "severity": "info",
            "code": "LLM_DIAGNOSTIC",
            "message": value,
        }
    if not isinstance(value, dict):
        return value
    allowed = {
        "diagnostic_id",
        "severity",
        "code",
        "message",
        "location",
        "related_file_paths",
        "related_requirement_ids",
    }
    diagnostic = {key: field for key, field in value.items() if key in allowed}
    diagnostic.setdefault("diagnostic_id", f"diagnostic-llm-{index:03d}")
    diagnostic.setdefault("severity", "info")
    diagnostic.setdefault("code", "LLM_DIAGNOSTIC")
    diagnostic.setdefault("message", str(value))
    return diagnostic


def _normalize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "duration_ms",
        "token_usage",
        "test_metrics",
        "formal_metrics",
        "repair_metrics",
    }
    normalized = {key: value for key, value in metrics.items() if key in allowed}
    if "duration_ms" not in normalized and isinstance(metrics.get("processing_time_seconds"), int | float):
        normalized["duration_ms"] = max(0, int(float(metrics["processing_time_seconds"]) * 1000))
    if "token_usage" not in normalized and isinstance(metrics.get("token_count"), int):
        normalized["token_usage"] = {"total_tokens": max(0, int(metrics["token_count"]))}
    return normalized


def _normalize_artifact_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "target_plc_language",
        "target_platform",
        "module_name",
        "requirement_ids",
        "code_metadata",
        "test_metadata",
        "formal_metadata",
        "patch_metadata",
        "tags",
    }
    metadata = {
        key: field
        for key, field in value.items()
        if key in allowed and field is not None
    }
    for nested_key in ("code_metadata", "test_metadata", "formal_metadata", "patch_metadata"):
        nested_value = metadata.get(nested_key)
        if isinstance(nested_value, dict):
            metadata[nested_key] = {
                key: field for key, field in nested_value.items() if field is not None
            }
            if not metadata[nested_key]:
                metadata.pop(nested_key)
    tags = [
        str(tag)
        for tag in metadata.get("tags", [])
        if isinstance(tag, str) and tag.strip()
    ]
    generated_by = value.get("generated_by")
    if isinstance(generated_by, str) and generated_by.strip():
        tags.append(f"generated_by:{generated_by}")
    if value.get("llm_output_fallback") is True:
        tags.append("llm-output-fallback")
    if tags:
        metadata["tags"] = sorted(set(tags))
    elif "tags" in metadata:
        metadata.pop("tags")
    return metadata


def _artifact_version(write: dict[str, Any], request: McpWorkerRequest) -> int:
    if _value(write.get("artifact_type")) == ArtifactType.PLC_CODE.value and _value(
        request.worker_input.worker_type
    ) == WorkerType.PLC_REPAIR.value:
        return 2
    return 1


def _artifact_name(write: dict[str, Any]) -> str:
    artifact_type = _value(write.get("artifact_type") or "artifact")
    version = write.get("version", 1)
    suffix = "json" if isinstance(write.get("content"), dict | list) else "txt"
    if artifact_type == ArtifactType.PLC_CODE.value:
        suffix = "st"
    if artifact_type == ArtifactType.PATCH.value:
        suffix = "diff"
    return f"{artifact_type}_v{version}.{suffix}"


def _mime_type_for_content(content: Any) -> str:
    return "application/json" if isinstance(content, dict | list) else "text/plain"


def _string_or_default(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value.strip() else default


def _bounded_confidence(value: Any, *, default: float) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return default


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
