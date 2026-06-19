import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  Box,
  BrainCircuit,
  CheckCircle2,
  CircleDot,
  Clock3,
  FileText,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Route,
  Sparkles,
  SquareTerminal,
  Wrench,
  XCircle,
} from "lucide-react";

import type {
  ProjectContext,
  RouterEvent,
  TaskState,
} from "../../api/router/types";
import type { StreamState } from "../../api/router/events";

interface SseConsoleProps {
  task: TaskState | null;
  events: RouterEvent[];
  streamState: StreamState;
  streamError?: string;
  latestSeq: number;
  loading: boolean;
  canAppendMessage: boolean;
  canCancel: boolean;
  traceOpen: boolean;
  onCreateTask: (message: string, context: ProjectContext) => Promise<unknown>;
  onAppendMessage: (message: string) => Promise<unknown>;
  onRefresh: () => void;
  onToggleTrace: () => void;
  onCancel: () => void;
  onArtifactClick: (artifactId: string) => void;
}

type TranscriptItem =
  | UserTranscriptItem
  | AgentTranscriptItem
  | PlanTranscriptItem
  | ToolTranscriptItem
  | StatusTranscriptItem;

interface BaseTranscriptItem {
  id: string;
  seq: number;
  createdAt?: string;
}

interface UserTranscriptItem extends BaseTranscriptItem {
  kind: "user";
  content: string;
}

interface AgentTranscriptItem extends BaseTranscriptItem {
  kind: "agent";
  content: string;
  label?: string;
  finalReportArtifactId?: string;
}

interface PlanTranscriptItem extends BaseTranscriptItem {
  kind: "plan";
  summary?: string;
  steps: string[];
}

interface ToolTranscriptItem extends BaseTranscriptItem {
  kind: "tool";
  toolName: string;
  title: string;
  turnIndex?: number;
  status: "running" | "applied" | "rejected" | "failed" | "no-op" | "observed";
  rationale?: string;
  summary?: string;
  artifactIds: string[];
  failureIds: string[];
  workerJobId?: string;
  workerType?: string;
  resultSeq?: number;
  resultCreatedAt?: string;
  argumentsPreview?: string;
  details?: unknown;
}

interface StatusTranscriptItem extends BaseTranscriptItem {
  kind: "status";
  eventType: string;
  title: string;
  message?: string;
  status?: string;
}

export function SseConsole({
  task,
  events,
  streamState,
  streamError,
  latestSeq,
  loading,
  canAppendMessage,
  canCancel,
  traceOpen,
  onCreateTask,
  onAppendMessage,
  onRefresh,
  onToggleTrace,
  onCancel,
  onArtifactClick,
}: SseConsoleProps) {
  const [message, setMessage] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);
  const transcript = useMemo(() => buildTranscript(task, events), [task, events]);
  const openQuestions = useMemo(
    () => task?.unresolved_questions.filter((question) => question.status === "open") ?? [],
    [task?.unresolved_questions],
  );
  const canSubmit =
    message.trim().length > 0 && !loading && (!task || canAppendMessage);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [transcript.length, latestSeq, streamState]);

  const submitMessage = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || loading) {
      return;
    }

    if (task) {
      if (!canAppendMessage) {
        return;
      }
      await onAppendMessage(trimmed);
    } else {
      await onCreateTask(trimmed, {
        target_plc_language: "ST",
        target_platform: "Codesys",
      });
    }
    setMessage("");
  };

  return (
    <section className="sse-stage">
      <header className="stage-header">
        <div className="stage-title-block">
          <span data-state={streamState} className="stream-dot" aria-hidden="true" />
          <div>
            <h1>{task?.title ?? "Playground"}</h1>
            <p>{task?.task_id ?? "Router Agent transcript"}</p>
          </div>
        </div>
        <div className="stage-actions">
          <span className="pill">seq {latestSeq}</span>
          <button
            className="ghost-button"
            type="button"
            title="Refresh"
            aria-label="Refresh"
            onClick={onRefresh}
            disabled={!task}
          >
            <RefreshCw size={15} />
          </button>
          <button
            aria-pressed={traceOpen}
            className="ghost-button"
            type="button"
            title="Trace"
            aria-label="Trace"
            onClick={onToggleTrace}
            disabled={!task}
          >
            <SquareTerminal size={15} />
          </button>
          <button
            className="ghost-button danger"
            type="button"
            title="Cancel"
            aria-label="Cancel"
            onClick={onCancel}
            disabled={!canCancel || loading}
          >
            <XCircle size={15} />
          </button>
        </div>
      </header>

      <div className="sse-scroll">
        {streamError ? (
          <div className="inline-alert">
            <AlertTriangle size={15} />
            {streamError}
          </div>
        ) : null}

        {!task && events.length === 0 ? (
          <div className="console-empty">
            <h2>我们应该让 Router Agent 构建什么？</h2>
          </div>
        ) : null}

        {task && transcript.length === 1 && events.length === 0 ? (
          <div className="console-empty compact">
            <h2>{task.normalized_goal ?? task.raw_user_request}</h2>
            <p>等待 Main Agent 输出。</p>
          </div>
        ) : null}

        {transcript.length ? (
          <div className="agent-transcript" aria-label="Main Agent transcript">
            {transcript.map((item) => (
              <TranscriptRow
                item={item}
                key={item.id}
                onArtifactClick={onArtifactClick}
              />
            ))}
            <div ref={endRef} />
          </div>
        ) : null}

        {events.length ? (
          <details className="raw-stream-details">
            <summary>Raw SSE events · {events.length}</summary>
            <div className="raw-event-list">
              {events.slice(-16).map((event) => (
                <EventPayload event={event} key={event.event_id} />
              ))}
            </div>
          </details>
        ) : null}
      </div>

      {openQuestions.length ? (
        <div className="question-strip">
          {openQuestions.map((question) => (
            <p key={question.question_id}>
              <strong>{question.required ? "Required" : "Optional"}:</strong>{" "}
              {question.question}
            </p>
          ))}
        </div>
      ) : null}

      <form className="prompt-card" onSubmit={submitMessage}>
        <textarea
          aria-label="Message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder={task ? "继续补充任务要求..." : "随心输入"}
        />
        <div className="prompt-footer">
          <div className="prompt-context">
            <span>{task ? "append" : "new task"}</span>
            <span>SSE {streamState}</span>
            <span>{task?.phase ?? "idle"}</span>
          </div>
          <button
            className="send-button"
            type="submit"
            disabled={!canSubmit}
            aria-label={task ? "Send message" : "Create task"}
            title={task ? "Send message" : "Create task"}
          >
            <ArrowUp size={18} />
          </button>
        </div>
      </form>
    </section>
  );
}

