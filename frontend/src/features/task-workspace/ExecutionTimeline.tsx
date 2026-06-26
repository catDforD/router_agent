import { AlertTriangle, FileText, GitCommitVertical, Info } from "lucide-react";

import type { RouterEvent } from "../../api/router/types";

interface ExecutionTimelineProps {
  events: RouterEvent[];
}

export function ExecutionTimeline({ events }: ExecutionTimelineProps) {
  if (!events.length) {
    return (
      <section className="empty-state timeline-empty">
        <div>
          <h3 className="empty-title">Timeline</h3>
          <p className="small muted">No events yet</p>
        </div>
      </section>
    );
  }

  return (
    <section className="stack">
      <div className="panel-header">
        <h2 className="panel-title">Timeline</h2>
        <span className="mini-pill">{events.length} events</span>
      </div>
      <div className="timeline">
        {events.map((event) => (
          <article className="timeline-row" key={event.event_id}>
            <div className="timeline-seq">
              <GitCommitVertical size={16} />
              <div>#{event.seq}</div>
            </div>
            <div>
              <div className="status-row">
                <span className="event-type">{event.type}</span>
                <span data-tone={severityTone(event.severity)} className="status-pill">
                  {event.severity === "error" ? (
                    <AlertTriangle size={13} />
                  ) : (
                    <Info size={13} />
                  )}
                  {event.severity}
                </span>
                <span className="mini-pill">{event.source.type}</span>
              </div>
              <h3 className="timeline-title">{event.title}</h3>
              {event.message ? <p className="small">{event.message}</p> : null}
              <div className="inline-list">
                {eventPaths(event).map((path) => (
                  <span className="mini-pill" key={path}>
                    <FileText size={13} />
                    {path}
                  </span>
                ))}
                {(event.correlation.failure_ids ?? []).map((failureId) => {
                  const tone = failureTone(event);
                  return (
                    <span
                      className={tone ? "status-pill" : "mini-pill"}
                      data-tone={tone}
                      key={failureId}
                    >
                      related {failureId}
                    </span>
                  );
                })}
                <span className="mini-pill">{formatTime(event.created_at)}</span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function eventPaths(event: RouterEvent): string[] {
  return [
    ...payloadStringArray(event.payload.input_paths),
    ...payloadStringArray(event.payload.read_paths),
    ...payloadStringArray(event.payload.written_paths),
    ...payloadStringArray(event.payload.report_paths),
    ...payloadStringArray(event.payload.evidence_paths),
    payloadString(event.payload.final_report_path),
    payloadString(event.payload.main_agent_log_path),
    payloadString(event.payload.gate_report_path),
  ].filter((value): value is string => Boolean(value));
}

function payloadString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function payloadStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function severityTone(severity: string): "ok" | "warn" | "bad" {
  if (severity === "error") {
    return "bad";
  }
  if (severity === "warning") {
    return "warn";
  }
  return "ok";
}

function failureTone(event: RouterEvent): "bad" | undefined {
  if (event.severity === "error") {
    return "bad";
  }
  if (payloadString(event.payload.status) === "failed") {
    return "bad";
  }
  const details = event.payload.details;
  if (
    details &&
    typeof details === "object" &&
    "blocking" in details &&
    (details as { blocking?: unknown }).blocking === true
  ) {
    return "bad";
  }
  return undefined;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
