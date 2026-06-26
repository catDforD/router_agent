import type { ReactNode } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  Flag,
  GitBranch,
  HelpCircle,
  ListChecks,
  PackageCheck,
  Route,
  ShieldCheck,
} from "lucide-react";

import type { ArtifactContentResponse } from "../../api/router/types";

interface FinalReportViewProps {
  finalReport?: ArtifactContentResponse;
  loading: boolean;
  onArtifactSelect?: (artifactId: string) => void;
}

type JsonRecord = Record<string, unknown>;

const DELIVERY_KEYS = [
  ["final_plc_code", "PLC Code"],
  ["io_contract", "I/O Contract"],
  ["test_report", "Test Report"],
  ["formal_report", "Formal Report"],
  ["gate_report", "Quality Gate"],
  ["patch", "Patch"],
  ["repair_summary", "Repair Summary"],
  ["requirements_ir", "Requirements"],
] as const;

export function FinalReportView({
  finalReport,
  loading,
  onArtifactSelect,
}: FinalReportViewProps) {
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
  const data = report.data;
  const status = stringValue(data.final_task_status) ?? "available";
  const userGoal = recordValue(data.user_goal);
  const classification = recordValue(data.classification);
  const difficulty = recordValue(classification?.difficulty);
  const validation = recordValue(data.validation_summary);
  const repair = recordValue(data.repair_summary);
  const unresolved = recordValue(data.unresolved_items);
  const traceRefs = recordValue(data.trace_refs);
  const delivery = deliveryRows(recordValue(data.delivery_artifacts));
  const gateResults = recordsArray(validation?.gate_results);
  const assumptions = arrayValue(data.assumptions);
  const plan = arrayValue(data.plan);
  const decisions = arrayValue(data.decisions);

  return (
    <section className="final-report stack">
      <div className="panel-header">
        <h2 className="panel-title">Final Report</h2>
        <span className="status-pill" data-tone={statusTone(status)}>
          <ClipboardCheck size={14} />
          {status}
        </span>
      </div>

      <article className="report-hero">
        <p>{stringValue(data.summary) ?? "Final report is available."}</p>
        <div className="report-stat-grid">
          <ReportStat
            label="Task"
            value={stringValue(classification?.task_type) ?? "unknown"}
            icon={<Flag size={14} />}
          />
          <ReportStat
            label="Difficulty"
            value={stringValue(difficulty?.level) ?? "unknown"}
            icon={<Route size={14} />}
          />
          <ReportStat
            label="Delivery"
            value={`${delivery.length} refs`}
            icon={<PackageCheck size={14} />}
          />
          <ReportStat
            label="Blockers"
            value={String(numberValue(unresolved?.blocking_failure_count) ?? 0)}
            tone={
              numberValue(unresolved?.blocking_failure_count) ? "bad" : "ok"
            }
            icon={<ShieldCheck size={14} />}
          />
        </div>
      </article>

      <ReportBlock title="User Goal" icon={<Flag size={15} />}>
        <p className="report-text">
          {stringValue(userGoal?.normalized_goal) ??
            stringValue(userGoal?.raw_user_request) ??
            "No user goal captured."}
        </p>
        <div className="inline-list">
          {stringValue(userGoal?.title) ? (
            <span className="mini-pill">{stringValue(userGoal?.title)}</span>
          ) : null}
          {projectPills(recordValue(userGoal?.project_context)).map((pill) => (
            <span className="mini-pill" key={pill}>
              {pill}
            </span>
          ))}
        </div>
      </ReportBlock>

      <ReportBlock title="Delivery Artifacts" icon={<PackageCheck size={15} />}>
        {delivery.length ? (
          <div className="delivery-list">
            {delivery.map((artifact) => (
              <button
                className="delivery-row"
                key={`${artifact.key}-${artifact.artifactId}`}
                type="button"
                disabled={!artifact.artifactId || !onArtifactSelect}
                onClick={() => {
                  if (artifact.artifactId) {
                    onArtifactSelect?.(artifact.artifactId);
                  }
                }}
              >
                <FileText size={15} />
                <span>
                  <strong>{artifact.label}</strong>
                  <small>{artifact.summary ?? artifact.type ?? "Referenced artifact"}</small>
                </span>
                <span className="mini-pill">{artifact.type ?? "artifact"}</span>
                {artifact.artifactId ? (
                  <span className="mini-pill">{shortId(artifact.artifactId)}</span>
                ) : null}
              </button>
            ))}
          </div>
        ) : (
          <p className="small muted">No delivery artifacts referenced yet.</p>
        )}
      </ReportBlock>

      <ReportBlock title="Validation" icon={<ListChecks size={15} />}>
        <div className="check-list">
          <CheckRow
            label="Test gate"
            value={gateLabel(
              booleanValue(validation?.test_required),
              booleanValue(validation?.latest_test_passed),
            )}
            tone={gateTone(
              booleanValue(validation?.test_required),
              booleanValue(validation?.latest_test_passed),
            )}
          />
          <CheckRow
            label="Formal gate"
            value={gateLabel(
              booleanValue(validation?.formal_required),
              booleanValue(validation?.latest_formal_passed),
            )}
            tone={gateTone(
              booleanValue(validation?.formal_required),
              booleanValue(validation?.latest_formal_passed),
            )}
          />
          <CheckRow
            label="Regression"
            value={
              booleanValue(validation?.regression_required) ||
              booleanValue(validation?.formal_regression_required)
                ? "required"
                : "clear"
            }
            tone={
              booleanValue(validation?.regression_required) ||
              booleanValue(validation?.formal_regression_required)
                ? "warn"
                : "ok"
            }
          />
          <CheckRow
            label="Final gate"
            value={booleanValue(validation?.can_finish_as_success) ? "pass" : "blocked"}
            tone={booleanValue(validation?.can_finish_as_success) ? "ok" : "bad"}
          />
        </div>
        {gateResults.length ? (
          <div className="gate-result-list">
            {gateResults.slice(0, 6).map((gate) => (
              <div className="gate-result-row" key={stringValue(gate.gate_result_id)}>
                <span
                  className="status-pill"
                  data-tone={statusTone(stringValue(gate.status) ?? "pending")}
                >
                  {stringValue(gate.status) ?? "pending"}
                </span>
                <strong>{stringValue(gate.gate_type) ?? "gate"}</strong>
                <small>
                  {booleanValue(gate.blocking) ? "blocking" : "non-blocking"}
                </small>
              </div>
            ))}
          </div>
        ) : null}
      </ReportBlock>

      {plan.length || decisions.length ? (
        <ReportBlock title="Plan & Decisions" icon={<GitBranch size={15} />}>
          {plan.length ? <CompactList items={plan} /> : null}
          {decisions.length ? <CompactList items={decisions} /> : null}
        </ReportBlock>
      ) : null}

      <ReportBlock title="Repair & Open Items" icon={<AlertCircle size={15} />}>
        <div className="report-stat-grid">
          <ReportStat
            label="Repair"
            value={`${numberValue(repair?.repair_rounds) ?? 0}/${
              numberValue(repair?.max_repair_rounds) ?? 0
            }`}
            icon={<GitBranch size={14} />}
          />
          <ReportStat
            label="Open Failures"
            value={String(numberValue(unresolved?.open_failure_count) ?? 0)}
            tone={numberValue(unresolved?.open_failure_count) ? "bad" : "ok"}
            icon={<AlertCircle size={14} />}
          />
          <ReportStat
            label="Questions"
            value={String(numberValue(unresolved?.open_question_count) ?? 0)}
            tone={numberValue(unresolved?.open_question_count) ? "warn" : "ok"}
            icon={<HelpCircle size={14} />}
          />
        </div>
        <OpenItems unresolved={unresolved} />
      </ReportBlock>

      {assumptions.length ? (
        <ReportBlock title="Assumptions" icon={<HelpCircle size={15} />}>
          <CompactList items={assumptions} />
        </ReportBlock>
      ) : null}

      {traceRefs ? (
        <ReportBlock title="Trace Refs" icon={<Route size={15} />}>
          <div className="inline-list">
            {stringValue(traceRefs.openai_trace_id) ? (
              <span className="mini-pill">
                trace {shortId(stringValue(traceRefs.openai_trace_id)!)}
              </span>
            ) : null}
            {arrayValue(traceRefs.main_agent_run_ids)
              .filter((item): item is string => typeof item === "string")
              .slice(0, 3)
              .map((runId) => (
                <span className="mini-pill" key={runId}>
                  run {shortId(runId)}
                </span>
              ))}
          </div>
        </ReportBlock>
      ) : null}

      <details className="payload-details report-raw-details">
        <summary>Raw report payload</summary>
        <pre>
          {report.isJson ? JSON.stringify(data, null, 2) : finalReport.content}
        </pre>
      </details>
    </section>
  );
}