function TranscriptRow({
  item,
  onArtifactClick,
}: {
  item: TranscriptItem;
  onArtifactClick: (artifactId: string) => void;
}) {
  if (item.kind === "user") {
    return (
      <article className="transcript-message user-message">
        <div className="message-avatar">U</div>
        <div className="message-body">
          <p>{item.content}</p>
        </div>
      </article>
    );
  }

  if (item.kind === "agent") {
    return (
      <article className="transcript-message agent-message">
        <div className="message-avatar">
          <BrainCircuit size={16} />
        </div>
        <div className="message-body">
          {item.label ? <span className="message-label">{item.label}</span> : null}
          <p>{item.content}</p>
          {item.finalReportArtifactId ? (
            <button
              className="artifact-chip"
              type="button"
              onClick={() => onArtifactClick(item.finalReportArtifactId!)}
            >
              <FileText size={13} />
              final_report
            </button>
          ) : null}
        </div>
      </article>
    );
  }

  if (item.kind === "plan") {
    return (
      <article className="transcript-step plan-step">
        <div className="step-icon">
          <Route size={15} />
        </div>
        <div className="step-main">
          <div className="step-heading">
            <span>更新执行计划</span>
            <span className="step-time">{formatTime(item.createdAt)}</span>
          </div>
          {item.summary ? <p>{item.summary}</p> : null}
          {item.steps.length ? (
            <ol className="plan-list">
              {item.steps.slice(0, 6).map((step, index) => (
                <li key={`${item.id}-${index}`}>{step}</li>
              ))}
            </ol>
          ) : null}
        </div>
      </article>
    );
  }

  if (item.kind === "tool") {
    return (
      <article className="transcript-step tool-step" data-status={item.status}>
        <div className="step-icon">{toolIcon(item.toolName, item.status)}</div>
        <div className="step-main">
          <div className="step-heading">
            <span>{item.title}</span>
            <span data-tone={toolTone(item.status)} className="status-pill">
              {item.status === "running" ? <Loader2 size={13} /> : null}
              {item.status}
            </span>
            <span className="step-time">
              {formatTime(item.resultCreatedAt ?? item.createdAt)}
            </span>
          </div>
          {item.rationale ? <p>{item.rationale}</p> : null}
          {item.summary && item.summary !== item.rationale ? (
            <p className="tool-result-summary">{item.summary}</p>
          ) : null}
          <div className="step-links">
            {item.workerType ? (
              <span className="mini-pill">{item.workerType}</span>
            ) : null}
            {item.workerJobId ? (
              <span className="mini-pill">{shortId(item.workerJobId)}</span>
            ) : null}
            {item.artifactIds.map((artifactId) => (
              <button
                className="artifact-chip"
                key={artifactId}
                type="button"
                onClick={() => onArtifactClick(artifactId)}
              >
                <Box size={13} />
                {shortId(artifactId)}
              </button>
            ))}
            {item.failureIds.map((failureId) => (
              <span data-tone="bad" className="status-pill" key={failureId}>
                {shortId(failureId)}
              </span>
            ))}
          </div>
          {item.argumentsPreview || item.details ? (
            <details className="payload-details compact-payload">
              <summary>details</summary>
              {item.argumentsPreview ? <pre>{item.argumentsPreview}</pre> : null}
              {item.details ? <pre>{formatJson(item.details)}</pre> : null}
            </details>
          ) : null}
        </div>
      </article>
    );
  }

  return (
    <article className="transcript-step status-step" data-status={item.status}>
      <div className="step-icon">
        <CircleDot size={14} />
      </div>
      <div className="step-main">
        <div className="step-heading">
          <span>{item.title}</span>
          <span className="step-time">{formatTime(item.createdAt)}</span>
        </div>
        {item.message ? <p>{item.message}</p> : null}
      </div>
    </article>
  );
}

