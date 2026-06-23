
"""Pydantic models for the Router v1 cross-service contract."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


# ---------------------------------------------------------------------------
# 0. 通用基础类型
#
# 五类主 schema 共享这些枚举和轻量对象。系统边界之间尽量传 ArtifactRef，
# 不直接传大段 PLC 代码、日志、trace 或报告正文。
# ---------------------------------------------------------------------------

DEFAULT_SCHEMA_VERSION = "router.v2"
SchemaVersion = Literal["router.v2", "router.v1"]


class RouterBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class WorkerType(str, Enum):
    PLC_DEV = "plc-dev"
    PLC_TEST = "plc-test"
    PLC_FORMAL = "plc-formal"
    PLC_REPAIR = "plc-repair"


class McpToolName(str, Enum):
    PLC_DEV_RUN = "plc_dev.run"
    PLC_TEST_RUN = "plc_test.run"
    PLC_FORMAL_RUN = "plc_formal.run"
    PLC_REPAIR_RUN = "plc_repair.run"


class ArtifactType(str, Enum):
    RAW_USER_REQUEST = "raw_user_request"
    REQUIREMENTS_IR = "requirements_ir"
    PLC_CODE = "plc_code"
    IO_CONTRACT = "io_contract"
    PLC_PROJECT_BUNDLE = "plc_project_bundle"
    TEST_CASES = "test_cases"
    TEST_REPORT = "test_report"
    FAILING_TRACE = "failing_trace"
    FORMAL_PROPERTIES = "formal_properties"
    FORMAL_MODEL = "formal_model"
    FORMAL_REPORT = "formal_report"
    COUNTEREXAMPLE = "counterexample"
    PATCH = "patch"
    REPAIR_SUMMARY = "repair_summary"
    GATE_REPORT = "gate_report"
    FINAL_REPORT = "final_report"
    WORKER_LOG = "worker_log"
    MAIN_AGENT_LOG = "main_agent_log"
    MISC = "misc"


class DifficultyLevel(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class TaskType(str, Enum):
    QA = "qa"
    NEW_PLC_DEVELOPMENT = "new_plc_development"
    MODIFY_EXISTING_CODE = "modify_existing_code"
    TEST_EXISTING_CODE = "test_existing_code"
    FORMAL_VERIFY_EXISTING_CODE = "formal_verify_existing_code"
    REPAIR_EXISTING_CODE = "repair_existing_code"
    PROJECT_LEVEL_DEVELOPMENT = "project_level_development"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


class NextRecommendedAction(str, Enum):
    FINISH = "finish"
    TEST = "test"
    FORMAL = "formal"
    REPAIR = "repair"
    ASK_USER = "ask_user"
    RETRY = "retry"
    RUN_QUALITY_GATE = "run_quality_gate"
    NONE = "none"


class AssumptionSource(str, Enum):
    USER = "user"
    MAIN_AGENT = "main_agent"
    PLC_DEV = "plc-dev"
    PLC_TEST = "plc-test"
    PLC_FORMAL = "plc-formal"
    PLC_REPAIR = "plc-repair"
    RUNTIME = "runtime"


class ClarificationStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class FailureSource(str, Enum):
    COMPILE = "compile"
    TEST = "test"
    FORMAL = "formal"
    REQUIREMENT = "requirement"
    REPAIR = "repair"
    RUNTIME = "runtime"
    UNKNOWN = "unknown"


class FailureStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"


class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPhase(str, Enum):
    INTAKE = "intake"
    CLARIFYING = "clarifying"
    PLANNING = "planning"
    DEVELOPING = "developing"
    TESTING = "testing"
    FORMAL_VERIFYING = "formal_verifying"
    REPAIRING = "repairing"
    REGRESSION = "regression"
    QUALITY_GATE = "quality_gate"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"


class WorkerJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TargetPlcLanguage(str, Enum):
    ST = "ST"
    LD = "LD"
    FBD = "FBD"
    SFC = "SFC"
    IL = "IL"
    UNKNOWN = "unknown"


class WorkerMode(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    ANALYZE = "analyze"
    TEST = "test"
    REGRESSION_TEST = "regression_test"
    FORMAL_VERIFY = "formal_verify"
    FORMAL_REGRESSION = "formal_regression"
    REPAIR = "repair"
    EXPLAIN = "explain"


class WorkerConstraintType(str, Enum):
    PLATFORM = "platform"
    LANGUAGE = "language"
    STYLE = "style"
    INTERFACE = "interface"
    SAFETY_PROPERTY = "safety_property"
    TESTING = "testing"
    FORMAL = "formal"
    REPAIR = "repair"
    RUNTIME = "runtime"
    OTHER = "other"


class ConstraintSeverity(str, Enum):
    SOFT = "soft"
    HARD = "hard"


class WorkerExecutionStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class WorkerOutcomeStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NEED_CLARIFICATION = "need_clarification"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class ArtifactStatus(str, Enum):
    PROCESSING = "processing"
    AVAILABLE = "available"
    FAILED = "failed"
    ARCHIVED = "archived"


class ArtifactVisibility(str, Enum):
    USER = "user"
    INTERNAL = "internal"


class ArtifactStorageProvider(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    MINIO = "minio"
    DATABASE = "database"
    MEMORY = "memory"


class ArtifactCreatorType(str, Enum):
    USER = "user"
    MAIN_AGENT = "main_agent"
    RUNTIME = "runtime"
    WORKER = "worker"


class PassFailUnknownStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class EventSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class EventVisibility(str, Enum):
    USER = "user"
    INTERNAL = "internal"


class EventType(str, Enum):
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_WAITING_USER = "task.waiting_user"
    TASK_SUCCEEDED = "task.succeeded"
    TASK_PARTIAL_FAILED = "task.partial_failed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    MAIN_AGENT_STARTED = "agent.started"
    MAIN_AGENT_DECISION = "agent.decision"
    MAIN_AGENT_PLAN_UPDATED = "agent.plan_updated"
    MAIN_AGENT_CLARIFICATION_REQUESTED = "agent.clarification_requested"
    MAIN_AGENT_FINALIZING = "agent.finalizing"
    MAIN_AGENT_TURN_STARTED = "agent.turn_started"
    MAIN_AGENT_MESSAGE = "agent.message"
    MAIN_AGENT_TOOL_CALLED = "agent.tool_called"
    MAIN_AGENT_TOOL_RESULT = "agent.tool_result"
    MAIN_AGENT_COMPLETED = "agent.completed"
    LEGACY_MAIN_AGENT_STARTED = "main_agent.started"
    LEGACY_MAIN_AGENT_DECISION = "main_agent.decision"
    LEGACY_MAIN_AGENT_PLAN_UPDATED = "main_agent.plan_updated"
    LEGACY_MAIN_AGENT_CLARIFICATION_REQUESTED = "main_agent.clarification_requested"
    LEGACY_MAIN_AGENT_FINALIZING = "main_agent.finalizing"
    LEGACY_MAIN_AGENT_TURN_STARTED = "main_agent.turn_started"
    LEGACY_MAIN_AGENT_MESSAGE = "main_agent.message"
    LEGACY_MAIN_AGENT_TOOL_CALLED = "main_agent.tool_called"
    LEGACY_MAIN_AGENT_TOOL_RESULT = "main_agent.tool_result"
    LEGACY_MAIN_AGENT_COMPLETED = "main_agent.completed"
    WORKER_JOB_CREATED = "worker.job_created"
    WORKER_STARTED = "worker.started"
    WORKER_PROGRESS = "worker.progress"
    WORKER_COMPLETED = "worker.completed"
    WORKER_PARTIAL = "worker.partial"
    WORKER_ERROR = "worker.error"
    WORKER_TIMEOUT = "worker.timeout"
    WORKER_CANCELLED = "worker.cancelled"
    ARTIFACT_PROCESSING = "artifact.processing"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_AVAILABLE = "artifact.available"
    ARTIFACT_FAILED = "artifact.failed"
    GATE_STARTED = "gate.started"
    GATE_PASSED = "gate.passed"
    GATE_FAILED = "gate.failed"
    REPAIR_ROUND_STARTED = "repair.round_started"
    REPAIR_ROUND_COMPLETED = "repair.round_completed"
    REPAIR_ROUND_FAILED = "repair.round_failed"


class EventSourceType(str, Enum):
    FRONTEND = "frontend"
    MAIN_AGENT = "main_agent"
    RUNTIME = "runtime"
    MCP_ADAPTER = "mcp_adapter"
    WORKER = "worker"
    QUALITY_GATE = "quality_gate"


# worker_type 和 MCP tool 必须一一对应，Runtime 在调用前后都会校验。
WORKER_TOOL_BY_TYPE = {
    WorkerType.PLC_DEV.value: McpToolName.PLC_DEV_RUN.value,
    WorkerType.PLC_TEST.value: McpToolName.PLC_TEST_RUN.value,
    WorkerType.PLC_FORMAL.value: McpToolName.PLC_FORMAL_RUN.value,
    WorkerType.PLC_REPAIR.value: McpToolName.PLC_REPAIR_RUN.value,
}


class ArtifactRef(RouterBaseModel):
    artifact_id: str
    type: ArtifactType
    version: int = Field(ge=1)
    uri: str | None = None
    summary: str | None = None
    content_hash: str | None = None


class Assumption(RouterBaseModel):
    assumption_id: str
    text: str
    source: AssumptionSource
    confidence: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime


class ClarificationQuestion(RouterBaseModel):
    question_id: str
    question: str
    reason: str
    required: bool
    status: ClarificationStatus
    answer: str | None = None
    asked_at: datetime | None = None
    answered_at: datetime | None = None


class FailureReproduction(RouterBaseModel):
    steps: list[str] | None = None
    input_trace_artifact_id: str | None = None
    counterexample_artifact_id: str | None = None


class Failure(RouterBaseModel):
    failure_id: str
    source: FailureSource
    severity: Severity
    title: str
    description: str
    requirement_ids: list[str] | None = None
    expected: str | None = None
    actual: str | None = None
    reproduction: FailureReproduction | None = None
    evidence_artifact_ids: list[str]
    status: FailureStatus
    created_by_worker_job_id: str | None = None
    resolved_by_worker_job_id: str | None = None
    resolved_by_artifact_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


# ---------------------------------------------------------------------------
# 1. TaskState
#
# TaskState 是 Runtime 和 Main Agent 共享的任务运行状态。它记录当前阶段、
# 关键 artifact、worker job、失败项、追问、修复轮次和质量门禁；大内容仍然
# 应该留在 Artifact 存储中。
# ---------------------------------------------------------------------------

class DifficultySignals(RouterBaseModel):
    has_existing_code: bool
    has_io_points: bool
    has_timing_logic: bool
    has_state_machine: bool
    has_safety_constraints: bool
    has_emergency_stop: bool
    has_interlock: bool
    has_fault_latching: bool
    has_mode_switching: bool
    multi_module: bool
    requirement_incomplete: bool


class DifficultyProfile(RouterBaseModel):
    level: DifficultyLevel
    score: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reasons: list[str]
    signals: DifficultySignals
    requires_test: bool
    requires_formal: bool
    requires_repair_loop: bool
    need_clarification: bool


class RuntimeLimits(RouterBaseModel):
    max_repair_rounds: int = Field(ge=0, le=3)
    repair_rounds: int = Field(ge=0)
    max_parallel_workers: int = Field(ge=1, le=4)
    active_parallel_workers: int = Field(ge=0)
    max_worker_calls: int = Field(ge=0)
    worker_calls_used: int = Field(ge=0)
    task_timeout_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_limits(self) -> RuntimeLimits:
        # 这些上限不能只靠 Main Agent prompt 保证，Runtime 必须硬校验。
        if self.repair_rounds > self.max_repair_rounds:
            raise ValueError("repair_rounds cannot exceed max_repair_rounds")
        if self.active_parallel_workers > self.max_parallel_workers:
            raise ValueError(
                "active_parallel_workers cannot exceed max_parallel_workers"
            )
        if self.worker_calls_used > self.max_worker_calls:
            raise ValueError("worker_calls_used cannot exceed max_worker_calls")
        return self


class GateState(RouterBaseModel):
    test_required: bool
    formal_required: bool
    regression_required: bool
    formal_regression_required: bool
    latest_test_passed: bool | None = None
    latest_formal_passed: bool | None = None
    has_blocking_failure: bool
    can_finish_as_success: bool


class CurrentArtifacts(RouterBaseModel):
    raw_user_request: ArtifactRef | None = None
    requirements_ir: ArtifactRef | None = None
    current_code: ArtifactRef | None = None
    current_io_contract: ArtifactRef | None = None
    latest_test_cases: ArtifactRef | None = None
    latest_test_report: ArtifactRef | None = None
    latest_failing_trace: ArtifactRef | None = None
    latest_formal_properties: ArtifactRef | None = None
    latest_formal_report: ArtifactRef | None = None
    latest_counterexample: ArtifactRef | None = None
    latest_patch: ArtifactRef | None = None
    latest_repair_summary: ArtifactRef | None = None
    latest_gate_report: ArtifactRef | None = None
    final_report: ArtifactRef | None = None
    all_artifact_ids: list[str]


class WorkerJobRef(RouterBaseModel):
    worker_job_id: str
    worker_type: WorkerType
    status: WorkerJobStatus
    objective: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ProjectContext(RouterBaseModel):
    project_id: str | None = None
    project_name: str | None = None
    target_plc_language: TargetPlcLanguage | None = None
    target_platform: str | None = Field(
        default=None,
        description=(
            "Known values include Codesys, Siemens, Beckhoff, Mitsubishi, Omron, "
            "Rockwell, and unknown; arbitrary platform strings are allowed."
        ),
    )
    coding_style_artifact_id: str | None = None
    project_memory_artifact_ids: list[str] | None = None
    workspace_root: str | None = None


class ExecutionPolicy(RouterBaseModel):
    mode: Literal["disabled", "local_read_only", "local_full_access"] = "disabled"
    command_timeout_seconds: int = Field(default=120, ge=1)
    tool_output_max_chars: int = Field(default=12_000, ge=1)
    allow_network: bool | None = None


class WorkspaceContext(RouterBaseModel):
    root: str
    current_directory: str | None = None
    writable: bool = False


class AgentToolCallRecord(RouterBaseModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    status: Literal["queued", "running", "applied", "rejected", "failed", "no-op"]
    summary: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class AgentRunState(RouterBaseModel):
    agent_run_id: str
    status: Literal["running", "waiting_user", "succeeded", "partial_failed", "failed", "cancelled"]
    workspace: WorkspaceContext | None = None
    execution_policy: ExecutionPolicy | None = None
    tool_calls: list[AgentToolCallRecord]
    started_at: datetime
    completed_at: datetime | None = None


class TaskTrace(RouterBaseModel):
    openai_trace_id: str | None = None
    main_agent_run_ids: list[str]
    latest_main_agent_run_id: str | None = None


class TaskState(RouterBaseModel):
    schema_version: SchemaVersion
    task_id: str
    session_id: str
    user_id: str | None = None
    title: str | None = None
    status: TaskStatus
    phase: TaskPhase
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    raw_user_request: str
    normalized_goal: str | None = None
    task_type: TaskType
    difficulty: DifficultyProfile
    project_context: ProjectContext
    workspace: WorkspaceContext | None = None
    execution_policy: ExecutionPolicy | None = None
    agent_runs: list[AgentRunState] = Field(default_factory=list)
    runtime_limits: RuntimeLimits
    gates: GateState
    current_artifacts: CurrentArtifacts
    active_worker_jobs: list[WorkerJobRef]
    completed_worker_job_ids: list[str]
    assumptions: list[Assumption]
    unresolved_questions: list[ClarificationQuestion]
    failures: list[Failure]
    trace: TaskTrace
    event_seq: int = Field(ge=0)
    metadata: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_task_state_rules(self) -> TaskState:
        # 任务状态的关键流转规则在这里兜底，避免状态被推进到不合法阶段。
        if self.gates.has_blocking_failure and self.status == TaskStatus.SUCCEEDED.value:
            raise ValueError("status cannot be succeeded when a blocking failure exists")

        if (
            self.phase in {TaskPhase.TESTING.value, TaskPhase.FORMAL_VERIFYING.value}
            and self.current_artifacts.current_code is None
        ):
            raise ValueError("testing/formal_verifying phases require current_code")

        if self.phase == TaskPhase.REPAIRING.value:
            if self.current_artifacts.current_code is None:
                raise ValueError("repairing phase requires current_code")
            has_open_blocking_failure = any(
                failure.status == FailureStatus.OPEN.value
                and failure.severity == Severity.BLOCKING.value
                for failure in self.failures
            )
            if not has_open_blocking_failure:
                raise ValueError(
                    "repairing phase requires at least one open blocking failure"
                )

        return self


# ---------------------------------------------------------------------------
# 2. WorkerInput
#
# WorkerInput 是 Backend Runtime 发给外部 MCP worker 的统一请求格式。
# Main Agent 决定“调用谁、做什么”，Runtime 负责补齐 job、artifact、预算、
# trace、约束和幂等 key，并在真正调用前校验最小输入要求。
# ---------------------------------------------------------------------------

class WorkerConstraint(RouterBaseModel):
    constraint_id: str
    type: WorkerConstraintType
    description: str
    severity: ConstraintSeverity


class ExpectedOutputSpec(RouterBaseModel):
    artifact_type: ArtifactType
    required: bool
    description: str
    schema_ref: str | None = None


class WorkerBudget(RouterBaseModel):
    timeout_seconds: int = Field(ge=1)
    max_iterations: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)


class ModuleScope(RouterBaseModel):
    module_name: str | None = None
    included_io_points: list[str] | None = None
    included_requirements: list[str] | None = None
    excluded_requirements: list[str] | None = None


class QualityRequirements(RouterBaseModel):
    must_compile: bool | None = None
    must_generate_tests: bool | None = None
    must_run_tests: bool | None = None
    must_formal_verify: bool | None = None
    must_return_counterexample_on_failure: bool | None = None
    must_use_minimal_patch: bool | None = None


class WorkerContext(RouterBaseModel):
    user_goal: str
    task_type: TaskType
    difficulty_level: DifficultyLevel
    requirement_summary: str | None = None
    target_plc_language: str | None = None
    target_platform: str | None = None
    module_scope: ModuleScope | None = None
    repair_round: int | None = Field(default=None, ge=0)
    assumptions: list[Assumption]
    selected_failure_ids: list[str] | None = None
    quality_requirements: QualityRequirements | None = None


class TraceContext(RouterBaseModel):
    openai_trace_id: str | None = None
    main_agent_run_id: str | None = None
    worker_job_id: str | None = None
    mcp_request_id: str | None = None
    parent_event_id: str | None = None


class WorkerInput(RouterBaseModel):
    schema_version: SchemaVersion
    task_id: str
    worker_job_id: str
    worker_type: WorkerType
    mcp_tool: McpToolName
    mode: WorkerMode
    objective: str
    input_artifacts: list[ArtifactRef]
    context: WorkerContext
    constraints: list[WorkerConstraint]
    expected_outputs: list[ExpectedOutputSpec]
    budget: WorkerBudget
    trace_context: TraceContext
    idempotency_key: str
    created_at: datetime
    metadata: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_worker_input_rules(self) -> WorkerInput:
        # 不同 worker 有不同的最小输入要求，这里提前拦截错误调用。
        expected_tool = WORKER_TOOL_BY_TYPE[self.worker_type]
        if self.mcp_tool != expected_tool:
            raise ValueError(
                f"mcp_tool must be {expected_tool!r} for worker_type "
                f"{self.worker_type!r}"
            )

        artifact_types = {artifact.type for artifact in self.input_artifacts}
        if self.worker_type == WorkerType.PLC_DEV.value:
            if not {
                ArtifactType.RAW_USER_REQUEST.value,
                ArtifactType.REQUIREMENTS_IR.value,
            } & artifact_types:
                raise ValueError(
                    "plc-dev requires raw_user_request or requirements_ir artifact"
                )
        elif self.worker_type in {
            WorkerType.PLC_TEST.value,
            WorkerType.PLC_FORMAL.value,
        }:
            required = {ArtifactType.REQUIREMENTS_IR.value, ArtifactType.PLC_CODE.value}
            if not required <= artifact_types:
                raise ValueError(
                    f"{self.worker_type} requires requirements_ir and plc_code artifacts"
                )
        elif self.worker_type == WorkerType.PLC_REPAIR.value:
            evidence_types = {
                ArtifactType.TEST_REPORT.value,
                ArtifactType.FAILING_TRACE.value,
                ArtifactType.FORMAL_REPORT.value,
                ArtifactType.COUNTEREXAMPLE.value,
            }
            if ArtifactType.PLC_CODE.value not in artifact_types:
                raise ValueError("plc-repair requires a plc_code artifact")
            if not evidence_types & artifact_types:
                raise ValueError(
                    "plc-repair requires failure evidence from test/formal artifacts"
                )

        return self


# ---------------------------------------------------------------------------
# 3. WorkerResult
#
# WorkerResult 是外部 worker 返回给 Runtime 的统一结果格式。
# execution_status 表示工具调用本身是否成功；outcome.status 表示业务结果，
# 例如测试是否通过、形式化验证是否发现反例、是否需要追问。
# ---------------------------------------------------------------------------

class WorkerOutcome(RouterBaseModel):
    status: WorkerOutcomeStatus
    blocking: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None


class DiagnosticLocation(RouterBaseModel):
    artifact_id: str | None = None
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    symbol: str | None = None


class Diagnostic(RouterBaseModel):
    diagnostic_id: str
    severity: Severity
    code: str
    message: str
    location: DiagnosticLocation | None = None
    related_artifact_ids: list[str] | None = None
    related_requirement_ids: list[str] | None = None


class TokenUsage(RouterBaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class TestMetrics(RouterBaseModel):
    total: int | None = Field(default=None, ge=0)
    passed: int | None = Field(default=None, ge=0)
    failed: int | None = Field(default=None, ge=0)
    skipped: int | None = Field(default=None, ge=0)
    coverage_score: float | None = Field(default=None, ge=0, le=1)


class FormalMetrics(RouterBaseModel):
    total_properties: int | None = Field(default=None, ge=0)
    passed_properties: int | None = Field(default=None, ge=0)
    failed_properties: int | None = Field(default=None, ge=0)
    unknown_properties: int | None = Field(default=None, ge=0)


class RepairMetrics(RouterBaseModel):
    changed_files: int | None = Field(default=None, ge=0)
    changed_lines: int | None = Field(default=None, ge=0)
    patch_size_bytes: int | None = Field(default=None, ge=0)


class WorkerMetrics(RouterBaseModel):
    duration_ms: int | None = Field(default=None, ge=0)
    token_usage: TokenUsage | None = None
    test_metrics: TestMetrics | None = None
    formal_metrics: FormalMetrics | None = None
    repair_metrics: RepairMetrics | None = None


class ClarificationRequest(RouterBaseModel):
    blocking: bool
    questions: list[ClarificationQuestion]


class WorkerError(RouterBaseModel):
    error_code: str
    message: str
    retryable: bool
    details: dict[str, JsonValue] | None = None


class WorkerResult(RouterBaseModel):
    schema_version: SchemaVersion
    task_id: str
    worker_job_id: str
    worker_type: WorkerType
    mcp_tool: McpToolName
    execution_status: WorkerExecutionStatus
    outcome: WorkerOutcome
    summary: str
    produced_artifacts: list[ArtifactRef]
    diagnostics: list[Diagnostic]
    assumptions: list[Assumption]
    failures: list[Failure]
    clarification_request: ClarificationRequest | None = None
    metrics: WorkerMetrics
    next_recommended_action: NextRecommendedAction
    error: WorkerError | None = None
    trace_context: TraceContext
    started_at: datetime
    completed_at: datetime
    metadata: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_worker_result_rules(self) -> WorkerResult:
        # 返回结果也要确认 worker_type 和 mcp_tool 对得上，防止接错 worker。
        expected_tool = WORKER_TOOL_BY_TYPE[self.worker_type]
        if self.mcp_tool != expected_tool:
            raise ValueError(
                f"mcp_tool must be {expected_tool!r} for worker_type "
                f"{self.worker_type!r}"
            )
        return self


# ---------------------------------------------------------------------------
# 4. Artifact
#
# Artifact 是系统产物的事实来源。代码、测试报告、形式化报告、反例、patch、
# 最终报告等都应该登记为 Artifact。内容按版本不可变，大内容放 storage.uri，
# inline_content 只放小 JSON 或短摘要。
# ---------------------------------------------------------------------------

class ArtifactStorage(RouterBaseModel):
    provider: ArtifactStorageProvider
    uri: str
    bucket: str | None = None
    path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = None


class ArtifactCreator(RouterBaseModel):
    type: ArtifactCreatorType
    id: str | None = None
    worker_type: WorkerType | None = None
    worker_job_id: str | None = None
    main_agent_run_id: str | None = None


class CodeMetadata(RouterBaseModel):
    code_version: int | None = Field(default=None, ge=1)
    is_current: bool | None = None
    compile_status: PassFailUnknownStatus | None = None
    entry_function_block: str | None = None


class TestArtifactMetadata(RouterBaseModel):
    total: int | None = Field(default=None, ge=0)
    passed: int | None = Field(default=None, ge=0)
    failed: int | None = Field(default=None, ge=0)
    coverage_score: float | None = Field(default=None, ge=0, le=1)
    status: PassFailUnknownStatus | None = None


class FormalArtifactMetadata(RouterBaseModel):
    total_properties: int | None = Field(default=None, ge=0)
    passed_properties: int | None = Field(default=None, ge=0)
    failed_properties: int | None = Field(default=None, ge=0)
    status: PassFailUnknownStatus | None = None


class PatchMetadata(RouterBaseModel):
    from_code_artifact_id: str | None = None
    to_code_artifact_id: str | None = None
    changed_files: int | None = Field(default=None, ge=0)
    changed_lines: int | None = Field(default=None, ge=0)
    repair_round: int | None = Field(default=None, ge=0)


class ArtifactMetadata(RouterBaseModel):
    target_plc_language: str | None = None
    target_platform: str | None = None
    module_name: str | None = None
    requirement_ids: list[str] | None = None
    code_metadata: CodeMetadata | None = None
    test_metadata: TestArtifactMetadata | None = None
    formal_metadata: FormalArtifactMetadata | None = None
    patch_metadata: PatchMetadata | None = None
    tags: list[str] | None = None


class Artifact(RouterBaseModel):
    schema_version: SchemaVersion
    artifact_id: str
    task_id: str
    type: ArtifactType
    version: int = Field(ge=1)
    name: str
    display_name: str | None = None
    status: ArtifactStatus
    visibility: ArtifactVisibility
    storage: ArtifactStorage
    summary: str
    parent_artifact_ids: list[str]
    derived_from_worker_job_id: str | None = None
    derived_from_artifact_ids: list[str] | None = None
    created_by: ArtifactCreator
    created_at: datetime
    updated_at: datetime
    metadata: ArtifactMetadata
    inline_content: JsonValue | None = None


# ---------------------------------------------------------------------------
# 5. RouterEvent
#
# RouterEvent 用于前端时间线、调试、回放和 trace 关联。
# Event 应该 append-only：新状态写新事件，不回头修改旧事件。
# ---------------------------------------------------------------------------

class EventSource(RouterBaseModel):
    type: EventSourceType
    id: str | None = None
    worker_type: WorkerType | None = None


class EventCorrelation(RouterBaseModel):
    parent_event_id: str | None = None
    openai_trace_id: str | None = None
    main_agent_run_id: str | None = None
    worker_job_id: str | None = None
    mcp_request_id: str | None = None
    artifact_ids: list[str] | None = None
    failure_ids: list[str] | None = None


class RouterEvent(RouterBaseModel):
    schema_version: SchemaVersion
    event_id: str
    task_id: str
    seq: int = Field(ge=0)
    type: EventType
    source: EventSource
    severity: EventSeverity
    visibility: EventVisibility
    title: str
    message: str | None = None
    correlation: EventCorrelation
    payload: dict[str, JsonValue]
    created_at: datetime


ROUTER_V1_SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "task_state": TaskState,
    "worker_input": WorkerInput,
    "worker_result": WorkerResult,
    "artifact": Artifact,
    "event": RouterEvent,
}
