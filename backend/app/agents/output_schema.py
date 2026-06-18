"""Internal structured outputs for Router Main Agent episodes."""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.models.router_schema import (
    DifficultyLevel,
    DifficultySignals,
    NextRecommendedAction,
    TaskStatus,
    TaskType,
)


class MainAgentOutputModel(BaseModel):
    """Strict base class for internal Main Agent outputs."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class IntakeClarificationQuestion(MainAgentOutputModel):
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required: bool = True


class IntakeClassificationOutput(MainAgentOutputModel):
    """Structured intake result before Runtime applies deterministic policy."""

    normalized_goal: str = Field(min_length=1)
    task_type: TaskType
    difficulty_level: DifficultyLevel
    difficulty_score: float | None = Field(default=None, ge=0, le=1)
    difficulty_confidence: float | None = Field(default=None, ge=0, le=1)
    difficulty_reasons: list[str] = Field(min_length=1)
    difficulty_signals: DifficultySignals
    requires_test: bool
    requires_formal: bool
    requires_repair_loop: bool
    need_clarification: bool
    clarification_questions: list[IntakeClarificationQuestion] = Field(
        default_factory=list
    )

    @field_validator("difficulty_score", "difficulty_confidence", mode="before")
    @classmethod
    def clamp_probability(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value
        return min(max(numeric, 0.0), 1.0)

    @model_validator(mode="after")
    def validate_classification(self) -> IntakeClassificationOutput:
        if self.task_type == TaskType.UNKNOWN.value:
            raise ValueError("classification task_type must not be unknown")
        if self.need_clarification and not self.clarification_questions:
            raise ValueError(
                "clarification_questions are required when need_clarification is true"
            )
        return self


class MainAgentArtifactReference(MainAgentOutputModel):
    artifact_id: str
    type: str
    version: int = Field(ge=1)
    uri: str | None = None
    summary: str | None = None
    content_hash: str | None = None


class MainAgentPlanStep(MainAgentOutputModel):
    order: int = Field(ge=1)
    action: str = Field(min_length=1)
    status: str = Field(min_length=1)
    reason: str | None = None
    worker_type: str | None = None
    tool_name: str | None = None


class MainAgentDecision(MainAgentOutputModel):
    decision_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    action: str | None = None
    tool_name: str | None = None
    artifact_refs: list[MainAgentArtifactReference] = Field(default_factory=list)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class MainAgentGateSummary(MainAgentOutputModel):
    test_required: bool
    formal_required: bool
    regression_required: bool
    formal_regression_required: bool
    latest_test_passed: bool | None = None
    latest_formal_passed: bool | None = None
    has_blocking_failure: bool
    can_finish_as_success: bool


class MainAgentEpisodeOutput(MainAgentOutputModel):
    """Structured result returned by one Main Agent episode."""

    task_id: str
    main_agent_run_id: str
    final_task_status: TaskStatus
    phase: str | None = None
    decisions: list[MainAgentDecision] = Field(default_factory=list)
    plan: list[MainAgentPlanStep] = Field(default_factory=list)
    artifact_refs: list[MainAgentArtifactReference] = Field(default_factory=list)
    gate_summary: MainAgentGateSummary | None = None
    open_clarification_question_ids: list[str] = Field(default_factory=list)
    next_recommended_action: NextRecommendedAction = NextRecommendedAction.NONE
    summary: str = Field(min_length=1)
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_waiting_user_output(self) -> MainAgentEpisodeOutput:
        if (
            self.final_task_status == TaskStatus.WAITING_USER.value
            and self.next_recommended_action != NextRecommendedAction.ASK_USER.value
        ):
            raise ValueError(
                "waiting_user episode output must recommend ask_user"
            )
        return self


def artifact_reference_from_mapping(value: dict[str, Any]) -> MainAgentArtifactReference:
    """Validate a compact artifact reference mapping."""

    return MainAgentArtifactReference.model_validate(value)
