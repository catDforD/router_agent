/**
 * Router v1 TypeScript reference contract.
 *
 * This file is kept as a human-readable companion to the Python Pydantic
 * models in backend/models/router_schema.py and the generated JSON Schema
 * files under schema/.
 *
 * Source of truth for backend validation: backend/models/router_schema.py
 * Language-neutral contract: schema/*.schema.json
 * Human-readable TS reference: this file
 */

/**
 * 0. 通用基础类型
 *
 * 这些类型会被五类主 schema 共享。重点是统一枚举值和轻量引用：
 * Main Agent、Runtime、Worker 之间尽量传 ArtifactRef，不直接传大段代码、
 * 日志、trace 或报告正文。
 */
export type SchemaVersion = "router.v1";
export type ISODateTime = string;

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type WorkerType =
  | "plc-dev"
  | "plc-test"
  | "plc-formal"
  | "plc-repair";

export type McpToolName =
  | "plc_dev.run"
  | "plc_test.run"
  | "plc_formal.run"
  | "plc_repair.run";

export type ArtifactType =
  | "raw_user_request"
  | "requirements_ir"
  | "plc_code"
  | "io_contract"
  | "plc_project_bundle"
  | "test_cases"
  | "test_report"
  | "failing_trace"
  | "formal_properties"
  | "formal_model"
  | "formal_report"
  | "counterexample"
  | "patch"
  | "repair_summary"
  | "gate_report"
  | "final_report"
  | "worker_log"
  | "main_agent_log"
  | "misc";

export type DifficultyLevel = "L0" | "L1" | "L2" | "L3" | "L4";

export type TaskType =
  | "qa"
  | "new_plc_development"
  | "modify_existing_code"
  | "test_existing_code"
  | "formal_verify_existing_code"
  | "repair_existing_code"
  | "project_level_development"
  | "unknown";

export type Severity =
  | "debug"
  | "info"
  | "warning"
  | "error"
  | "blocking";

export type NextRecommendedAction =
  | "finish"
  | "test"
  | "formal"
  | "repair"
  | "ask_user"
  | "retry"
  | "run_quality_gate"
  | "none";

export interface ArtifactRef {
  artifact_id: string;
  type: ArtifactType;
  version: number;
  uri?: string | null;
  summary?: string | null;
  content_hash?: string | null;
}

export interface Assumption {
  assumption_id: string;
  text: string;
  source:
    | "user"
    | "main_agent"
    | "plc-dev"
    | "plc-test"
    | "plc-formal"
    | "plc-repair"
    | "runtime";
  confidence?: number | null;
  created_at: ISODateTime;
}

export interface ClarificationQuestion {
  question_id: string;
  question: string;
  reason: string;
  required: boolean;
  status: "open" | "answered" | "skipped";
  answer?: string | null;
  asked_at?: ISODateTime | null;
  answered_at?: ISODateTime | null;
}

export interface Failure {
  failure_id: string;

  source:
    | "compile"
    | "test"
    | "formal"
    | "requirement"
    | "repair"
    | "runtime"
    | "unknown";

  severity: Severity;

  title: string;
  description: string;

  requirement_ids?: string[];
  expected?: string | null;
  actual?: string | null;

  reproduction?: {
    steps?: string[];
    input_trace_artifact_id?: string | null;
    counterexample_artifact_id?: string | null;
  } | null;

  evidence_artifact_ids: string[];

  status: "open" | "resolved" | "waived";

  created_by_worker_job_id?: string | null;
  resolved_by_worker_job_id?: string | null;
  resolved_by_artifact_id?: string | null;

  created_at: ISODateTime;
  resolved_at?: ISODateTime | null;
}

/**
 * 1. TaskState
 *
 * TaskState 是 Router Runtime 和 Main Agent 共享的任务运行状态。
 * 它记录任务当前走到哪一步、已有产物、worker job、失败项、追问、
 * 修复轮次和质量门禁。这里不要保存大段正文，大内容应该放 Artifact。
 */
export type TaskStatus =
  | "created"
  | "running"
  | "waiting_user"
  | "succeeded"
  | "partial_failed"
  | "failed"
  | "cancelled";

