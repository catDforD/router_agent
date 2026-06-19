import type {
  Artifact,
  ArtifactRef,
  GateState,
  RouterEvent,
  TaskState,
  TaskTraceSummary,
  WorkerType,
} from "../../api/router/types";

export interface WorkerCardModel {
  workerType: WorkerType;
  status: string;
  objective?: string;
  startedAt?: string | null;
  completedAt?: string | null;
  artifactIds: string[];
  failureIds: string[];
  summary?: string | null;
}

export interface GateSummaryModel {
  key: string;
  label: string;
  status: "passed" | "failed" | "pending" | "not_required";
  blocking?: boolean;
  evidenceArtifactIds: string[];
}

export interface RepairSummaryModel {
  rounds: number;
  maxRounds: number;
  regressionRequired: boolean;
  formalRegressionRequired: boolean;
  hasBlockingFailure: boolean;
}

const WORKER_TYPES: WorkerType[] = [
  "plc-dev",
  "plc-test",
  "plc-formal",
  "plc-repair",
];

export function buildWorkerCards(
  task: TaskState | null,
  events: RouterEvent[],
  trace: TaskTraceSummary | null,
): WorkerCardModel[] {
  return WORKER_TYPES.map((workerType) => {
    const active = task?.active_worker_jobs.find(
      (job) => job.worker_type === workerType,
    );
    const traceJobs =
      trace?.worker_jobs.filter((job) => job.worker_type === workerType) ?? [];
    const latestTraceJob = traceJobs.at(-1);
    const relatedEvents = events.filter(
      (event) =>
        event.source.worker_type === workerType ||
        event.payload.worker_type === workerType,
    );
    const latestEvent = relatedEvents.at(-1);

    return {
      workerType,
      status: active?.status ?? latestTraceJob?.status ?? eventStatus(latestEvent),
      objective: active?.objective,
      startedAt: active?.started_at ?? latestTraceJob?.started_at,
      completedAt: active?.completed_at ?? latestTraceJob?.completed_at,
      artifactIds: unique([
        ...(latestTraceJob?.produced_artifact_ids ?? []),
        ...relatedEvents.flatMap((event) => event.correlation.artifact_ids ?? []),
      ]),
      failureIds: unique([
        ...(latestTraceJob?.failure_ids ?? []),
        ...relatedEvents.flatMap((event) => event.correlation.failure_ids ?? []),
      ]),
      summary:
        (typeof latestEvent?.payload.summary === "string"
          ? latestEvent.payload.summary
          : latestEvent?.message) ?? null,
    };
  });
}

export function buildGateSummaries(
  gates: GateState | undefined,
  trace: TaskTraceSummary | null,
): GateSummaryModel[] {
  const traceByType = new Map(
    (trace?.gate_results ?? []).map((gate) => [gate.gate_type, gate]),
  );

  const base: GateSummaryModel[] = [
    gate("requirements_gate", "Requirements", "pending", traceByType),
    gate("code_gate", "Code", "pending", traceByType),
    gate(
      "test_gate",
      "Test",
      gates?.test_required ? passFail(gates.latest_test_passed) : "not_required",
      traceByType,
    ),
    gate(
      "formal_gate",
      "Formal",
      gates?.formal_required ? passFail(gates.latest_formal_passed) : "not_required",
      traceByType,
    ),
    gate(
      "regression_gate",
      "Regression",
      gates?.regression_required || gates?.formal_regression_required
        ? "pending"
        : "passed",
      traceByType,
    ),
    gate(
      "final_gate",
      "Final",
      gates?.can_finish_as_success
        ? "passed"
        : gates?.has_blocking_failure
          ? "failed"
          : "pending",
      traceByType,
    ),
  ];

  return base.map((item) => {
    const traced = traceByType.get(item.key);
    if (!traced) {
      return item;
    }
    return {
      ...item,
      status: traced.status === "passed" ? "passed" : "failed",
      blocking: traced.blocking,
      evidenceArtifactIds: traced.evidence_artifact_ids,
    };
  });
}

export function getRepairSummary(task: TaskState | null): RepairSummaryModel {
  return {
    rounds: task?.runtime_limits.repair_rounds ?? 0,
    maxRounds: task?.runtime_limits.max_repair_rounds ?? 3,
    regressionRequired: task?.gates.regression_required ?? false,
    formalRegressionRequired: task?.gates.formal_regression_required ?? false,
    hasBlockingFailure: task?.gates.has_blocking_failure ?? false,
  };
}

export function findFinalReportArtifact(
  task: TaskState | null,
  artifacts: Artifact[],
  events: RouterEvent[],
): ArtifactRef | Artifact | null {
  if (task?.current_artifacts.final_report) {
    return task.current_artifacts.final_report;
  }
  const artifact = artifacts.find((item) => item.type === "final_report");
  if (artifact) {
    return artifact;
  }
  const completed = [...events]
    .reverse()
    .find((event) => event.type === "main_agent.completed");
  const artifactId = completed?.payload.final_report_artifact_id;
  if (typeof artifactId !== "string") {
    return null;
  }
  return (
    artifacts.find((item) => item.artifact_id === artifactId) ?? {
      artifact_id: artifactId,
      type: "final_report",
      version: 1,
      summary: "Main Agent final report.",
      uri: null,
      content_hash: null,
    }
  );
}

export function shouldRefreshTask(event: RouterEvent): boolean {
  return (
    event.type.startsWith("task.") ||
    event.type.startsWith("worker.") ||
    event.type.startsWith("gate.") ||
    event.type.startsWith("repair.") ||
    event.type === "main_agent.completed" ||
    event.type === "main_agent.clarification_requested"
  );
}

export function shouldRefreshArtifacts(event: RouterEvent): boolean {
  return (
    event.type.startsWith("artifact.") ||
    event.type === "worker.completed" ||
    event.type === "main_agent.completed" ||
    event.type.startsWith("task.")
  );
}

export function shouldRefreshTrace(event: RouterEvent): boolean {
  return (
    event.type.startsWith("main_agent.") ||
    event.type.startsWith("worker.") ||
    event.type.startsWith("gate.") ||
    event.type.startsWith("task.")
  );
}

function eventStatus(event: RouterEvent | undefined): string {
  if (!event) {
    return "idle";
  }
  if (event.type.endsWith("started")) {
    return "running";
  }
  if (event.type.endsWith("completed")) {
    return "completed";
  }
  if (event.type.endsWith("error") || event.type.endsWith("timeout")) {
    return "error";
  }
  return event.type;
}

function passFail(value: boolean | null | undefined): GateSummaryModel["status"] {
  if (value === true) {
    return "passed";
  }
  if (value === false) {
    return "failed";
  }
  return "pending";
}

function gate(
  key: string,
  label: string,
  status: GateSummaryModel["status"],
  traceByType: Map<string, { blocking: boolean; evidence_artifact_ids: string[] }>,
): GateSummaryModel {
  const traced = traceByType.get(key);
  return {
    key,
    label,
    status,
    blocking: traced?.blocking,
    evidenceArtifactIds: traced?.evidence_artifact_ids ?? [],
  };
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}
