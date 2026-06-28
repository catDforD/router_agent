import { Network, RefreshCw } from "lucide-react";

import type { TaskTraceSummary } from "../../api/router/types";

interface TraceViewProps {
  trace: TaskTraceSummary | null;
  loading: boolean;
  error?: string;
  onRefresh: () => void;
}

export function TraceView({
  trace,
  loading,
  error,
  onRefresh,
}: TraceViewProps) {
  return (
    <section className="stack">
      <div className="panel-header">
        <h2 className="panel-title">Trace</h2>
        <button className="button secondary" type="button" onClick={onRefresh}>
          <RefreshCw size={14} />
          Reload
        </button>
      </div>
      {loading ? <div className="notice">Loading trace.</div> : null}
      {error ? <div className="notice error-box">{error}</div> : null}
      {!trace ? (
        <div className="empty-state">
          <div>
            <h3 className="empty-title">No trace yet</h3>
            <p className="small muted">Runtime events and worker calls will appear here.</p>
          </div>
        </div>
      ) : null}
      {trace ? (
        <div className="trace-grid">
          <div className="notice">
            <Network size={14} /> terminal {trace.terminal_event_type ?? "open"}; runs{" "}
            {trace.main_agent_run_ids.length}; events {trace.events.length}
          </div>
          <TraceSection title="Main Agent Runs">
            {trace.main_agent_runs.map((run) => (
              <div className="trace-row" key={run.main_agent_run_id}>
                <strong>{run.main_agent_run_id}</strong>
                <span className="small muted">
                  final report {run.final_report_path ?? "none"}
                </span>
              </div>
            ))}
          </TraceSection>
          <TraceSection title="Worker Jobs">
            {trace.worker_jobs.map((job) => (
              <div className="trace-row" key={job.worker_job_id}>
                <strong>
                  {job.worker_type} · {job.status}
                </strong>
                <span className="small muted">{job.worker_job_id}</span>
                <div className="inline-list">
                  {[
                    ...job.input_paths,
                    ...job.read_paths,
                    ...job.written_paths,
                    ...job.report_paths,
                  ].map((path) => (
                    <span className="mini-pill" key={path}>
                      {path}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </TraceSection>
          <TraceSection title="Gate Results">
            {trace.gate_results.map((gate) => (
              <div className="trace-row" key={gate.gate_result_id}>
                <strong>
                  {gate.gate_type} · {gate.status}
                </strong>
                <span className="small muted">
                  {gate.blocking ? "blocking" : "non-blocking"}
                </span>
              </div>
            ))}
          </TraceSection>
          <TraceSection title="Events">
            {trace.events.slice(-20).map((event) => (
              <div className="trace-row" key={event.event_id}>
                <strong>
                  #{event.seq} {event.type}
                </strong>
                <span className="small muted">{event.title}</span>
              </div>
            ))}
          </TraceSection>
        </div>
      ) : null}
    </section>
  );
}

function TraceSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="report-section">
      <h3>{title}</h3>
      <div className="trace-grid">{children}</div>
    </section>
  );
}
