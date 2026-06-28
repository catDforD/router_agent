export type {
  AgentSession,
  AgentSessionRunRef,
  AgentSessionStatus,
  Artifact,
  ArtifactRef,
  ArtifactType,
  ArtifactVisibility,
  ClarificationQuestion,
  CurrentArtifacts,
  CurrentFiles,
  EventCorrelation,
  EventSeverity,
  EventType,
  Failure,
  GateState,
  JsonValue,
  ProjectContext,
  RouterEvent,
  TaskPhase,
  TaskState,
  TaskStatus,
  TokenUsage,
  WorkerJobRef,
  WorkerType,
} from "../../../../schema/ts/router_contract";

import type {
  AgentSession,
  Artifact,
  EventCorrelation,
  JsonValue,
  ProjectContext,
  TaskState,
  WorkerType,
} from "../../../../schema/ts/router_contract";

export interface HealthResponse {
  status: string;
  app: string;
  env: string;
}

export interface SubagentStatusWorker {
  worker_type: WorkerType;
  agent_id: string;
  route: "mock" | "real" | "subagent" | string;
  status: string;
  online?: boolean | null;
  latency_ms?: number | null;
  status_code?: number | null;
  error?: string | null;
  probe_scope?: string | null;
}

export interface SubagentStatusProbe {
  method: string;
  path: string;
  scope?: string;
  status: string;
  online?: boolean | null;
  latency_ms?: number | null;
  status_code?: number | null;
  error?: string | null;
  checked_at: string;
}

export interface SubagentStatusResponse {
  mode: string;
  base_url: string;
  probe: SubagentStatusProbe;
  workers: SubagentStatusWorker[];
}

export interface CreateTaskRequest {
  message: string;
  project_context?: ProjectContext;
}

export interface CreateTaskResponse {
  task_id: string;
  status: string;
  events_url: string;
}

export interface TaskListResponse {
  tasks: TaskState[];
}

export interface CreateSessionResponse {
  session: AgentSession;
  task: TaskState;
  task_id: string;
  run_id: string;
  events_url: string;
}

export interface SessionResponse {
  session: AgentSession;
  latest_task?: TaskState | null;
}

export interface ListSessionsResponse {
  sessions: AgentSession[];
}

export interface AppendSessionMessageResponse {
  session: AgentSession;
  task: TaskState;
  task_id: string;
  run_id: string;
}

export interface AppendUserMessageResponse {
  task: TaskState;
  message_path: string;
}

export interface ArtifactListResponse {
  task_id: string;
  artifacts: Artifact[];
}

export interface ArtifactContentResponse {
  artifact: Artifact;
  content: string;
  content_encoding: "utf-8";
  mime_type?: string | null;
  size_bytes?: number | null;
  content_hash?: string | null;
}

export interface TraceEventSummary {
  event_id: string;
  seq: number;
  type: string;
  source_type: string;
  source_id?: string | null;
  source_worker_type?: string | null;
  severity: string;
  visibility: string;
  title: string;
  message?: string | null;
  correlation: EventCorrelation;
  payload_keys: string[];
  created_at: string;
}

export interface TraceWorkerJobSummary {
  worker_job_id: string;
  worker_type: string;
  status: string;
  mcp_tool: string;
  openai_trace_id?: string | null;
  main_agent_run_id?: string | null;
  mcp_request_id?: string | null;
  input_paths: string[];
  read_paths: string[];
  written_paths: string[];
  report_paths: string[];
  failure_ids: string[];
  execution_status?: string | null;
  outcome_status?: string | null;
  error_code?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TraceGateResultSummary {
  gate_result_id: string;
  gate_type: string;
  status: string;
  blocking: boolean;
  evidence_paths: string[];
  created_at: string;
}

export interface TraceMainAgentRunSummary {
  main_agent_run_id: string;
  openai_trace_id?: string | null;
  started_event_id?: string | null;
  started_seq?: number | null;
  started_at?: string | null;
  completed_event_id?: string | null;
  completed_seq?: number | null;
  completed_at?: string | null;
  error_event_ids: string[];
  final_report_path?: string | null;
  replay_log_path?: string | null;
}

export interface TraceFileSummary {
  path: string;
  exists?: boolean | null;
  size_bytes?: number | null;
  mime_type?: string | null;
}

export interface TaskTraceSummary {
  task_id: string;
  openai_trace_id?: string | null;
  main_agent_run_ids: string[];
  latest_main_agent_run_id?: string | null;
  terminal_event_id?: string | null;
  terminal_event_type?: string | null;
  main_agent_runs: TraceMainAgentRunSummary[];
  worker_jobs: TraceWorkerJobSummary[];
  files: TraceFileSummary[];
  gate_results: TraceGateResultSummary[];
  events: TraceEventSummary[];
}

export interface ApiErrorPayload {
  detail?: string | { msg?: string }[] | Record<string, JsonValue>;
}