function ReportBlock({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <article className="report-section">
      <div className="report-section-title">
        {icon}
        <h3>{title}</h3>
      </div>
      {children}
    </article>
  );
}

function ReportStat({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: string;
  icon: ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="report-stat" data-tone={tone}>
      {icon}
      <span>
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

function CheckRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "bad";
}) {
  return (
    <div className="check-row">
      {tone === "ok" ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
      <span>{label}</span>
      <strong data-tone={tone}>{value}</strong>
    </div>
  );
}

function CompactList({ items }: { items: unknown[] }) {
  return (
    <ul className="report-list">
      {items.slice(0, 8).map((item, index) => (
        <li key={index}>{compactText(item)}</li>
      ))}
    </ul>
  );
}

function OpenItems({ unresolved }: { unresolved: JsonRecord | null }) {
  const failures = recordsArray(unresolved?.open_failures);
  const questions = recordsArray(unresolved?.open_questions);
  if (!failures.length && !questions.length) {
    return <p className="small muted">No open failures or clarification questions.</p>;
  }
  return (
    <div className="open-item-list">
      {failures.slice(0, 4).map((failure) => (
        <div className="open-item" key={stringValue(failure.failure_id)}>
          <strong>{stringValue(failure.title) ?? "Open failure"}</strong>
          <small>{stringValue(failure.description) ?? stringValue(failure.source)}</small>
        </div>
      ))}
      {questions.slice(0, 4).map((question) => (
        <div className="open-item" key={stringValue(question.question_id)}>
          <strong>{stringValue(question.question) ?? "Clarification needed"}</strong>
          <small>{stringValue(question.reason) ?? "waiting for user input"}</small>
        </div>
      ))}
    </div>
  );
}

function parseReport(content: string): {
  data: JsonRecord;
  isJson: boolean;
} {
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? { data: parsed as JsonRecord, isJson: true }
      : { data: { summary: content }, isJson: false };
  } catch {
    return { data: { summary: content }, isJson: false };
  }
}