export type TaskPhase =
  | "intake"
  | "clarifying"
  | "planning"
  | "developing"
  | "testing"
  | "formal_verifying"
  | "repairing"
  | "regression"
  | "quality_gate"
  | "synthesizing"
  | "completed";

export interface DifficultyProfile {
  level: DifficultyLevel;
  score?: number | null;
  confidence?: number | null;

  reasons: string[];

  signals: {
    has_existing_code: boolean;
    has_io_points: boolean;
    has_timing_logic: boolean;
    has_state_machine: boolean;
    has_safety_constraints: boolean;
    has_emergency_stop: boolean;
    has_interlock: boolean;
    has_fault_latching: boolean;
    has_mode_switching: boolean;
    multi_module: boolean;
    requirement_incomplete: boolean;
  };

  requires_test: boolean;
  requires_formal: boolean;
  requires_repair_loop: boolean;
  need_clarification: boolean;
}

export interface RuntimeLimits {
  max_repair_rounds: number;
  repair_rounds: number;

  max_parallel_workers: number;
  active_parallel_workers: number;

  max_worker_calls: number;
  worker_calls_used: number;

  task_timeout_seconds?: number | null;
}

export interface GateState {
  test_required: boolean;
  formal_required: boolean;
  regression_required: boolean;
  formal_regression_required: boolean;

  latest_test_passed?: boolean | null;
  latest_formal_passed?: boolean | null;

  has_blocking_failure: boolean;
  can_finish_as_success: boolean;
}

export interface CurrentArtifacts {
  raw_user_request?: ArtifactRef | null;
  requirements_ir?: ArtifactRef | null;

  current_code?: ArtifactRef | null;
  current_io_contract?: ArtifactRef | null;

  latest_test_cases?: ArtifactRef | null;
  latest_test_report?: ArtifactRef | null;
  latest_failing_trace?: ArtifactRef | null;

  latest_formal_properties?: ArtifactRef | null;
  latest_formal_report?: ArtifactRef | null;
  latest_counterexample?: ArtifactRef | null;

  latest_patch?: ArtifactRef | null;
  latest_repair_summary?: ArtifactRef | null;

  latest_gate_report?: ArtifactRef | null;
  final_report?: ArtifactRef | null;

  all_artifact_ids: string[];
}

export interface WorkerJobRef {
  worker_job_id: string;
  worker_type: WorkerType;
  status:
    | "queued"
    | "running"
    | "completed"
    | "partial"
    | "error"
    | "timeout"
    | "cancelled";

  objective: string;
  started_at?: ISODateTime | null;
  completed_at?: ISODateTime | null;
}

export interface ProjectContext {
  project_id?: string | null;
  project_name?: string | null;

  target_plc_language?:
    | "ST"
    | "LD"
    | "FBD"
    | "SFC"
    | "IL"
    | "unknown";

  target_platform?:
    | "Codesys"
    | "Siemens"
    | "Beckhoff"
    | "Mitsubishi"
    | "Omron"
    | "Rockwell"
    | "unknown"
    | string;

  coding_style_artifact_id?: string | null;
  project_memory_artifact_ids?: string[];
}

export interface TaskState {
  schema_version: SchemaVersion;

  task_id: string;
  session_id: string;
  user_id?: string | null;

  title?: string | null;

  status: TaskStatus;
  phase: TaskPhase;

  created_at: ISODateTime;
  updated_at: ISODateTime;
  started_at?: ISODateTime | null;
  completed_at?: ISODateTime | null;

  raw_user_request: string;
  normalized_goal?: string | null;

  task_type: TaskType;
  difficulty: DifficultyProfile;

  project_context: ProjectContext;

  runtime_limits: RuntimeLimits;
  gates: GateState;

  current_artifacts: CurrentArtifacts;

  active_worker_jobs: WorkerJobRef[];
  completed_worker_job_ids: string[];

  assumptions: Assumption[];
  unresolved_questions: ClarificationQuestion[];
  failures: Failure[];

  trace: {
    openai_trace_id?: string | null;
    main_agent_run_ids: string[];
    latest_main_agent_run_id?: string | null;
  };

  event_seq: number;

