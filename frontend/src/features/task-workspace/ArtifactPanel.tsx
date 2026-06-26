import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clipboard,
  ClipboardCheck,
  FileCode2,
  FileJson2,
  FileText,
  Loader2,
  Package,
  ShieldCheck,
} from "lucide-react";

import type { Artifact } from "../../api/router/types";
import type { ArtifactContentState } from "./hooks/useTaskArtifacts";

interface ArtifactPanelProps {
  artifacts: Artifact[];
  loading: boolean;
  error?: string;
  selectedArtifactId: string | null;
  selectedContent?: ArtifactContentState;
  onSelect: (artifactId: string) => void;
}

type JsonRecord = Record<string, unknown>;

const DELIVERY_ORDER = [
  "final_report",
  "plc_code",
  "io_contract",
  "test_report",
  "formal_report",
  "gate_report",
  "patch",
  "repair_summary",
  "requirements_ir",
  "test_cases",
];

export function ArtifactPanel({
  artifacts,
  loading,
  error,
  selectedArtifactId,
  selectedContent,
  onSelect,
}: ArtifactPanelProps) {
  const orderedArtifacts = useMemo(() => sortArtifacts(artifacts), [artifacts]);
  const userArtifacts = orderedArtifacts.filter(
    (artifact) => artifact.visibility !== "internal",
  );
  const internalArtifacts = orderedArtifacts.filter(
    (artifact) => artifact.visibility === "internal",
  );

  return (
    <section className="artifact-panel stack">
      {loading ? (
        <div className="notice">
          <Loader2 size={14} /> Loading artifacts
        </div>
      ) : null}
      {error ? <div className="notice error-box">{error}</div> : null}

      {userArtifacts.length ? (
        <ArtifactList
          artifacts={userArtifacts}
          selectedArtifactId={selectedArtifactId}
          onSelect={onSelect}
        />
      ) : null}

      {internalArtifacts.length ? (
        <details className="internal-artifacts">
          <summary>Internal artifacts · {internalArtifacts.length}</summary>
          <ArtifactList
            artifacts={internalArtifacts}
            selectedArtifactId={selectedArtifactId}
            onSelect={onSelect}
          />
        </details>
      ) : null}

      {selectedContent ? (
        <ArtifactPreview state={selectedContent} />
      ) : artifacts.length ? (
        <div className="empty-state">
          <div>
            <h3 className="empty-title">Select an artifact</h3>
            <p className="small muted">Preview code, reports, and generated files here.</p>
          </div>
        </div>
      ) : (
        <div className="empty-state">
          <div>
            <h3 className="empty-title">No artifacts yet</h3>
            <p className="small muted">Generated files will appear after a task runs.</p>
          </div>
        </div>
      )}
    </section>
  );
}

function ArtifactList({
  artifacts,
  selectedArtifactId,
  onSelect,
}: {
  artifacts: Artifact[];
  selectedArtifactId: string | null;
  onSelect: (artifactId: string) => void;
}) {
  return (
    <div className="artifact-list">
      {artifacts.map((artifact) => (
        <button
          aria-selected={artifact.artifact_id === selectedArtifactId}
          className="artifact-row"
          key={artifact.artifact_id}
          type="button"
          onClick={() => onSelect(artifact.artifact_id)}
        >
          <div className="card-heading">
            <strong>{artifact.display_name ?? artifact.name}</strong>
            <span className="artifact-type">{artifact.type}</span>
          </div>
          <p className="small muted">{artifact.summary}</p>
          <div className="inline-list">
            <span className="mini-pill">v{artifact.version}</span>
            <span
              className="status-pill"
              data-tone={artifact.status === "available" ? "ok" : "warn"}
            >
              {artifact.status}
            </span>
            <span className="mini-pill">{artifact.visibility}</span>
            {artifact.created_by.worker_type ? (
              <span className="mini-pill">{artifact.created_by.worker_type}</span>
            ) : null}
            <span className="mini-pill">
              {artifact.storage.mime_type ?? "unknown"}
            </span>
            {artifact.storage.size_bytes ? (
              <span className="mini-pill">{formatBytes(artifact.storage.size_bytes)}</span>
            ) : null}
          </div>
        </button>
      ))}
    </div>
  );
}

