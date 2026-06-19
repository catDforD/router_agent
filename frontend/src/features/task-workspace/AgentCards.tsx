import { CheckCircle2, CircleDashed, RotateCcw, ShieldAlert } from "lucide-react";

import type {
  GateSummaryModel,
  RepairSummaryModel,
  WorkerCardModel,
} from "./selectors";

interface AgentCardsProps {
  workers: WorkerCardModel[];
  gates: GateSummaryModel[];
  repair: RepairSummaryModel;
}

export function AgentCards({ workers, gates, repair }: AgentCardsProps) {
  return (
    <section className="agent-summary">
      <div className="dock-section-title">
        <span>Subagents</span>
        <span>{workers.filter((worker) => worker.status !== "idle").length}/{workers.length}</span>
      </div>
      <div className="worker-grid">
        {workers.map((worker) => (
          <article className="worker-card" key={worker.workerType}>
            <div className="card-heading">
              <h3>{worker.workerType}</h3>
              <span data-tone={statusTone(worker.status)} className="status-pill">
                {worker.status}
              </span>
            </div>
            <p className="small muted worker-objective">
              {worker.objective ?? worker.summary ?? "idle"}
            </p>
            <div className="inline-list">
              {worker.artifactIds.slice(0, 3).map((artifactId) => (
                <span className="mini-pill" key={artifactId}>
                  {artifactId}
                </span>
              ))}
              {worker.failureIds.length ? (
                <span data-tone="bad" className="status-pill">
                  {worker.failureIds.length} failures
                </span>
              ) : null}
            </div>
          </article>
        ))}
      </div>

      <div className="dock-section-title">
        <span>Quality Gates</span>
        <span>{gates.filter((gate) => gate.status === "passed").length}/{gates.length}</span>
      </div>
      <div className="gate-list" aria-label="Quality gates">
        {gates.map((gate) => (
          <article className="gate-row" key={gate.key}>
            <div className="gate-main">
              <span className="gate-pill" data-status={gate.status}>
                {gateIcon(gate.status)}
                {gate.status}
              </span>
              <strong>{gate.label}</strong>
            </div>
            {gate.evidenceArtifactIds.length ? (
              <p className="small muted gate-evidence">
                evidence {gate.evidenceArtifactIds.slice(0, 2).join(", ")}
              </p>
            ) : (
              <p className="small muted gate-evidence">no evidence</p>
            )}
          </article>
        ))}
      </div>

      <div className="repair-strip">
        <RotateCcw size={15} />
        <span>
          Repair rounds {repair.rounds}/{repair.maxRounds}
        </span>
        <span data-tone={repair.regressionRequired ? "warn" : "ok"} className="status-pill">
          regression {repair.regressionRequired ? "pending" : "clear"}
        </span>
        <span
          data-tone={repair.formalRegressionRequired ? "warn" : "ok"}
          className="status-pill"
        >
          formal {repair.formalRegressionRequired ? "pending" : "clear"}
        </span>
        <span data-tone={repair.hasBlockingFailure ? "bad" : "ok"} className="status-pill">
          blockers {repair.hasBlockingFailure ? "open" : "clear"}
        </span>
      </div>
    </section>
  );
}

function gateIcon(status: string) {
  if (status === "passed") {
    return <CheckCircle2 size={14} />;
  }
  if (status === "failed") {
    return <ShieldAlert size={14} />;
  }
  return <CircleDashed size={14} />;
}

function statusTone(status: string): "ok" | "warn" | "bad" {
  if (status === "completed" || status === "passed") {
    return "ok";
  }
  if (status === "error" || status === "timeout" || status === "failed") {
    return "bad";
  }
  if (status === "running" || status === "queued") {
    return "warn";
  }
  return "warn";
}
