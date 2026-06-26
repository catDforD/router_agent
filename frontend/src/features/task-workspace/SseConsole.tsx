import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  SquareTerminal,
  Wrench,
  XCircle,
} from "lucide-react";

import type {
  ProjectContext,
  RouterEvent,
  TaskState,
  TokenUsage,
} from "../../api/router/types";
import type { StreamState } from "../../api/router/events";
import { MarkdownText } from "./MarkdownText";

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
  onNewTask: () => void;
  draftMessage?: string;
  focusSignal: number;
  resetSignal: number;
  onDraftConsumed: () => void;
}

type TranscriptItem =
  | UserTranscriptItem
  | AgentTranscriptItem
  | PlanTranscriptItem
  | ToolTranscriptItem
  | StatusTranscriptItem;

type ProcessTranscriptItem =
  | AgentTranscriptItem
  | PlanTranscriptItem
  | ToolTranscriptItem
  | StatusTranscriptItem;

interface TranscriptView {
  runs: TranscriptRunView[];
}

interface TranscriptRunView {
  id: string;
  taskId?: string;
  runId?: string;
  userMessage?: UserTranscriptItem;
  processItems: ProcessTranscriptItem[];
  finalAnswer?: AgentTranscriptItem;
  terminalStatus?: StatusTranscriptItem;
  finalReportArtifactId?: string;
  tokenUsage?: TokenUsage;
  isTerminal: boolean;
  latestSeq: number;
  createdAt?: string;
  latestAt?: string;
}

