import { ClipboardCheck } from "lucide-react";

import type { ArtifactContentResponse } from "../../api/router/types";

interface FinalReportViewProps {
  finalReport?: ArtifactContentResponse;
  loading: boolean;
}

export function FinalReportView({ finalReport, loading }: FinalReportViewProps) {
  if (loading) {
    return <div className="empty-state">Loading final report.</div>;
  }
  if (!finalReport) {
    return (
      <section className="empty-state">
        <div>
          <h3 className="empty-title">Final Report</h3>
          <p className="small muted">Waiting for a completed task.</p>
        </div>
      </section>
    );
  }

  const report = parseReport(finalReport.content);
  return (
    <section className="stack">
      <div className="panel-header">
        <h2 className="panel-title">Final Report</h2>
        <span className="status-pill">
          <ClipboardCheck size={14} />
          {stringValue(report.final_task_status) ?? "available"}
        </span>
      </div>
      <ReportSection title="Summary" value={report.summary} />
      <ReportSection title="Goal" value={report.user_goal} />
      <ReportSection title="Classification" value={report.classification} />
      <ReportSection title="Plan" value={report.plan} />
      <ReportSection title="Decisions" value={report.decisions} />
      <ReportSection title="Delivery" value={report.delivery_artifacts} />
      <ReportSection title="Validation" value={report.validation_summary} />
      <ReportSection title="Repairs" value={report.repair_summary} />
      <ReportSection title="Assumptions" value={report.assumptions} />
      <ReportSection title="Unresolved" value={report.unresolved_items} />
      <ReportSection title="Trace" value={report.trace_refs} />
    </section>
  );
}

function ReportSection({ title, value }: { title: string; value: unknown }) {
  if (value === undefined || value === null) {
    return null;
  }
  return (
    <article className="report-section">
      <h3>{title}</h3>
      <pre className="small">{formatValue(value)}</pre>
    </article>
  );
}

function parseReport(content: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : { summary: content };
  } catch {
    return { summary: content };
  }
}

function formatValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}