function deliveryRows(delivery: JsonRecord | null) {
  if (!delivery) {
    return [];
  }
  return DELIVERY_KEYS.flatMap(([key, label]) => {
    const value = recordValue(delivery[key]);
    if (!value) {
      return [];
    }
    return [
      {
        key,
        label,
        artifactId: stringValue(value.artifact_id),
        type: stringValue(value.type),
        summary: stringValue(value.summary),
      },
    ];
  });
}

function gateLabel(required: boolean | null, passed: boolean | null): string {
  if (!required) {
    return "not required";
  }
  if (passed === true) {
    return "pass";
  }
  if (passed === false) {
    return "fail";
  }
  return "pending";
}

function gateTone(
  required: boolean | null,
  passed: boolean | null,
): "ok" | "warn" | "bad" {
  if (!required || passed === true) {
    return "ok";
  }
  if (passed === false) {
    return "bad";
  }
  return "warn";
}

function statusTone(status: string): "ok" | "warn" | "bad" {
  if (["succeeded", "passed", "pass", "available", "completed"].includes(status)) {
    return "ok";
  }
  if (["failed", "error", "blocked", "blocking"].includes(status)) {
    return "bad";
  }
  return "warn";
}

function projectPills(context: JsonRecord | null): string[] {
  if (!context) {
    return [];
  }
  return [
    stringValue(context.target_plc_language),
    stringValue(context.target_platform),
    stringValue(context.project_name),
  ].filter((value): value is string => Boolean(value));
}

function compactText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  const record = recordValue(value);
  if (!record) {
    return String(value ?? "");
  }
  return (
    stringValue(record.text) ??
    stringValue(record.summary) ??
    stringValue(record.title) ??
    stringValue(record.decision) ??
    stringValue(record.description) ??
    JSON.stringify(record)
  );
}

function recordValue(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function recordsArray(value: unknown): JsonRecord[] {
  return arrayValue(value).flatMap((item) => {
    const record = recordValue(item);
    return record ? [record] : [];
  });
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function shortId(value: string): string {
  if (value.length <= 18) {
    return value;
  }
  return `${value.slice(0, 10)}...${value.slice(-5)}`;
}
