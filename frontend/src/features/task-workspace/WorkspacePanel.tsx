import { FileText, FolderTree, RefreshCw } from "lucide-react";

import type { TaskState, TaskTraceSummary } from "../../api/router/types";

interface WorkspacePanelProps {
  task: TaskState | null;
  trace: TaskTraceSummary | null;
  loading: boolean;
  error?: string;
  onRefresh: () => void;
}

interface WorkspacePathRow {
  path: string;
  role?: string;
  source: string;
  exists?: boolean | null;
  sizeBytes?: number | null;
}

export function WorkspacePanel({
  task,
  trace,
  loading,
  error,
  onRefresh,
}: WorkspacePanelProps) {
  const rows = workspaceRows(task, trace);
  const fileRows = rows.filter((row) => isUserFilePath(row.path));
  const reportRows = rows.filter((row) => isReportPath(row.path));
  const systemRows = rows.filter((row) => isSystemPath(row.path));

  return (
    <section className="workspace-panel stack">
      <div className="panel-header">
        <h2 className="panel-title">Workspace</h2>
        <button className="button secondary" type="button" onClick={onRefresh}>
          <RefreshCw size={14} />
          Reload
        </button>
      </div>
      {loading ? <div className="notice">Loading workspace paths.</div> : null}
      {error ? <div className="notice error-box">{error}</div> : null}
      {!rows.length ? (
        <div className="empty-state">
          <div>
            <h3 className="empty-title">No workspace files yet</h3>
            <p className="small muted">Generated code and reports will appear here.</p>
          </div>
        </div>
      ) : null}
      {fileRows.length ? (
        <WorkspaceSection title="Files" rows={fileRows} icon="file" />
      ) : null}
      {reportRows.length ? (
        <WorkspaceSection title="Reports" rows={reportRows} icon="report" />
      ) : null}
      {systemRows.length ? (
        <WorkspaceSection
          title={`System (${systemRows.length})`}
          rows={systemRows}
          icon="report"
          collapsed
        />
      ) : null}
      {trace?.worker_jobs.length ? (
        <section className="report-section">
          <h3>Worker Paths</h3>
          <div className="trace-grid">
            {trace.worker_jobs.map((job) => (
              <div className="trace-row" key={job.worker_job_id}>
                <strong>
                  {job.worker_type} · {job.status}
                </strong>
                <span className="small muted">{job.worker_job_id}</span>
                <PathList label="read" paths={job.read_paths} />
                <PathList label="written" paths={job.written_paths} />
                <PathList label="reports" paths={job.report_paths} />
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function WorkspaceSection({
  title,
  rows,
  icon,
  collapsed = false,
}: {
  title: string;
  rows: WorkspacePathRow[];
  icon: "file" | "report";
  collapsed?: boolean;
}) {
  const Icon = icon === "report" ? FolderTree : FileText;
  const content = (
    <div className="file-list">
      {rows.map((row) => (
        <div className="file-row" key={`${row.source}:${row.path}`}>
          <div className="file-row-main">
            <strong>
              <Icon size={14} />
              {displayPath(row.path)}
            </strong>
            <span className="file-type">{row.role ?? row.source}</span>
          </div>
          <div className="inline-list">
            <span className="mini-pill">{row.source}</span>
            {row.exists !== undefined ? (
              <span data-tone={row.exists ? "ok" : "warn"} className="status-pill">
                {row.exists ? "exists" : "missing"}
              </span>
            ) : null}
            {row.sizeBytes !== undefined && row.sizeBytes !== null ? (
              <span className="mini-pill">{formatBytes(row.sizeBytes)}</span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
  if (collapsed) {
    return (
      <details className="report-section">
        <summary>
          <h3>{title}</h3>
        </summary>
        {content}
      </details>
    );
  }
  return (
    <section className="report-section">
      <h3>{title}</h3>
      {content}
    </section>
  );
}

function PathList({ label, paths }: { label: string; paths: string[] }) {
  if (!paths.length) {
    return null;
  }
  const visiblePaths = paths.filter((path) => !isRouterPath(path));
  const routerPaths = paths.filter(isRouterPath);
  return (
    <div className="inline-list">
      <span className="mini-pill">{label}</span>
      {visiblePaths.map((path) => (
        <span className="mini-pill" key={`${label}:${path}`}>
          {path}
        </span>
      ))}
      {routerPaths.length ? (
        <details className="inline-details">
          <summary className="mini-pill">{routerPaths.length} .router paths</summary>
          <div className="inline-list">
            {routerPaths.map((path) => (
              <span className="mini-pill" key={`${label}:${path}`}>
                {path}
              </span>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function workspaceRows(
  task: TaskState | null,
  trace: TaskTraceSummary | null,
): WorkspacePathRow[] {
  const byPath = new Map<string, WorkspacePathRow>();
  const add = (row: WorkspacePathRow) => {
    const existing = byPath.get(row.path);
    if (!existing) {
      byPath.set(row.path, row);
      return;
    }
    byPath.set(row.path, {
      ...existing,
      role: existing.role ?? row.role,
      exists: existing.exists ?? row.exists,
      sizeBytes: existing.sizeBytes ?? row.sizeBytes,
    });
  };

  if (task?.current_files) {
    for (const path of task.current_files.all_paths ?? []) {
      add({ path, role: currentFileRole(task, path), source: "task" });
    }
  }
  for (const file of trace?.files ?? []) {
    add({
      path: file.path,
      source: "trace",
      exists: file.exists,
      sizeBytes: file.size_bytes,
    });
  }
  for (const job of trace?.worker_jobs ?? []) {
    for (const path of job.input_paths) {
      add({ path, source: `${job.worker_type} input` });
    }
    for (const path of job.read_paths) {
      add({ path, source: `${job.worker_type} read` });
    }
    for (const path of job.written_paths) {
      add({ path, source: `${job.worker_type} wrote` });
    }
    for (const path of job.report_paths) {
      add({ path, source: `${job.worker_type} report` });
    }
  }
  return [...byPath.values()].sort(compareWorkspaceRows);
}

function currentFileRole(task: TaskState, path: string): string | undefined {
  for (const [key, value] of Object.entries(task.current_files)) {
    if (key === "all_paths") {
      continue;
    }
    if (value === path) {
      return key;
    }
  }
  return undefined;
}

function isReportPath(path: string): boolean {
  return path.startsWith(".router/reports/") || path.includes("_report");
}

function isUserFilePath(path: string): boolean {
  return !isRouterPath(path) && !isReportPath(path);
}

function isSystemPath(path: string): boolean {
  return isRouterPath(path) && !isReportPath(path);
}

function isRouterPath(path: string): boolean {
  return path === ".router" || path.startsWith(".router/");
}

function displayPath(path: string): string {
  if (!path.startsWith(".router/reports/")) {
    return path;
  }
  return path.replace(".router/reports/", "reports/");
}

function compareWorkspaceRows(left: WorkspacePathRow, right: WorkspacePathRow): number {
  const leftRank = pathRank(left.path);
  const rightRank = pathRank(right.path);
  if (leftRank !== rightRank) {
    return leftRank - rightRank;
  }
  return left.path.localeCompare(right.path);
}

function pathRank(path: string): number {
  if (path.startsWith("src/")) {
    return 0;
  }
  if (path.startsWith("tests/")) {
    return 1;
  }
  if (!isRouterPath(path)) {
    return 2;
  }
  if (isReportPath(path)) {
    return 3;
  }
  return 4;
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