  metadata?: Record<string, JsonValue>;
}

/**
 * 2. WorkerInput
 *
 * WorkerInput 是 Backend Runtime 发给外部 MCP worker 的统一请求格式。
 * Main Agent 决定调用哪个 worker、目标是什么；Runtime 负责补齐 job id、
 * artifact 引用、预算、trace、约束和幂等 key，并在调用前做 schema 校验。
 */
export type WorkerMode =
  | "create"
  | "modify"
  | "analyze"
  | "test"
  | "regression_test"
  | "formal_verify"
  | "formal_regression"
  | "repair"
  | "explain";

export interface WorkerConstraint {
  constraint_id: string;

  type:
    | "platform"
    | "language"
    | "style"
    | "interface"
    | "safety_property"
    | "testing"
    | "formal"
    | "repair"
    | "runtime"
    | "other";

  description: string;
  severity: "soft" | "hard";
}

export interface ExpectedOutputSpec {
  artifact_type: ArtifactType;
  required: boolean;
  description: string;
  schema_ref?: string | null;
}

export interface WorkerBudget {
  timeout_seconds: number;
  max_iterations?: number | null;
  max_tokens?: number | null;
  max_cost_usd?: number | null;
}

export interface WorkerContext {
  user_goal: string;
  task_type: TaskType;
  difficulty_level: DifficultyLevel;

  requirement_summary?: string | null;
  target_plc_language?: string | null;
  target_platform?: string | null;

  module_scope?: {
    module_name?: string | null;
    included_io_points?: string[];
    included_requirements?: string[];
    excluded_requirements?: string[];
  } | null;

  repair_round?: number | null;

  assumptions: Assumption[];

  selected_failure_ids?: string[];

  quality_requirements?: {
    must_compile?: boolean;
    must_generate_tests?: boolean;
    must_run_tests?: boolean;
    must_formal_verify?: boolean;
    must_return_counterexample_on_failure?: boolean;
    must_use_minimal_patch?: boolean;
  };
}

export interface WorkerLLMConfig {
  model?: string | null;
  base_url?: string | null;
  temperature?: number | null;
  timeout_seconds?: number | null;
  max_retries?: number | null;
}

export interface WorkerConfig {
  target_language?: "ST" | "SCL" | "FBD" | null;
  template?: string | null;
  language_hint?: string | null;
  enable_socratic_spec?: boolean | null;
  socratic_skip?: boolean | null;
  compiler_type?: "matiec" | "rusty" | null;
  rpc_pipeline?: Array<"fuzz" | "formal"> | null;
  repair_source?:
    | "compile"
    | "test_failure"
    | "formal_validation_failure"
    | "multi"
    | null;
  repair_targets?: Array<
    "compile" | "test_failure" | "formal_validation_failure"
  > | null;
  repair_failure_notes?: string | null;
  properties?: JsonValue | null;
  natural_language_requirements?: string | null;
  fuzz_method?: "random" | "boundary" | "scenario" | "dse" | "afl" | "llm" | null;
  case_count?: number | null;
  enable_fuzz_test?: boolean | null;
  llm?: WorkerLLMConfig | null;
}

export interface TraceContext {
  openai_trace_id?: string | null;
  main_agent_run_id?: string | null;
  worker_job_id?: string | null;
  mcp_request_id?: string | null;
  parent_event_id?: string | null;
}

export interface WorkerInput {
  schema_version: SchemaVersion;

  task_id: string;
  worker_job_id: string;

  worker_type: WorkerType;
  mcp_tool: McpToolName;

  mode: WorkerMode;

  objective: string;

  input_artifacts: ArtifactRef[];

  context: WorkerContext;

  constraints: WorkerConstraint[];

  expected_outputs: ExpectedOutputSpec[];

  budget: WorkerBudget;

  trace_context: TraceContext;

  idempotency_key: string;

  created_at: ISODateTime;

  worker_config?: WorkerConfig | null;

  metadata?: Record<string, JsonValue>;
}

/**
 * 3. WorkerResult
 *
 * WorkerResult 是外部 worker 返回给 Runtime 的统一结果格式。
 * execution_status 表示工具调用本身是否成功；outcome.status 表示业务结果，
 * 例如测试是否通过、形式化验证是否发现反例、是否需要继续追问。
 */