function ArtifactPreview({ state }: { state: ArtifactContentState }) {
  const [copied, setCopied] = useState(false);

  if (state.loading) {
    return <div className="notice">Loading artifact content.</div>;
  }
  if (state.error) {
    return <div className="notice error-box">{state.error}</div>;
  }
  if (!state.response) {
    return null;
  }

  const response = state.response;
  const artifact = response.artifact;
  const parsed = recordValue(state.parsed);
  const textContent =
    typeof state.parsed === "string"
      ? state.parsed
      : response.mime_type === "application/json"
        ? JSON.stringify(state.parsed, null, 2)
        : response.content;

  const copyContent = async () => {
    await navigator.clipboard.writeText(textContent);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <section className="artifact-preview stack">
      <div className="artifact-preview-header">
        <div>
          <div className="status-row">
            <span className="status-pill">
              {artifactIcon(artifact.type)}
              {artifact.type}
            </span>
            <span className="mini-pill">
              {response.mime_type ?? "text/plain"}
            </span>
            <span className="mini-pill">
              {formatBytes(response.size_bytes ?? response.content.length)}
            </span>
          </div>
          <h3>{artifact.display_name ?? artifact.name}</h3>
          <p className="small muted">{artifact.summary}</p>
        </div>
        <button
          className="ghost-button"
          type="button"
          onClick={() => void copyContent()}
          title="Copy content"
          aria-label="Copy content"
        >
          {copied ? <ClipboardCheck size={15} /> : <Clipboard size={15} />}
        </button>
      </div>

      {artifact.type === "plc_code" ? (
        <CodePreview content={response.content} />
      ) : artifact.type === "test_report" ? (
        <TestReportPreview artifact={artifact} content={response.content} parsed={parsed} />
      ) : artifact.type === "gate_report" ? (
        <GateReportPreview parsed={parsed} content={response.content} />
      ) : artifact.type === "io_contract" ? (
        <IoContractPreview parsed={parsed} content={response.content} />
      ) : artifact.type === "final_report" ? (
        <FinalReportArtifactPreview parsed={parsed} content={response.content} />
      ) : response.mime_type === "application/json" ? (
        <JsonPreview parsed={state.parsed} />
      ) : (
        <TextPreview content={response.content} />
      )}
    </section>
  );
}

function CodePreview({ content }: { content: string }) {
  const lines = content.split(/\r?\n/).length;
  return (
    <div className="typed-preview">
      <div className="preview-toolbar">
        <span className="mini-pill">Structured Text</span>
        <span className="mini-pill">{lines} lines</span>
      </div>
      <pre className="preview code-preview">{content}</pre>
    </div>
  );
}

function TestReportPreview({
  artifact,
  content,
  parsed,
}: {
  artifact: Artifact;
  content: string;
  parsed: JsonRecord | null;
}) {
  const metadata = artifact.metadata.test_metadata;
  const tags = artifact.metadata.tags ?? [];
  const status =
    metadata?.status ?? stringValue(parsed?.status) ?? stringValue(parsed?.outcome);
  const cases =
    recordsArray(parsed?.cases).length > 0
      ? recordsArray(parsed?.cases)
      : recordsArray(parsed?.test_cases);

  return (
    <div className="typed-preview">
      <div className="report-stat-grid">
        <Metric label="Status" value={status ?? "unknown"} tone={statusTone(status)} />
        <Metric label="Total" value={metadata?.total ?? numberValue(parsed?.total)} />
        <Metric label="Passed" value={metadata?.passed ?? numberValue(parsed?.passed)} tone="ok" />
        <Metric label="Failed" value={metadata?.failed ?? numberValue(parsed?.failed)} tone={metadata?.failed ? "bad" : "ok"} />
      </div>
      {tags.includes("llm-output-fallback") ? (
        <div className="notice">
          <AlertTriangle size={14} />
          Worker returned fallback output; review the report before treating it as executed evidence.
        </div>
      ) : null}
      {cases.length ? (
        <div className="case-list">
          {cases.slice(0, 8).map((item, index) => (
            <div className="case-row" key={index}>
              <span
                className="status-pill"
                data-tone={statusTone(stringValue(item.status))}
              >
                {stringValue(item.status) ?? "case"}
              </span>
              <strong>
                {stringValue(item.name) ??
                  stringValue(item.test_name) ??
                  stringValue(item.id) ??
                  `Case ${index + 1}`}
              </strong>
              <small>{stringValue(item.summary) ?? stringValue(item.description)}</small>
            </div>
          ))}
        </div>
      ) : (
        <TextPreview content={content} />
      )}
    </div>
  );
}

function GateReportPreview({
  parsed,
  content,
}: {
  parsed: JsonRecord | null;
  content: string;
}) {
  const assessment = recordValue(parsed?.assessment);
  const outcomes = recordsArray(assessment?.outcomes);
  const status = stringValue(assessment?.status);

  if (!assessment) {
    return <TextPreview content={content} />;
  }

  return (
    <div className="typed-preview">
      <div className="notice" data-tone={statusTone(status)}>
        <ShieldCheck size={14} />
        {stringValue(assessment.message) ?? `Quality gate ${status ?? "recorded"}`}
      </div>
      <div className="check-list">
        {outcomes.map((outcome, index) => (
          <div className="check-row" key={stringValue(outcome.gate_type) ?? index}>
            {statusTone(stringValue(outcome.status)) === "ok" ? (
              <CheckCircle2 size={15} />
            ) : (
              <AlertTriangle size={15} />
            )}
            <span>{stringValue(outcome.gate_type) ?? `Gate ${index + 1}`}</span>
            <strong data-tone={statusTone(stringValue(outcome.status))}>
              {stringValue(outcome.status) ?? "unknown"}
            </strong>
          </div>
        ))}
      </div>
      <details className="payload-details">
        <summary>Raw gate report</summary>
        <pre>{JSON.stringify(parsed, null, 2)}</pre>
      </details>
    </div>
  );
}

