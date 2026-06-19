import { AlertTriangle, Box, GitCommitVertical, Info } from "lucide-react";

import type { RouterEvent } from "../../api/router/types";

interface ExecutionTimelineProps {
  events: RouterEvent[];
  onArtifactClick: (artifactId: string) => void;
}

export function ExecutionTimeline({
  events,
  onArtifactClick,
}: ExecutionTimelineProps) {
  if (!events.length) {
    return (
      <section className="empty-state timeline-empty">
        <div>
          <h3 className="empty-title">Timeline</h3>
          <p className="small muted">No events yet.</p>
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
                {(event.correlation.artifact_ids ?? []).map((artifactId) => (
                  <button
                    className="mini-pill"
                    key={artifactId}
                    type="button"
                    onClick={() => onArtifactClick(artifactId)}
                  >
                    <Box size={13} />
                    {artifactId}
                  </button>
                ))}
                {(event.correlation.failure_ids ?? []).map((failureId) => (
                  <span className="mini-pill" key={failureId}>
                    {failureId}
                  </span>
                ))}
                <span className="mini-pill">{formatTime(event.created_at)}</span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