interface BaseTranscriptItem {
  id: string;
  seq: number;
  createdAt?: string;
  taskId?: string;
  runId?: string;
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
  tokenUsage?: TokenUsage;
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
  parameterChips: string[];
  argumentsPayload?: unknown;
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
  onNewTask,
  draftMessage,
  focusSignal,
  resetSignal,
  onDraftConsumed,
}: SseConsoleProps) {
  const [message, setMessage] = useState("");
  const [followupPendingAfterSeq, setFollowupPendingAfterSeq] = useState<number | null>(
    null,
  );
  const [processOpenByRun, setProcessOpenByRun] = useState<Record<string, boolean>>({});
  const [processTouchedByRun, setProcessTouchedByRun] = useState<Record<string, boolean>>({});
  const endRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const transcriptView = useMemo(() => buildTranscriptView(task, events), [task, events]);
  const transcriptContentCount = transcriptView.runs.reduce(
    (count, run) =>
      count +
      (run.userMessage ? 1 : 0) +
      run.processItems.length +
      (run.finalAnswer ? 1 : 0),
    0,
  );
  const latestRun = transcriptView.runs.at(-1);
  const latestTokenUsageLabel = formatTokenUsage(latestRun?.tokenUsage);
  const latestEventAt =
    latestRun?.latestAt ?? events.at(-1)?.created_at ?? task?.updated_at ?? task?.created_at;
  const elapsedLabel = formatElapsed(latestRun?.createdAt ?? task?.created_at, latestEventAt);
  const runKey = transcriptView.runs.map((run) => run.id).join(":");
  const openQuestions = useMemo(
    () => task?.unresolved_questions.filter((question) => question.status === "open") ?? [],
    [task?.unresolved_questions],
  );
  const terminal = isTerminalTask(task);
  const running = Boolean(
    task &&
      !terminal &&
      (loading ||
        task.status === "created" ||
        task.status === "running" ||
        task.status === "waiting_user" ||
        streamState === "connecting" ||
        streamState === "connected" ||
        streamState === "reconnecting"),
  );
  const canSubmit =
    message.trim().length > 0 &&
    !loading &&
    followupPendingAfterSeq === null &&
    (!task || canAppendMessage || terminal);

  useEffect(() => {
    if (followupPendingAfterSeq === null) {
      return;
    }
    if (
      events.some(
        (event) =>
          event.seq > followupPendingAfterSeq &&
          (event.type === "agent.message" ||
            event.type === "agent.final_response" ||
            event.type === "agent.completed"),
      )
    ) {
      setFollowupPendingAfterSeq(null);
    }
  }, [events, followupPendingAfterSeq]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [transcriptContentCount, latestSeq, streamState, processOpenByRun]);

  useEffect(() => {
    const activeRunIds = new Set(transcriptView.runs.map((run) => run.id));
    setProcessOpenByRun((current) => pruneRunState(current, activeRunIds));
    setProcessTouchedByRun((current) => pruneRunState(current, activeRunIds));
  }, [runKey, transcriptView.runs]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, [focusSignal]);

  useEffect(() => {
    setMessage("");
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [resetSignal]);

  useEffect(() => {
    if (!draftMessage) {
      return;
    }
    setMessage(draftMessage);
    onDraftConsumed();
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [draftMessage, onDraftConsumed]);

  const submitMessage = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || loading || followupPendingAfterSeq !== null) {
      return;
    }

    if (task) {
      if (!canAppendMessage && !terminal) {
        return;
      }
      if (terminal) {
        setFollowupPendingAfterSeq(latestSeq);
      }
      try {
        await onAppendMessage(trimmed);
      } catch (error) {
        if (terminal) {
          setFollowupPendingAfterSeq(null);
        }
        throw error;
      }
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
          <h1>
            <span>{elapsedLabel}</span>
            <span aria-hidden="true">·</span>
            <span>SSE {streamState}</span>
            <span aria-hidden="true">·</span>
            <span>seq {latestSeq}</span>
            {latestTokenUsageLabel ? (
              <>
                <span aria-hidden="true">·</span>
                <span>{latestTokenUsageLabel}</span>
              </>
            ) : null}
            {task?.task_id ? (
              <>
                <span aria-hidden="true">·</span>
                <span>{shortId(task.task_id)}</span>
              </>
            ) : null}
          </h1>
        </div>
        <div className="stage-actions">
          <button
            className="ghost-button"
            type="button"
            title="新任务"
            aria-label="新任务"
            onClick={onNewTask}
          >
            <Plus size={15} />
          </button>
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

        {task && transcriptContentCount === 0 && events.length === 0 ? (
          <div className="console-empty compact">
            <h2>{task.normalized_goal ?? task.raw_user_request}</h2>
            <p>等待 Main Agent 输出。</p>
          </div>
        ) : null}

        {transcriptContentCount ? (
          <div className="agent-transcript" aria-label="Main Agent transcript">
            {transcriptView.runs.map((run) => {
              const processOpen = processPanelOpen(
                run,
                processOpenByRun,
                processTouchedByRun,
              );
              return (
                <div className="transcript-run" key={run.id}>
                  {run.userMessage ? (
                    <TranscriptRow
                      item={run.userMessage}
                      onArtifactClick={onArtifactClick}
                    />
                  ) : null}
                  {run.processItems.length ? (
                    <ExecutionProcessPanel
                      elapsedLabel={formatElapsed(run.createdAt, run.latestAt)}
                      items={run.processItems}
                      onArtifactClick={onArtifactClick}
                      onToggle={() => {
                        setProcessTouchedByRun((current) => ({
                          ...current,
                          [run.id]: true,
                        }));
                        setProcessOpenByRun((current) => ({
                          ...current,
                          [run.id]: !processPanelOpen(
                            run,
                            current,
                            processTouchedByRun,
                          ),
                        }));
                      }}
                      open={processOpen}
                    />
                  ) : null}
                  {run.finalAnswer ? (
                    <FinalAnswer
                      item={run.finalAnswer}
                    />
                  ) : null}
                </div>
              );
            })}
            <div ref={endRef} />
          </div>
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
          ref={textareaRef}
          aria-label="Message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key !== "Enter" ||
              event.shiftKey ||
              event.nativeEvent.isComposing
            ) {
              return;
            }
            event.preventDefault();
            if (canSubmit) {
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={
            task && terminal
              ? "继续追问当前任务..."
              : task
                ? "继续补充任务要求..."
                : "描述新的 PLC 任务目标..."
          }
        />
        <div className="prompt-footer">
          <button
            className="send-button"
            data-running={running || followupPendingAfterSeq !== null}
            type="submit"
            disabled={!canSubmit}
            aria-label={
              followupPendingAfterSeq !== null
                ? "Answering follow-up"
                : task && terminal
                  ? "Send follow-up"
                  : task
                    ? "Send message"
                    : "Create task"
            }
            title={
              followupPendingAfterSeq !== null
                ? "正在回答"
                : task && terminal
                  ? "继续追问当前任务"
                  : task
                    ? "Send message"
                    : "Create task"
            }
          >
            {running || followupPendingAfterSeq !== null ? (
              <Loader2 size={18} />
            ) : (
              <ArrowUp size={18} />
            )}
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
      <article className="transcript-entry user-message">
        <div className="user-message-bubble">
          <MarkdownText content={item.content} variant="message" />
        </div>
      </article>
    );
  }

  if (item.kind === "agent") {
    return (
      <article className="transcript-entry transcript-message agent-message">
        <div className="message-avatar">
          <Sparkles size={13} />
        </div>
        <div className="message-body">
          {item.label ? <span className="message-label">{item.label}</span> : null}
          <MarkdownText content={item.content} variant="message" />
        </div>
      </article>
    );
  }

  if (item.kind === "plan") {
    return (
      <article className="transcript-entry transcript-activity plan-step">
        <div className="activity-icon">
          <CheckCircle2 size={14} />
        </div>
        <div className="activity-main">
          <div className="activity-heading">
            <span>更新执行计划</span>
            <span className="activity-time">{formatTime(item.createdAt)}</span>
          </div>
          {item.summary ? (
            <div className="activity-summary">
              <MarkdownText content={item.summary} variant="compact" />
            </div>
          ) : null}
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
      <article className="transcript-entry transcript-activity tool-step" data-status={item.status}>
        <div className="activity-icon">{toolIcon(item.toolName, item.status)}</div>
        <div className="activity-main">
          <div className="activity-heading">
            <span className="activity-title">{item.title}</span>
            <span data-tone={toolTone(item.status)} className="activity-status">
              {item.status === "running" ? <Loader2 size={13} /> : null}
              {item.status}
            </span>
            {item.parameterChips.map((chip) => (
              <span className="inline-code-chip" key={chip}>
                {chip}
              </span>
            ))}
            <span className="activity-time">
              {formatTime(item.resultCreatedAt ?? item.createdAt)}
            </span>
          </div>
          {item.summary ? (
            <div className="activity-summary">
              <MarkdownText content={item.summary} variant="compact" />
            </div>
          ) : null}
          <div className="activity-links">
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
          {item.argumentsPayload || item.details ? (
            <details className="payload-details compact-payload">
              <summary>details</summary>
              {item.argumentsPayload ? <pre>{formatJson(item.argumentsPayload)}</pre> : null}
              {item.details ? <pre>{formatJson(item.details)}</pre> : null}
            </details>
          ) : null}
        </div>
      </article>
    );
  }

  return (
    <article className="transcript-entry transcript-activity status-step" data-status={item.status}>
      <div className="activity-icon">
        <CircleDot size={14} />
      </div>
      <div className="activity-main">
        <div className="activity-heading">
          <span className="activity-title">{item.title}</span>
          <span className="activity-time">{formatTime(item.createdAt)}</span>
        </div>
        {item.message ? (
          <div className="activity-summary">
            <MarkdownText content={item.message} variant="compact" />
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ExecutionProcessPanel({
  elapsedLabel,
  items,
  onArtifactClick,
  onToggle,
  open,
}: {
  elapsedLabel: string;
  items: ProcessTranscriptItem[];
  onArtifactClick: (artifactId: string) => void;
  onToggle: () => void;
  open: boolean;
}) {
  return (
    <section className="execution-process" data-open={open}>
      <button
        aria-expanded={open}
        className="execution-process-header"
        type="button"
        onClick={onToggle}
      >
        <span className="process-header-main">
          <span>{elapsedLabel}</span>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open ? (
        <div className="process-items">
          {items.map((item) => (
            <TranscriptRow
              item={item}
              key={item.id}
              onArtifactClick={onArtifactClick}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function FinalAnswer({
  item,
}: {
  item: AgentTranscriptItem;
}) {
  const tokenUsageLabel = formatTokenUsage(item.tokenUsage);
  return (
    <article className="final-answer">
      <div className="final-answer-body">
        <MarkdownText content={item.content} variant="message" />
        {tokenUsageLabel ? (
          <span className="usage-chip">{tokenUsageLabel}</span>
        ) : null}
      </div>
    </article>
  );
}

function buildTranscriptView(
  task: TaskState | null,
  events: RouterEvent[],
): TranscriptView {
  const items = buildTranscript(task, events);
  const runIds = orderedRunIds(task, items, events);
  const runs = runIds
    .map((runId) => {
      const runItems = items.filter((item) => item.runId === runId);
      const runEvents = events.filter((event) => eventRunId(event) === runId);
      const userMessage = runItems.find(
        (item): item is UserTranscriptItem => item.kind === "user",
      );
      const finalReportArtifactId = latestFinalReportArtifactId(runEvents);
      const tokenUsage = latestCompletedTokenUsage(runEvents);
      const finalAnswer = pickFinalAnswer(
        runItems,
        runEvents,
        finalReportArtifactId,
        tokenUsage,
      );
      const isTerminal = isTerminalRun(task, runEvents, runId);
      const terminalStatus = isTerminal
        ? [...runItems]
            .reverse()
            .find(
              (item): item is StatusTranscriptItem =>
                item.kind === "status" && isTerminalEventType(item.eventType),
            )
        : undefined;
      const processItems: ProcessTranscriptItem[] = [];

      for (const item of runItems) {
        if (item.kind === "user") {
          continue;
        }
        if (finalAnswer && item.id === finalAnswer.id) {
          continue;
        }
        if (item.kind === "agent" && item.label) {
          continue;
        }
        processItems.push(item);
      }

      const latestSeq = Math.max(
        0,
        ...runItems.map((item) => item.seq),
        ...runEvents.map((event) => event.seq),
      );
      const createdAt = userMessage?.createdAt ?? runItems.at(0)?.createdAt;
      const latestAt =
        [...runItems].reverse().find((item) => item.createdAt)?.createdAt ??
        runEvents.at(-1)?.created_at ??
        createdAt;

      return {
        id: runId,
        taskId: runItems.at(0)?.taskId ?? runEvents.at(0)?.task_id,
        runId,
        userMessage,
        processItems,
        finalAnswer,
        terminalStatus,
        finalReportArtifactId,
        tokenUsage,
        isTerminal,
        latestSeq,
        createdAt,
        latestAt,
      };
    })
    .filter(
      (run) =>
        run.userMessage || run.processItems.length || run.finalAnswer || run.terminalStatus,
    );

  return { runs };
}

function orderedRunIds(
  task: TaskState | null,
  items: TranscriptItem[],
  events: RouterEvent[],
): string[] {
  const runIds: string[] = [];
  const seen = new Set<string>();
  const addRunId = (value?: string) => {
    if (!value || seen.has(value)) {
      return;
    }
    seen.add(value);
    runIds.push(value);
  };

  for (const event of events) {
    addRunId(eventRunId(event));
  }
  for (const item of items) {
    addRunId(item.runId ?? item.taskId);
  }
  addRunId(task?.task_id);
  return runIds;
}

function eventRunId(event: RouterEvent): string {
  return (
    payloadString(event, "run_id") ??
    stringValue(event.correlation.run_id) ??
    event.task_id
  );
}

function pickFinalAnswer(
  items: TranscriptItem[],
  events: RouterEvent[],
  finalReportArtifactId?: string,
  tokenUsage?: TokenUsage,
): AgentTranscriptItem | undefined {
  const finalResponseEvent = [...events]
    .reverse()
    .find((event) => event.type === "agent.final_response");
  const finalResponse = finalResponseEvent
    ? payloadString(finalResponseEvent, "content") ?? finalResponseEvent.message
    : undefined;
  if (finalResponseEvent && finalResponse) {
    return {
      id: finalResponseEvent.event_id,
      kind: "agent",
      seq: finalResponseEvent.seq,
      createdAt: finalResponseEvent.created_at,
      label: payloadString(finalResponseEvent, "final_status"),
      content: finalResponse,
      finalReportArtifactId,
      tokenUsage,
    };
  }

  const latestAgentMessage = [...items]
    .reverse()
    .find(
      (item): item is AgentTranscriptItem =>
        item.kind === "agent" && !item.label && Boolean(item.content),
    );
  if (latestAgentMessage) {
    return {
      ...latestAgentMessage,
      finalReportArtifactId:
        finalReportArtifactId ?? latestAgentMessage.finalReportArtifactId,
      tokenUsage: tokenUsage ?? latestAgentMessage.tokenUsage,
    };
  }

  const completedEvent = [...events]
    .reverse()
    .find((event) => event.type === "agent.completed");
  const completedSummary = completedEvent
    ? payloadString(completedEvent, "summary") ?? completedEvent.message
    : undefined;
  if (completedEvent && completedSummary) {
    return {
      id: `${completedEvent.event_id}-final-answer`,
      kind: "agent",
      seq: completedEvent.seq,
      createdAt: completedEvent.created_at,
      label: payloadString(completedEvent, "final_task_status") ?? "completed",
      content: completedSummary,
      finalReportArtifactId,
      tokenUsage,
    };
  }

  const terminalEvent = [...events]
    .reverse()
    .find((event) => isTerminalEventType(event.type));
  const terminalSummary = terminalEvent
    ? payloadString(terminalEvent, "summary") ?? terminalEvent.message
    : undefined;
  if (terminalEvent && terminalSummary) {
    return {
      id: `${terminalEvent.event_id}-final-answer`,
      kind: "agent",
      seq: terminalEvent.seq,
      createdAt: terminalEvent.created_at,
      label: terminalEvent.title,
      content: terminalSummary,
      finalReportArtifactId,
      tokenUsage,
    };
  }

  return undefined;
}

function buildTranscript(
  task: TaskState | null,
  events: RouterEvent[],
): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  const pendingTools = new Map<string, ToolTranscriptItem[]>();
  const hasUserEvents = events.some(
    (event) => event.type === "task.created" && payloadString(event, "message"),
  );

  if (task?.raw_user_request && !hasUserEvents) {
    items.push({
      id: `user-${task.task_id}`,
      kind: "user",
      seq: 0,
      taskId: task.task_id,
      runId: task.task_id,
      content: task.raw_user_request,
      createdAt: task.created_at,
    });
  }

  for (const event of events) {
    const taskId = event.task_id;
    const runId = eventRunId(event);
    if (event.type === "task.created") {
      const content = payloadString(event, "message");
      if (content) {
        items.push({
          id: `user-${event.event_id}`,
          kind: "user",
          seq: event.seq,
          taskId,
          runId,
          content,
          createdAt: event.created_at,
        });
      }
      continue;
    }

    if (event.type === "agent.message") {
      const content = payloadString(event, "content") ?? event.message;
      if (content) {
        items.push({
          id: event.event_id,
          kind: "agent",
          seq: event.seq,
          taskId,
          runId,
          createdAt: event.created_at,
          content,
        });
      }
      continue;
    }

    if (event.type === "agent.plan_updated") {
      items.push({
        id: event.event_id,
        kind: "plan",
        seq: event.seq,
        taskId,
        runId,
        createdAt: event.created_at,
        summary: payloadString(event, "summary") ?? event.message ?? undefined,
        steps: planSteps(event.payload.plan),
      });
      continue;
    }

    if (event.type === "agent.tool_called") {
      const toolName = payloadString(event, "tool_name") ?? "tool";
      const turnIndex = payloadNumber(event, "turn_index");
      const item: ToolTranscriptItem = {
        id: event.event_id,
        kind: "tool",
        seq: event.seq,
        taskId,
        runId,
        createdAt: event.created_at,
        toolName,
        title: toolTitle(toolName),
        turnIndex,
        status: "running",
        rationale: payloadString(event, "rationale_summary") ?? event.message ?? undefined,
        artifactIds: payloadStringArray(event.payload.input_artifact_ids),
        failureIds: [],
        parameterChips: toolParameterPreview(toolName, event.payload.arguments),
        argumentsPayload: event.payload.arguments,
      };
      items.push(item);
      const key = toolKey(toolName, turnIndex);
      pendingTools.set(key, [...(pendingTools.get(key) ?? []), item]);
      continue;
    }

    if (event.type === "agent.tool_result") {
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
        if (!matched.summary || matched.summary === matched.rationale) {
          matched.summary = payloadString(event, "summary") ?? event.message ?? matched.summary;
        }
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
          taskId,
          runId,
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
          parameterChips: [],
          details: event.payload.details,
        });
      }
      continue;
    }

    if (event.type === "agent.completed") {
      items.push({
        id: event.event_id,
        kind: "agent",
        seq: event.seq,
        taskId,
        runId,
        createdAt: event.created_at,
        label: payloadString(event, "final_task_status") ?? "completed",
        content: payloadString(event, "summary") ?? event.message ?? "Main Agent completed.",
        finalReportArtifactId: payloadString(event, "final_report_artifact_id"),
        tokenUsage: tokenUsageFromPayload(event.payload.token_usage),
      });
      continue;
    }

    if (
      event.type === "task.waiting_user" ||
      event.type === "task.succeeded" ||
      event.type === "task.partial_failed" ||
      event.type === "task.failed" ||
      event.type === "task.cancelled" ||
      event.type === "agent.stop_blocked" ||
      event.type === "agent.clarification_requested"
    ) {
      items.push({
        id: event.event_id,
        kind: "status",
        seq: event.seq,
        taskId,
        runId,
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

function latestFinalReportArtifactId(events: RouterEvent[]): string | undefined {
  const completedEvent = [...events]
    .reverse()
    .find((event) => event.type === "agent.completed");
  return completedEvent
    ? payloadString(completedEvent, "final_report_artifact_id")
    : undefined;
}

function latestCompletedTokenUsage(events: RouterEvent[]): TokenUsage | undefined {
  const completedEvent = [...events]
    .reverse()
    .find((event) => event.type === "agent.completed");
  return completedEvent
    ? tokenUsageFromPayload(completedEvent.payload.token_usage)
    : undefined;
}

function isTerminalRun(
  task: TaskState | null,
  events: RouterEvent[],
  runId: string,
): boolean {
  if (events.some((event) => isTerminalEventType(event.type))) {
    return true;
  }
  if (task?.task_id === runId) {
    return (
      task.status === "succeeded" ||
      task.status === "partial_failed" ||
      task.status === "failed" ||
      task.status === "cancelled"
    );
  }
  return false;
}

function isTerminalEventType(type: string): boolean {
  return (
    type === "task.succeeded" ||
    type === "task.partial_failed" ||
    type === "task.failed" ||
    type === "task.cancelled"
  );
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

function toolParameterPreview(toolName: string, value: unknown): string[] {
  if (!value || typeof value !== "object") {
    return [];
  }
  const record = value as Record<string, unknown>;
  const chips: string[] = [];
  const addString = (key: string, label = key, max = 72) => {
    const value = stringValue(record[key]);
    if (value) {
      chips.push(`${label}: ${truncateMiddle(value, max)}`);
    }
  };

  if (toolName === "read_file" || toolName === "write_file" || toolName === "list_files") {
    addString("path");
  } else if (toolName === "exec_command") {
    addString("command", "cmd", 90);
  } else if (toolName === "apply_patch") {
    addString("cwd");
  } else if (toolName === "call_mcp_tool") {
    addString("tool_name", "tool");
  } else if (toolName === "read_artifact") {
    addString("artifact_id", "artifact");
  } else if (toolName === "write_artifact") {
    addString("name");
  } else if (toolName === "git_status") {
    addString("cwd");
  }

  return chips;
}

function payloadString(event: RouterEvent, key: string): string | undefined {
  return stringValue(event.payload[key]);
}

function payloadNumber(event: RouterEvent, key: string): number | undefined {
  const value = event.payload[key];
  return typeof value === "number" ? value : undefined;
}

function tokenUsageFromPayload(value: unknown): TokenUsage | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const inputTokens = tokenCount(record.input_tokens);
  const outputTokens = tokenCount(record.output_tokens);
  const totalTokens = tokenCount(record.total_tokens);
  if (
    inputTokens === undefined &&
    outputTokens === undefined &&
    totalTokens === undefined
  ) {
    return undefined;
  }
  return {
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    total_tokens:
      totalTokens ?? sumTokenParts(inputTokens, outputTokens),
  };
}

function formatTokenUsage(usage: TokenUsage | undefined): string | undefined {
  if (!usage) {
    return undefined;
  }
  const inputTokens = tokenCount(usage.input_tokens);
  const outputTokens = tokenCount(usage.output_tokens);
  const totalTokens =
    tokenCount(usage.total_tokens) ?? sumTokenParts(inputTokens, outputTokens);
  if (
    inputTokens === undefined &&
    outputTokens === undefined &&
    totalTokens === undefined
  ) {
    return undefined;
  }
  return [
    `input_tokens ${formatTokenCount(inputTokens)}`,
    `output_tokens ${formatTokenCount(outputTokens)}`,
    `total_tokens ${formatTokenCount(totalTokens)}`,
  ].join(" · ");
}

function formatTokenCount(value: number | undefined): string {
  return value === undefined
    ? "-"
    : new Intl.NumberFormat("en-US").format(value);
}

function tokenCount(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function sumTokenParts(
  inputTokens: number | undefined,
  outputTokens: number | undefined,
): number | undefined {
  const parts = [inputTokens, outputTokens].filter(
    (value): value is number => value !== undefined,
  );
  return parts.length ? parts.reduce((sum, value) => sum + value, 0) : undefined;
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
    list_files: "列出文件",
    read_file: "读取文件",
    write_file: "写入文件",
    apply_patch: "应用补丁",
    exec_command: "执行命令",
    git_status: "Git 状态",
    write_artifact: "写入 Artifact",
    call_mcp_tool: "调用 MCP 工具",
    call_plc_dev: "调用 plc-dev",
    call_plc_test: "调用 plc-test",
    call_plc_formal: "调用 plc-formal",
    call_plc_repair: "调用 plc-repair",
    read_artifact: "读取 Artifact",
  };
  return labels[toolName] ?? toolName;
}

function toolIcon(toolName: string, status: ToolTranscriptItem["status"]) {
  if (status === "running") {
    return <Loader2 size={15} />;
  }
  if (toolName.startsWith("call_plc") || toolName === "call_mcp_tool") {
    return <Wrench size={15} />;
  }
  if (toolName === "git_status") {
    return <CheckCircle2 size={15} />;
  }
  if (
    toolName === "write_artifact" ||
    toolName === "read_artifact" ||
    toolName === "read_file" ||
    toolName === "write_file" ||
    toolName === "apply_patch"
  ) {
    return <FileText size={15} />;
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

function isTerminalTask(task: TaskState | null): boolean {
  return Boolean(
    task &&
      (task.status === "succeeded" ||
        task.status === "partial_failed" ||
        task.status === "failed" ||
        task.status === "cancelled"),
  );
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

function formatElapsed(start?: string, end?: string): string {
  if (!start || !end) {
    return "等待任务";
  }
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) {
    return "已处理";
  }
  const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 1) {
    return `已处理 ${remainingSeconds}s`;
  }
  return `已处理 ${minutes}m ${remainingSeconds}s`;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function truncateMiddle(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const head = Math.max(8, Math.floor((maxLength - 3) * 0.58));
  const tail = Math.max(6, maxLength - 3 - head);
  return `${value.slice(0, head)}...${value.slice(-tail)}`;
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

function processPanelOpen(
  run: TranscriptRunView,
  openByRun: Record<string, boolean>,
  touchedByRun: Record<string, boolean>,
): boolean {
  if (touchedByRun[run.id]) {
    return openByRun[run.id] ?? false;
  }
  return !run.isTerminal;
}

function pruneRunState(
  current: Record<string, boolean>,
  activeRunIds: Set<string>,
): Record<string, boolean> {
  let changed = false;
  const next: Record<string, boolean> = {};
  for (const [runId, value] of Object.entries(current)) {
    if (activeRunIds.has(runId)) {
      next[runId] = value;
    } else {
      changed = true;
    }
  }
  return changed ? next : current;
}