function IoContractPreview({
  parsed,
  content,
}: {
  parsed: JsonRecord | null;
  content: string;
}) {
  const inputs = recordsArray(parsed?.inputs);
  const outputs = recordsArray(parsed?.outputs);
  const signals = inputs.length || outputs.length;

  if (!signals) {
    return parsed ? <JsonPreview parsed={parsed} /> : <TextPreview content={content} />;
  }

  return (
    <div className="typed-preview">
      <SignalTable title="Inputs" rows={inputs} />
      <SignalTable title="Outputs" rows={outputs} />
    </div>
  );
}

function FinalReportArtifactPreview({
  parsed,
  content,
}: {
  parsed: JsonRecord | null;
  content: string;
}) {
  if (!parsed) {
    return <TextPreview content={content} />;
  }
  return (
    <div className="typed-preview">
      <p className="report-text">
        {stringValue(parsed.summary) ?? "Final report payload is available."}
      </p>
      <div className="report-stat-grid">
        <Metric label="Status" value={stringValue(parsed.final_task_status) ?? "available"} tone={statusTone(stringValue(parsed.final_task_status))} />
        <Metric label="Version" value={numberValue(parsed.report_version) ?? "n/a"} />
        <Metric label="Task" value={shortId(stringValue(parsed.task_id) ?? "unknown")} />
      </div>
      <details className="payload-details">
        <summary>Raw final report</summary>
        <pre>{JSON.stringify(parsed, null, 2)}</pre>
      </details>
    </div>
  );
}

function JsonPreview({ parsed }: { parsed: unknown }) {
  return <pre className="preview">{JSON.stringify(parsed, null, 2)}</pre>;
}

function TextPreview({ content }: { content: string }) {
  return <pre className="preview">{content}</pre>;
}

function SignalTable({ title, rows }: { title: string; rows: JsonRecord[] }) {
  if (!rows.length) {
    return null;
  }
  return (
    <div className="signal-table-wrap">
      <h4>{title}</h4>
      <table className="signal-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={stringValue(row.name) ?? index}>
              <td>{stringValue(row.name) ?? stringValue(row.id) ?? "-"}</td>
              <td>{stringValue(row.type) ?? stringValue(row.data_type) ?? "-"}</td>
              <td>
                {stringValue(row.description) ??
                  stringValue(row.summary) ??
                  stringValue(row.role) ??
                  "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number | null | undefined;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="report-stat" data-tone={tone}>
      <span>
        <small>{label}</small>
        <strong>{value ?? "n/a"}</strong>
      </span>
    </div>
  );
}

function sortArtifacts(artifacts: Artifact[]): Artifact[] {
  return [...artifacts].sort((left, right) => {
    const leftInternal = left.visibility === "internal" ? 1 : 0;
    const rightInternal = right.visibility === "internal" ? 1 : 0;
    if (leftInternal !== rightInternal) {
      return leftInternal - rightInternal;
    }
    const leftRank = DELIVERY_ORDER.indexOf(left.type);
    const rightRank = DELIVERY_ORDER.indexOf(right.type);
    const normalizedLeft = leftRank === -1 ? DELIVERY_ORDER.length : leftRank;
    const normalizedRight = rightRank === -1 ? DELIVERY_ORDER.length : rightRank;
    if (normalizedLeft !== normalizedRight) {
      return normalizedLeft - normalizedRight;
    }
    return right.version - left.version;
  });
}

function artifactIcon(type: string) {
  if (type === "plc_code") {
    return <FileCode2 size={14} />;
  }
  if (type.endsWith("_report")) {
    return <FileText size={14} />;
  }
  if (type === "io_contract" || type === "gate_report") {
    return <ShieldCheck size={14} />;
  }
  if (type === "main_agent_log" || type === "worker_log") {
    return <FileJson2 size={14} />;
  }
  return <Package size={14} />;
}

function statusTone(value?: string | null): "ok" | "warn" | "bad" {
  if (
    value &&
    ["passed", "pass", "succeeded", "success", "available", "completed"].includes(
      value,
    )
  ) {
    return "ok";
  }
  if (value && ["failed", "fail", "error", "blocked", "blocking"].includes(value)) {
    return "bad";
  }
  return "warn";
}

function recordValue(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function recordsArray(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const record = recordValue(item);
        return record ? [record] : [];
      })
    : [];
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function shortId(value: string): string {
  if (value.length <= 18) {
    return value;
  }
  return `${value.slice(0, 10)}...${value.slice(-5)}`;
}
