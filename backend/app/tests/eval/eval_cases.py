"""Typed loader for backend PLC eval task definitions."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agents.output_schema import IntakeClassificationOutput
from app.mcp.mock_worker import (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    SCENARIO_WORKER_TIMEOUT,
)
from app.models.router_schema import (
    ArtifactType,
    DifficultyLevel,
    EventType,
    TaskStatus,
    TaskType,
    WorkerExecutionStatus,
    WorkerType,
)


DEFAULT_CASE_FILE = Path(__file__).with_name("plc_tasks.yaml")
CASE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SCRIPTED_ACTIONS = {
    "dev",
    "test",
    "formal",
    "repair",
    "repair_limit_rejected",
    "finalizing",
    "gate",
}
SUPPORTED_MOCK_SCENARIOS = {
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
    SCENARIO_WORKER_TIMEOUT,
}
SUPPORTED_INVARIANTS = {
    "l3_requires_formal",
    "repair_requires_regression",
    "formal_repair_requires_formal_regression",
    "no_success_without_quality_gate",
    "final_report_before_terminal_event",
    "no_worker_for_clarification",
    "no_fourth_repair_round",
    "no_false_success_on_worker_error",
}
REQUIRED_WORKFLOW_TAGS = {
    "qa",
    "ordinary_development",
    "emergency_stop",
    "fault_latch",
    "interlock",
    "mode_switch",
    "sequence",
    "timer",
    "counter",
    "clarification",
    "modify_existing_code",
    "test_failure_repair",
    "formal_counterexample_repair",
    "repair_budget_exhausted",
    "worker_timeout",
}
TOOL_STATUSES = {"applied", "rejected", "failed", "no-op"}

DEFAULT_SIGNALS = {
    "has_existing_code": False,
    "has_io_points": False,
    "has_timing_logic": False,
    "has_state_machine": False,
    "has_safety_constraints": False,
    "has_emergency_stop": False,
    "has_interlock": False,
    "has_fault_latching": False,
    "has_mode_switching": False,
    "multi_module": False,
    "requirement_incomplete": False,
}


class EvalCaseValidationError(ValueError):
    """Raised when the eval corpus cannot be parsed or validated."""


class EvalBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class EvalExpected(EvalBaseModel):
    final_status: list[TaskStatus] = Field(min_length=1)
    min_difficulty: DifficultyLevel | None = None
    worker_sequence: list[WorkerType] | None = None
    required_workers: list[WorkerType] = Field(default_factory=list)
    forbidden_workers: list[WorkerType] = Field(default_factory=list)
    required_artifacts: list[ArtifactType] = Field(default_factory=list)
    required_event_subsequence: list[EventType] = Field(default_factory=list)
    artifact_versions: dict[str, list[int]] = Field(default_factory=dict)
    invariants: list[str] = Field(default_factory=list)
    expected_rejections: dict[str, str] = Field(default_factory=dict)
    expected_tool_statuses: dict[str, str] = Field(default_factory=dict)
    expected_execution_statuses: dict[str, WorkerExecutionStatus] = Field(
        default_factory=dict
    )

    @field_validator("artifact_versions")
    @classmethod
    def validate_artifact_version_keys(
        cls,
        value: dict[str, list[int]],
    ) -> dict[str, list[int]]:
        allowed = _enum_values(ArtifactType)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown artifact version keys: {unknown}")
        return value

    @field_validator("invariants")
    @classmethod
    def validate_invariants(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - SUPPORTED_INVARIANTS)
        if unknown:
            raise ValueError(f"unknown invariant names: {unknown}")
        return value

    @field_validator(
        "expected_rejections",
        "expected_tool_statuses",
        "expected_execution_statuses",
    )
    @classmethod
    def validate_action_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(value) - SCRIPTED_ACTIONS)
        if unknown:
            raise ValueError(f"unknown scripted action keys: {unknown}")
        return value

    @field_validator("expected_tool_statuses")
    @classmethod
    def validate_tool_statuses(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value.values()) - TOOL_STATUSES)
        if unknown:
            raise ValueError(f"unknown expected tool statuses: {unknown}")
        return value


class EvalCase(EvalBaseModel):
    id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    message: str = Field(min_length=1)
    project_context: dict[str, Any] = Field(default_factory=dict)
    eval_mode: Literal["deterministic_mock", "live_provider"] = "deterministic_mock"
    mock_scenario: str = SCENARIO_DEV_TEST_PASS
    scripted_classification: dict[str, Any] = Field(default_factory=dict)
    scripted_sequence: list[str] = Field(default_factory=list)
    scripted_final_status: TaskStatus | None = None
    expected: EvalExpected

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not CASE_ID_RE.match(value):
            raise ValueError(
                "case id must be snake_case starting with a lowercase letter"
            )
        return value

    @field_validator("mock_scenario")
    @classmethod
    def validate_mock_scenario(cls, value: str) -> str:
        if value not in SUPPORTED_MOCK_SCENARIOS:
            raise ValueError(f"unsupported mock scenario: {value}")
        return value

    @field_validator("scripted_sequence")
    @classmethod
    def validate_scripted_sequence(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - SCRIPTED_ACTIONS)
        if unknown:
            raise ValueError(f"unknown scripted actions: {unknown}")
        return value

    @model_validator(mode="after")
    def validate_case(self) -> EvalCase:
        _ = self.intake_classification()
        unknown_rejections = sorted(
            set(self.expected.expected_rejections) - set(self.scripted_sequence)
        )
        if unknown_rejections:
            raise ValueError(
                "expected rejection actions are not in scripted_sequence: "
                f"{unknown_rejections}"
            )
        unknown_tool_statuses = sorted(
            set(self.expected.expected_tool_statuses) - set(self.scripted_sequence)
        )
        if unknown_tool_statuses:
            raise ValueError(
                "expected tool status actions are not in scripted_sequence: "
                f"{unknown_tool_statuses}"
            )
        unknown_execution_statuses = sorted(
            set(self.expected.expected_execution_statuses) - set(self.scripted_sequence)
        )
        if unknown_execution_statuses:
            raise ValueError(
                "expected execution status actions are not in scripted_sequence: "
                f"{unknown_execution_statuses}"
            )
        return self

    def intake_classification(self) -> IntakeClassificationOutput:
        payload = _default_classification_payload(self)
        payload.update(self.scripted_classification)
        payload["difficulty_signals"] = {
            **DEFAULT_SIGNALS,
            **payload.get("difficulty_signals", {}),
        }
        return IntakeClassificationOutput.model_validate(payload)

    def runner_final_status(self) -> str | None:
        if self.scripted_final_status is not None:
            return str(self.scripted_final_status)
        expected_status = self.expected.final_status[0]
        if expected_status == TaskStatus.WAITING_USER.value:
            return None
        return str(expected_status)


def load_eval_cases(path: Path = DEFAULT_CASE_FILE) -> list[EvalCase]:
    """Load and validate the repository eval case corpus."""

    return parse_eval_cases_text(
        path.read_text(encoding="utf-8"),
        source=str(path),
        validate_corpus=True,
    )


def parse_eval_cases_text(
    text: str,
    *,
    source: str = "<memory>",
    validate_corpus: bool = False,
) -> list[EvalCase]:
    """Parse JSON-valid YAML text into typed eval cases."""

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvalCaseValidationError(
            f"{source}: eval corpus must be JSON-compatible YAML: {exc}"
        ) from exc

    if isinstance(raw, dict):
        raw_cases = raw.get("cases")
    else:
        raw_cases = raw
    if not isinstance(raw_cases, list):
        raise EvalCaseValidationError(f"{source}: eval corpus must contain a cases list")

    cases: list[EvalCase] = []
    for index, item in enumerate(raw_cases, start=1):
        try:
            cases.append(EvalCase.model_validate(item))
        except ValidationError as exc:
            raise EvalCaseValidationError(
                f"{source}: invalid eval case at index {index}: {exc}"
            ) from exc
        except ValueError as exc:
            raise EvalCaseValidationError(
                f"{source}: invalid eval case at index {index}: {exc}"
            ) from exc

    _validate_unique_case_ids(cases, source=source)
    if validate_corpus:
        _validate_corpus_coverage(cases, source=source)
    return cases


def _validate_unique_case_ids(cases: list[EvalCase], *, source: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for case in cases:
        if case.id in seen:
            duplicates.append(case.id)
        seen.add(case.id)
    if duplicates:
        raise EvalCaseValidationError(
            f"{source}: duplicate eval case ids: {sorted(set(duplicates))}"
        )


def _validate_corpus_coverage(cases: list[EvalCase], *, source: str) -> None:
    if len(cases) < 15:
        raise EvalCaseValidationError(
            f"{source}: eval corpus must contain at least 15 cases"
        )
    present_tags = {tag for case in cases for tag in case.tags}
    missing = sorted(REQUIRED_WORKFLOW_TAGS - present_tags)
    if missing:
        raise EvalCaseValidationError(
            f"{source}: eval corpus is missing workflow tags: {missing}"
        )


def _default_classification_payload(case: EvalCase) -> dict[str, Any]:
    return {
        "normalized_goal": case.title,
        "task_type": TaskType.NEW_PLC_DEVELOPMENT.value,
        "difficulty_level": DifficultyLevel.L2.value,
        "difficulty_score": 0.55,
        "difficulty_confidence": 0.86,
        "difficulty_reasons": ["Eval case default classification."],
        "difficulty_signals": {**DEFAULT_SIGNALS, "has_io_points": True},
        "requires_test": True,
        "requires_formal": False,
        "requires_repair_loop": False,
        "need_clarification": False,
        "clarification_questions": [],
    }


def _enum_values(enum_type: Any) -> set[str]:
    return {str(member.value) for member in enum_type}