export type WorkerExecutionStatus =
  | "completed"
  | "partial"
  | "error"
  | "timeout"
  | "cancelled";

export type WorkerOutcomeStatus =
  | "passed"
  | "failed"
  | "need_clarification"
  | "not_applicable"
  | "unknown";

export interface WorkerOutcome {
  status: WorkerOutcomeStatus;

  blocking: boolean;

  confidence?: number | null;

  reason?: string | null;
}

export interface Diagnostic {
  diagnostic_id: string;

  severity: Severity;

  code: string;

  message: string;

  location?: {
    artifact_id?: string | null;
    file_path?: string | null;
    line_start?: number | null;
    line_end?: number | null;
    symbol?: string | null;
  } | null;

  related_artifact_ids?: string[];
  related_requirement_ids?: string[];
}

export interface WorkerMetrics {
  duration_ms?: number | null;

  token_usage?: {
    input_tokens?: number | null;
    output_tokens?: number | null;
    total_tokens?: number | null;
  } | null;

  test_metrics?: {
    total?: number | null;
    passed?: number | null;
    failed?: number | null;
    skipped?: number | null;
    coverage_score?: number | null;
  } | null;

  formal_metrics?: {
    total_properties?: number | null;
    passed_properties?: number | null;
    failed_properties?: number | null;
    unknown_properties?: number | null;
  } | null;

  repair_metrics?: {
    changed_files?: number | null;
    changed_lines?: number | null;
    patch_size_bytes?: number | null;
  } | null;
}

export interface ClarificationRequest {
  blocking: boolean;
  questions: ClarificationQuestion[];
}

export interface WorkerError {
  error_code: string;
  message: string;
  retryable: boolean;
  details?: Record<string, JsonValue>;
}

export interface WorkerResult {
  schema_version: SchemaVersion;

  task_id: string;
  worker_job_id: string;

  worker_type: WorkerType;
  mcp_tool: McpToolName;

  execution_status: WorkerExecutionStatus;

  outcome: WorkerOutcome;

  summary: string;

  produced_artifacts: ArtifactRef[];

  diagnostics: Diagnostic[];

  assumptions: Assumption[];

  failures: Failure[];

  clarification_request?: ClarificationRequest | null;

  metrics: WorkerMetrics;

  next_recommended_action: NextRecommendedAction;

  error?: WorkerError | null;

  trace_context: TraceContext;

  started_at: ISODateTime;
  completed_at: ISODateTime;

  metadata?: Record<string, JsonValue>;
}

/**
 * 4. Artifact
 *
 * Artifact 是系统产物的事实来源。PLC 代码、测试报告、形式化报告、反例、
 * patch、最终报告等都应该登记为 Artifact。Artifact 内容按版本不可变；
 * 大内容放 storage.uri，inline_content 只放很小的 JSON 或摘要。
 */
export type ArtifactStatus =
  | "processing"
  | "available"
  | "failed"
  | "archived";

export type ArtifactVisibility = "user" | "internal";

export interface ArtifactStorage {
  provider: "local" | "s3" | "minio" | "database" | "memory";

  uri: string;

  bucket?: string | null;
  path?: string | null;

  mime_type?: string | null;
  size_bytes?: number | null;

  content_hash?: string | null;
}

export interface ArtifactCreator {
  type:
    | "user"
    | "main_agent"
    | "runtime"
    | "worker";

  id?: string | null;

  worker_type?: WorkerType | null;
  worker_job_id?: string | null;
  main_agent_run_id?: string | null;
}

export interface ArtifactMetadata {
  target_plc_language?: string | null;
  target_platform?: string | null;

  module_name?: string | null;
  requirement_ids?: string[];

  code_metadata?: {
    code_version?: number | null;
    is_current?: boolean | null;
    compile_status?: "passed" | "failed" | "unknown" | null;
    entry_function_block?: string | null;
  } | null;

  test_metadata?: {
    total?: number | null;
    passed?: number | null;
    failed?: number | null;
    coverage_score?: number | null;
    status?: "passed" | "failed" | "unknown" | null;
  } | null;

