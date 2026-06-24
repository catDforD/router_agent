"""Internal structured outputs for Router Main Agent episodes."""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from app.models.router_schema import (
    NextRecommendedAction,
    TaskStatus,
)


class MainAgentOutputModel(BaseModel):
    """Strict base class for internal Main Agent outputs."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


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