function EventPayload({ event }: { event: RouterEvent }) {
  return (
    <details className="payload-details raw-payload">
      <summary>
        #{event.seq} · {event.type}
      </summary>
      <pre>{JSON.stringify(event.payload, null, 2)}</pre>
    </details>
  );
}

function buildTranscript(
  task: TaskState | null,
  events: RouterEvent[],
): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  const pendingTools = new Map<string, ToolTranscriptItem[]>();

  if (task?.raw_user_request) {
    items.push({
      id: `user-${task.task_id}`,
      kind: "user",
      seq: 0,
      content: task.raw_user_request,
      createdAt: task.created_at,
    });
  }

  for (const event of events) {
    if (event.type === "main_agent.message") {
      const content = payloadString(event, "content") ?? event.message;
      if (content) {
        items.push({
          id: event.event_id,
          kind: "agent",
          seq: event.seq,
          createdAt: event.created_at,
          content,
        });
      }
      continue;
    }

    if (event.type === "main_agent.plan_updated") {
      items.push({
        id: event.event_id,
        kind: "plan",
        seq: event.seq,
        createdAt: event.created_at,
        summary: payloadString(event, "summary") ?? event.message ?? undefined,
        steps: planSteps(event.payload.plan),
      });
      continue;
    }

    if (event.type === "main_agent.tool_called") {
      const toolName = payloadString(event, "tool_name") ?? "tool";
      const turnIndex = payloadNumber(event, "turn_index");
      const item: ToolTranscriptItem = {
        id: event.event_id,
        kind: "tool",
        seq: event.seq,
        createdAt: event.created_at,
        toolName,
        title: toolTitle(toolName),
        turnIndex,
        status: "running",
        rationale: payloadString(event, "rationale_summary") ?? event.message ?? undefined,
        artifactIds: payloadStringArray(event.payload.input_artifact_ids),
        failureIds: [],
        argumentsPreview: previewArguments(event.payload.arguments),
      };
      items.push(item);
      const key = toolKey(toolName, turnIndex);
      pendingTools.set(key, [...(pendingTools.get(key) ?? []), item]);
      continue;
    }

    if (event.type === "main_agent.tool_result") {
      const toolName = payloadString(event, "tool_name") ?? "tool";
      const turnIndex = payloadNumber(event, "turn_index");
      const key = toolKey(toolName, turnIndex);
      const matched = (pendingTools.get(key) ?? []).find((item) => !item.resultSeq);
      const status = normalizeToolStatus(payloadString(event, "status"));
      const artifactIds = payloadStringArray(event.payload.artifact_ids);
      const failureIds = payloadStringArray(event.payload.failure_ids);

      if (matched) {
        matched.status = status;
        matched.resultSeq = event.seq;
        matched.resultCreatedAt = event.created_at;
        matched.summary = payloadString(event, "summary") ?? event.message ?? undefined;
        matched.artifactIds = unique([...matched.artifactIds, ...artifactIds]);
        matched.failureIds = unique([...matched.failureIds, ...failureIds]);
        matched.workerJobId = payloadString(event, "worker_job_id");
        matched.workerType = payloadString(event, "worker_type");
        matched.details = event.payload.details;
        for (const pending of pendingTools.get(key) ?? []) {
          if (!pending.resultSeq && pending.seq < event.seq) {
            pending.status = "observed";
            pending.resultCreatedAt = event.created_at;
            pending.summary =
              pending.summary ??
              "Duplicate tool call in the same turn; result was covered by the adjacent tool result.";
          }
        }
      } else {
        items.push({
          id: event.event_id,
          kind: "tool",
          seq: event.seq,
          createdAt: event.created_at,
          toolName,
          title: `${toolTitle(toolName)} result`,
          turnIndex,
          status,
          summary: payloadString(event, "summary") ?? event.message ?? undefined,
          artifactIds,
          failureIds,
          workerJobId: payloadString(event, "worker_job_id"),
          workerType: payloadString(event, "worker_type"),
          resultSeq: event.seq,
          resultCreatedAt: event.created_at,
          details: event.payload.details,
        });
      }
      continue;
    }

    if (event.type === "main_agent.completed") {
      items.push({
        id: event.event_id,
        kind: "agent",
        seq: event.seq,
        createdAt: event.created_at,
        label: payloadString(event, "final_task_status") ?? "completed",
        content: payloadString(event, "summary") ?? event.message ?? "Main Agent completed.",
        finalReportArtifactId: payloadString(event, "final_report_artifact_id"),
      });
      continue;
    }

    if (
      event.type === "task.waiting_user" ||
      event.type === "task.succeeded" ||
      event.type === "task.partial_failed" ||
      event.type === "task.failed" ||
      event.type === "task.cancelled" ||
      event.type === "main_agent.clarification_requested"
    ) {
      items.push({
        id: event.event_id,
        kind: "status",
        seq: event.seq,
        createdAt: event.created_at,
        eventType: event.type,
        title: event.title,
        message: event.message ?? payloadString(event, "summary"),
        status: statusFromEvent(event.type),
      });
    }
  }

  return items.sort((left, right) => left.seq - right.seq);
}