  formal_metadata?: {
    total_properties?: number | null;
    passed_properties?: number | null;
    failed_properties?: number | null;
    status?: "passed" | "failed" | "unknown" | null;
  } | null;

  patch_metadata?: {
    from_code_artifact_id?: string | null;
    to_code_artifact_id?: string | null;
    changed_files?: number | null;
    changed_lines?: number | null;
    repair_round?: number | null;
  } | null;

  tags?: string[];
}

export interface Artifact {
  schema_version: SchemaVersion;

  artifact_id: string;
  task_id: string;

  type: ArtifactType;

  version: number;

  name: string;
  display_name?: string | null;

  status: ArtifactStatus;

  visibility: ArtifactVisibility;

  storage: ArtifactStorage;

  summary: string;

  parent_artifact_ids: string[];

  derived_from_worker_job_id?: string | null;
  derived_from_artifact_ids?: string[];

  created_by: ArtifactCreator;

  created_at: ISODateTime;
  updated_at: ISODateTime;

  metadata: ArtifactMetadata;

  /**
   * Only small inline content should be stored here.
   * Large PLC code, logs, traces, and bundles should live behind storage.uri.
   */
  inline_content?: JsonValue | null;
}

/**
 * 5. RouterEvent
 *
 * RouterEvent 用于前端时间线、调试、回放和 trace 关联。
 * Event 应该 append-only：新状态写新事件，不回头修改旧事件。
 */
export type EventSeverity =
  | "debug"
  | "info"
  | "warning"
  | "error";

export type EventVisibility = "user" | "internal";

export type EventType =
  | "task.created"
  | "task.updated"
  | "task.waiting_user"
  | "task.succeeded"
  | "task.partial_failed"
  | "task.failed"
  | "task.cancelled"

  | "main_agent.started"
  | "main_agent.decision"
  | "main_agent.plan_updated"
  | "main_agent.clarification_requested"
  | "main_agent.finalizing"
  | "main_agent.turn_started"
  | "main_agent.message"
  | "main_agent.tool_called"
  | "main_agent.tool_result"
  | "main_agent.completed"

  | "worker.job_created"
  | "worker.started"
  | "worker.progress"
  | "worker.completed"
  | "worker.partial"
  | "worker.error"
  | "worker.timeout"
  | "worker.cancelled"

  | "artifact.processing"
  | "artifact.created"
  | "artifact.available"
  | "artifact.failed"

  | "gate.started"
  | "gate.passed"
  | "gate.failed"

  | "repair.round_started"
  | "repair.round_completed"
  | "repair.round_failed";

export interface EventSource {
  type:
    | "frontend"
    | "main_agent"
    | "runtime"
    | "mcp_adapter"
    | "worker"
    | "quality_gate";

  id?: string | null;
  worker_type?: WorkerType | null;
}

export interface EventCorrelation {
  parent_event_id?: string | null;

  openai_trace_id?: string | null;
  main_agent_run_id?: string | null;

  worker_job_id?: string | null;
  mcp_request_id?: string | null;

  artifact_ids?: string[];
  failure_ids?: string[];
}

export interface RouterEvent {
  schema_version: SchemaVersion;

  event_id: string;
  task_id: string;

  /**
   * Monotonically increasing sequence number within one task.
   */
  seq: number;

  type: EventType;

  source: EventSource;

  severity: EventSeverity;

  visibility: EventVisibility;

  title: string;
  message?: string | null;

  correlation: EventCorrelation;

  payload: Record<string, JsonValue>;

  created_at: ISODateTime;
}

/**
 * Runtime constraints represented by backend validators:
 *
 * 1. repair_rounds must not exceed max_repair_rounds. MVP default is 3.
 * 2. active_parallel_workers must not exceed max_parallel_workers. MVP default is 4.
 * 3. testing/formal_verifying phases require current_code.
 * 4. repairing phase requires current_code and at least one open blocking failure.
 * 5. status cannot be succeeded while gates.has_blocking_failure is true.
 * 6. after repair, gates.regression_required should be true.
 * 7. after a formal failure and repair, gates.formal_regression_required should be true.
 */
