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
} from "../../../../schema/ts/router_contract";

export interface HealthResponse {
  status: string;
  app: string;
  env: string;
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
  message_artifact_id: string;
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

export interface TraceArtifactSummary {
  artifact_id: string;
  type: string;
  version: number;
  status: string;
  visibility: string;
  uri: string;
  content_hash?: string | null;
  size_bytes?: number | null;
  summary: string;
  parent_artifact_ids: string[];
  derived_from_worker_job_id?: string | null;
  derived_from_artifact_ids?: string[] | null;
  created_by_type: string;
  created_by_id?: string | null;
  created_by_worker_job_id?: string | null;
  created_by_main_agent_run_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TraceWorkerJobSummary {
  worker_job_id: string;
  worker_type: string;
  status: string;
  mcp_tool: string;
  openai_trace_id?: string | null;
  main_agent_run_id?: string | null;
  mcp_request_id?: string | null;
  input_artifact_ids: string[];
  produced_artifact_ids: string[];
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
  evidence_artifact_ids: string[];
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
  final_report_artifact_id?: string | null;
  replay_log_artifact_id?: string | null;
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
  artifacts: TraceArtifactSummary[];
  gate_results: TraceGateResultSummary[];
  events: TraceEventSummary[];
}

export interface ApiErrorPayload {
  detail?: string | { msg?: string }[] | Record<string, JsonValue>;
}
