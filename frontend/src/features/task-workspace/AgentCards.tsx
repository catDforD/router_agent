import {
  Beaker,
  Bot,
  Code2,
  FileCheck2,
  Hammer,
  Loader2,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";

import type { SubagentStatusResponse, SubagentStatusWorker } from "../../api/router/types";
import type { WorkerCardModel } from "./selectors";

interface AgentCardsProps {
  workers: WorkerCardModel[];
  subagentStatus?: SubagentStatusResponse | null;
  subagentStatusError?: string;
  subagentStatusLoading?: boolean;
}

const WORKER_META: Record<
  WorkerCardModel["workerType"],
  {
    label: string;
    role: string;
    accent: "blue" | "green" | "amber" | "rose";
    icon: typeof Code2;
  }
> = {
  "plc-dev": {
    label: "PLC Dev",
    role: "生成与修改 PLC 控制逻辑",
    accent: "blue",
    icon: Code2,
  },
  "plc-test": {
    label: "PLC Test",
    role: "生成用例并执行功能验证",
    accent: "green",
    icon: Beaker,
  },
  "plc-formal": {
    label: "PLC Formal",
    role: "形式化性质与反例检查",
    accent: "amber",
    icon: ShieldCheck,
  },
  "plc-repair": {
    label: "PLC Repair",
    role: "根据失败证据修复代码",
    accent: "rose",
    icon: Hammer,
  },
};

export function AgentCards({
  workers,
  subagentStatus,
  subagentStatusError,
  subagentStatusLoading = false,
}: AgentCardsProps) {
  const statusByWorker = new Map(
    subagentStatus?.workers.map((worker) => [worker.worker_type, worker]) ?? [],
  );
  const onlineCount =
    subagentStatus?.workers.filter((worker) => worker.online === true).length ?? 0;

  return (
    <section className="agent-summary">
      <div className="dock-section-title">
        <span>Subagents</span>
        <span>
          {subagentStatusLoading ? "checking" : `${onlineCount}/4 online`}
        </span>
      </div>
      {subagentStatusError ? (
        <p className="subagent-status-error">{subagentStatusError}</p>
      ) : null}
      <div className="worker-grid">
        {workers.map((worker) => (
          <WorkerCard
            worker={worker}
            status={statusByWorker.get(worker.workerType)}
            key={worker.workerType}
          />
        ))}
      </div>
    </section>
  );
}

function WorkerCard({
  worker,
  status,
}: {
  worker: WorkerCardModel;
  status?: SubagentStatusWorker;
}) {
  const meta = WORKER_META[worker.workerType];
  const Icon = meta?.icon ?? Bot;
  const workerStatus = normalizeStatus(worker.status);
  const remote = remoteStatus(status);
  const RemoteIcon = remote.online === true ? Wifi : remote.online === false ? WifiOff : Bot;

  return (
    <article className="worker-card" data-accent={meta?.accent ?? "blue"}>
      <div className="worker-card-top">
        <span
          className="worker-logo"
          data-running={workerStatus === "running"}
          aria-hidden="true"
        >
          {workerStatus === "running" ? <Loader2 size={18} /> : <Icon size={18} />}
        </span>
        <div>
          <h3>{meta?.label ?? worker.workerType}</h3>
          <p>{meta?.role ?? "Router worker"}</p>
        </div>
        <span data-tone={statusTone(workerStatus)} className="status-pill">
          {workerStatus}
        </span>
      </div>
      <div className="subagent-online-row">
        <span data-tone={remote.tone} className="status-pill">
          <RemoteIcon size={13} />
          {remote.label}
        </span>
        <span className="mini-pill">{status?.agent_id ?? "not configured"}</span>
      </div>
      <p className="worker-objective">
        {worker.objective ?? worker.summary ?? "等待 Main Agent 调度。"}
      </p>
      <div className="worker-foot">
        <span className="mini-pill">
          <FileCheck2 size={13} />
          {worker.artifactIds.length} artifacts
        </span>
        {worker.failureIds.length ? (
          <span data-tone="bad" className="status-pill">
            {worker.failureIds.length} failures
          </span>
        ) : (
          <span className="mini-pill">no failures</span>
        )}
      </div>
    </article>
  );
}

function remoteStatus(status: SubagentStatusWorker | undefined): {
  label: string;
  tone: "ok" | "warn" | "bad";
  online: boolean | null;
} {
  if (!status) {
    return { label: "unknown", tone: "warn", online: null };
  }
  if (status.route !== "subagent") {
    return { label: status.route, tone: "warn", online: null };
  }
  if (status.online === true) {
    const latency = Number.isFinite(status.latency_ms)
      ? ` ${status.latency_ms}ms`
      : "";
    return { label: `online${latency}`, tone: "ok", online: true };
  }
  if (status.status === "timeout") {
    return { label: "timeout", tone: "bad", online: false };
  }
  if (status.online === false) {
    return { label: "offline", tone: "bad", online: false };
  }
  return { label: "checking", tone: "warn", online: null };
}

function normalizeStatus(status: string): string {
  if (!status || status === "main_agent.started") {
    return "idle";
  }
  if (status === "completed" || status === "passed") {
    return "done";
  }
  if (status === "error" || status === "timeout" || status === "failed") {
    return "failed";
  }
  return status;
}

function statusTone(status: string): "ok" | "warn" | "bad" {
  if (status === "done") {
    return "ok";
  }
  if (status === "failed") {
    return "bad";
  }
  return "warn";
}