function planSteps(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((step, index) => {
      if (typeof step === "string") {
        return step;
      }
      if (!step || typeof step !== "object") {
        return `Step ${index + 1}`;
      }
      const record = step as Record<string, unknown>;
      const title =
        stringValue(record.title) ??
        stringValue(record.step) ??
        stringValue(record.description) ??
        `Step ${index + 1}`;
      const status = stringValue(record.status);
      return status ? `${title} · ${status}` : title;
    })
    .filter(Boolean);
}

function previewArguments(value: unknown): string | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const preview: Record<string, unknown> = {};
  for (const key of [
    "objective",
    "summary",
    "final_status",
    "task_type",
    "requires_test",
    "requires_formal",
  ]) {
    if (record[key] !== undefined) {
      preview[key] = record[key];
    }
  }
  return Object.keys(preview).length ? formatJson(preview) : undefined;
}

function payloadString(event: RouterEvent, key: string): string | undefined {
  return stringValue(event.payload[key]);
}

function payloadNumber(event: RouterEvent, key: string): number | undefined {
  const value = event.payload[key];
  return typeof value === "number" ? value : undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function payloadStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function normalizeToolStatus(value: string | undefined): ToolTranscriptItem["status"] {
  if (
    value === "applied" ||
    value === "rejected" ||
    value === "failed" ||
    value === "no-op"
  ) {
    return value;
  }
  return value ? "observed" : "applied";
}

function toolKey(toolName: string, turnIndex: number | undefined): string {
  return `${turnIndex ?? "unknown"}:${toolName}`;
}

function toolTitle(toolName: string): string {
  const labels: Record<string, string> = {
    update_plan: "更新计划",
    request_clarification: "请求澄清",
    call_plc_dev: "调用 plc-dev",
    call_plc_test: "调用 plc-test",
    call_plc_formal: "调用 plc-formal",
    call_plc_repair: "调用 plc-repair",
    run_parallel_workers: "并行调用 worker",
    read_artifact: "读取 Artifact",
    run_quality_gate: "运行 Quality Gate",
    write_final_report: "写入最终报告",
    finish_task: "完成任务",
  };
  return labels[toolName] ?? toolName;
}

function toolIcon(toolName: string, status: ToolTranscriptItem["status"]) {
  if (status === "running") {
    return <Loader2 size={15} />;
  }
  if (toolName.startsWith("call_plc") || toolName === "run_parallel_workers") {
    return <Wrench size={15} />;
  }
  if (toolName === "run_quality_gate") {
    return <CheckCircle2 size={15} />;
  }
  if (toolName === "write_final_report") {
    return <FileText size={15} />;
  }
  if (toolName === "finish_task") {
    return <Sparkles size={15} />;
  }
  return <SquareTerminal size={15} />;
}

function toolTone(status: ToolTranscriptItem["status"]): "ok" | "warn" | "bad" {
  if (status === "applied" || status === "no-op" || status === "observed") {
    return "ok";
  }
  if (status === "failed" || status === "rejected") {
    return "bad";
  }
  return "warn";
}

function statusFromEvent(type: string): string {
  if (type === "task.succeeded") {
    return "applied";
  }
  if (type === "task.failed" || type === "task.cancelled") {
    return "failed";
  }
  if (type === "task.partial_failed" || type === "task.waiting_user") {
    return "rejected";
  }
  return "observed";
}

function formatTime(value?: string): string {
  if (!value) {
    return "";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function shortId(value: string): string {
  if (value.length <= 18) {
    return value;
  }
  return `${value.slice(0, 10)}...${value.slice(-5)}`;
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}
