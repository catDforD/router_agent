import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Bot, FolderTree } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getTaskTrace } from "../../api/router/trace";
import type { TaskState, TaskTraceSummary } from "../../api/router/types";
import { AgentCards } from "./AgentCards";
import { ChatPanel, type RailView } from "./ChatPanel";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { SseConsole } from "./SseConsole";
import { TraceView } from "./TraceView";
import { WorkspacePanel } from "./WorkspacePanel";
import { useTaskEvents } from "./hooks/useTaskEvents";
import { useSubagentStatus } from "./hooks/useSubagentStatus";
import { readableError, useTaskState } from "./hooks/useTaskState";
import {
  buildWorkerCards,
  shouldRefreshWorkspace,
  shouldRefreshTask,
  shouldRefreshTrace,
} from "./selectors";

type DockTab = "overview" | "workspace" | "trace";

export function TaskWorkspace() {
  const taskState = useTaskState();
  const [eventReconnectSignal, setEventReconnectSignal] = useState(0);
  const eventStreamId = taskState.sessionId ?? taskState.taskId;
  const eventState = useTaskEvents(
    eventStreamId,
    Boolean(eventStreamId),
    taskState.sessionId ? "session" : "task",
    eventReconnectSignal,
  );
  const subagentStatus = useSubagentStatus();
  const [trace, setTrace] = useState<TaskTraceSummary | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | undefined>();
  const [activeDockTab, setActiveDockTab] = useState<DockTab>("overview");
  const [activeRailView, setActiveRailView] = useState<RailView>("playground");
  const [draftMessage, setDraftMessage] = useState("");
  const [composerFocusSignal, setComposerFocusSignal] = useState(0);
  const [composerResetSignal, setComposerResetSignal] = useState(0);
  const processedSeqRef = useRef(0);

  const loadTrace = async () => {
    if (!taskState.taskId) {
      return null;
    }
    setTraceLoading(true);
    setTraceError(undefined);
    try {
      const payload = await getTaskTrace(taskState.taskId);
      setTrace(payload);
      return payload;
    } catch (err) {
      setTraceError(readableError(err));
      return null;
    } finally {
      setTraceLoading(false);
    }
  };

  useEffect(() => {
    if (taskState.taskId) {
      void loadTrace();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskState.taskId]);

  useEffect(() => {
    processedSeqRef.current = 0;
  }, [eventStreamId]);

  useEffect(() => {
    const pendingEvents = eventState.events
      .filter((event) => event.seq > processedSeqRef.current)
      .sort((left, right) => left.seq - right.seq);
    if (!pendingEvents.length) {
      return;
    }

    processedSeqRef.current = Math.max(
      processedSeqRef.current,
      ...pendingEvents.map((event) => event.seq),
    );
    if (pendingEvents.some(shouldRefreshTask)) {
      void taskState.refreshTask();
    }
    if (pendingEvents.some(shouldRefreshWorkspace) || pendingEvents.some(shouldRefreshTrace)) {
      void loadTrace();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventState.events]);

  const workerCards = useMemo(
    () => buildWorkerCards(taskState.task, eventState.events, trace),
    [taskState.task, eventState.events, trace],
  );
  const subagentOnlineCount =
    subagentStatus.payload?.workers.filter((worker) => worker.online === true)
      .length ?? 0;
  const dockHeader = dockHeaderForTab({
    activeDockTab,
    workspaceCount: workspacePathCount(taskState.task, trace),
    eventCount: eventState.events.length,
    phase: taskState.task?.phase ?? "idle",
    subagentOnlineCount,
  });

  const refreshWorkspace = () => {
    void taskState.refreshTask();
    void loadTrace();
    void subagentStatus.refresh();
  };

  const startNewTask = () => {
    taskState.startBlankTask();
    setTrace(null);
    setTraceError(undefined);
    processedSeqRef.current = 0;
    setActiveDockTab("overview");
    setActiveRailView("playground");
    setDraftMessage("");
    setComposerResetSignal((value) => value + 1);
    setComposerFocusSignal((value) => value + 1);
  };

  return (
    <main className="workspace">
      <section className="workspace-frame">
        <div className="workspace-grid">
          <ChatPanel
            task={taskState.task}
            taskItems={taskState.recentTasks}
            health={taskState.health}
            loading={taskState.loading || taskState.mutation.loading}
            error={taskState.error ?? taskState.mutation.error}
            canAppendMessage={taskState.canAppendMessage}
            activeView={activeRailView}
            onAppendMessage={async (message) => {
              setEventReconnectSignal((value) => value + 1);
              return taskState.appendMessage(message);
            }}
            onRefreshHealth={taskState.refreshHealth}
            onSelectTask={(taskId) => {
              const selected = taskState.recentTasks.find(
                (item) => item.taskId === taskId,
              );
              if (selected?.sessionId) {
                taskState.setSessionId(selected.sessionId);
              }
              taskState.setTaskId(taskId);
              void taskState.refreshTask(taskId);
            }}
            onNewTask={startNewTask}
            onDeleteTask={async (taskId) => {
              const selected = taskState.recentTasks.find(
                (item) => item.taskId === taskId,
              );
              const deletingCurrentTask =
                taskState.taskId === taskId ||
                Boolean(
                  selected?.sessionId &&
                    selected.sessionId === taskState.sessionId,
                );
              await taskState.deleteTaskById(taskId);
              if (deletingCurrentTask) {
                setTrace(null);
                setTraceError(undefined);
                processedSeqRef.current = 0;
                setActiveDockTab("overview");
                setDraftMessage("");
                setComposerResetSignal((value) => value + 1);
              }
            }}
            onViewChange={setActiveRailView}
            onDraftMessage={(message) => {
              setDraftMessage(message);
              setComposerFocusSignal((value) => value + 1);
            }}
            onFocusComposer={() => setComposerFocusSignal((value) => value + 1)}
          />

          <SseConsole
            task={taskState.task}
            events={eventState.events}
            streamState={eventState.streamState}
            streamError={eventState.streamError}
            latestSeq={eventState.latestSeq}
            loading={taskState.loading || taskState.mutation.loading}
            canAppendMessage={taskState.canAppendMessage}
            canCancel={taskState.canCancel}
            traceOpen={activeDockTab === "trace"}
            onCreateTask={taskState.createNewTask}
            onAppendMessage={async (message) => {
              setEventReconnectSignal((value) => value + 1);
              return taskState.appendMessage(message);
            }}
            onNewTask={startNewTask}
            onRefresh={refreshWorkspace}
            onToggleTrace={() =>
              setActiveDockTab((value) =>
                value === "trace" ? "overview" : "trace",
              )
            }
            onCancel={() => void taskState.cancelCurrentTask()}
            draftMessage={draftMessage}
            focusSignal={composerFocusSignal}
            resetSignal={composerResetSignal}
            onDraftConsumed={() => setDraftMessage("")}
          />

          <aside className="subagent-dock">
            <div className="dock-header">
              <div>
                <h2>{dockHeader.title}</h2>
                <p>{dockHeader.subtitle}</p>
              </div>
              <span className="pill">{dockHeader.badge}</span>
            </div>
            <div className="dock-tabs" role="tablist" aria-label="Run inspector">
              <DockTabButton
                active={activeDockTab === "overview"}
                icon={Bot}
                label="Subagents"
                count={subagentOnlineCount}
                onClick={() => setActiveDockTab("overview")}
              />
              <DockTabButton
                active={activeDockTab === "workspace"}
                icon={FolderTree}
                label="Workspace"
                count={workspacePathCount(taskState.task, trace)}
                onClick={() => setActiveDockTab("workspace")}
              />
              <DockTabButton
                active={activeDockTab === "trace"}
                icon={Activity}
                label="Trace"
                count={eventState.events.length}
                onClick={() => setActiveDockTab("trace")}
              />
            </div>
            <div className="dock-scroll">
              {activeDockTab === "overview" ? (
                <AgentCards
                  workers={workerCards}
                  subagentStatus={subagentStatus.payload}
                  subagentStatusError={subagentStatus.error}
                  subagentStatusLoading={subagentStatus.loading}
                />
              ) : null}
              {activeDockTab === "workspace" ? (
                <WorkspacePanel
                  task={taskState.task}
                  trace={trace}
                  loading={traceLoading}
                  error={traceError}
                  onRefresh={refreshWorkspace}
                />
              ) : null}
              {activeDockTab === "trace" ? (
                <>
                  <ExecutionTimeline
                    events={eventState.events}
                  />
                  <TraceView
                    trace={trace}
                    loading={traceLoading}
                    error={traceError}
                    onRefresh={() => void loadTrace()}
                  />
                </>
              ) : null}
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

function DockTabButton({
  active,
  icon: Icon,
  label,
  count,
  onClick,
}: {
  active: boolean;
  icon: LucideIcon;
  label: string;
  count?: number;
  onClick: () => void;
}) {
  return (
    <button
      aria-selected={active}
      className="dock-tab"
      role="tab"
      type="button"
      onClick={onClick}
    >
      <Icon size={14} />
      <span>{label}</span>
      {count !== undefined ? <small>{count}</small> : null}
    </button>
  );
}

function dockHeaderForTab({
  activeDockTab,
  workspaceCount,
  eventCount,
  phase,
  subagentOnlineCount,
}: {
  activeDockTab: DockTab;
  workspaceCount: number;
  eventCount: number;
  phase: string;
  subagentOnlineCount: number;
}): {
  title: string;
  subtitle: string;
  badge: string;
} {
  if (activeDockTab === "workspace") {
    return {
      title: "Workspace",
      subtitle: workspaceCount ? "Generated task files" : "No generated files yet",
      badge: `${workspaceCount} paths`,
    };
  }
  if (activeDockTab === "trace") {
    return {
      title: "Trace",
      subtitle: eventCount ? "Execution event stream" : "No events yet",
      badge: `${eventCount} events`,
    };
  }
  return {
    title: "Subagents",
    subtitle: phase,
    badge: `${subagentOnlineCount}/4 online`,
  };
}

function workspacePathCount(
  task: TaskState | null,
  trace: TaskTraceSummary | null,
): number {
  const paths = new Set<string>();
  for (const path of task?.current_files.all_paths ?? []) {
    addWorkspacePath(paths, path);
  }
  for (const file of trace?.files ?? []) {
    addWorkspacePath(paths, file.path);
  }
  for (const job of trace?.worker_jobs ?? []) {
    job.input_paths.forEach((path) => addWorkspacePath(paths, path));
    job.read_paths.forEach((path) => addWorkspacePath(paths, path));
    job.written_paths.forEach((path) => addWorkspacePath(paths, path));
    job.report_paths.forEach((path) => addWorkspacePath(paths, path));
  }
  return paths.size;
}

function addWorkspacePath(paths: Set<string>, path: string): void {
  if (!isSystemWorkspacePath(path)) {
    paths.add(path);
  }
}

function isSystemWorkspacePath(path: string): boolean {
  return (
    (path === ".router" || path.startsWith(".router/")) &&
    !path.startsWith(".router/reports/")
  );
}
