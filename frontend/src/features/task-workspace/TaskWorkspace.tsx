import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Bot, Boxes } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getTaskTrace } from "../../api/router/trace";
import type { TaskTraceSummary } from "../../api/router/types";
import { AgentCards } from "./AgentCards";
import { ArtifactPanel } from "./ArtifactPanel";
import { ChatPanel, type RailView } from "./ChatPanel";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { SseConsole } from "./SseConsole";
import { TraceView } from "./TraceView";
import { useTaskArtifacts } from "./hooks/useTaskArtifacts";
import { useTaskEvents } from "./hooks/useTaskEvents";
import { useSubagentStatus } from "./hooks/useSubagentStatus";
import { readableError, useTaskState } from "./hooks/useTaskState";
import {
  buildWorkerCards,
  findFinalReportArtifact,
  shouldRefreshArtifacts,
  shouldRefreshTask,
  shouldRefreshTrace,
} from "./selectors";

type DockTab = "overview" | "artifacts" | "trace";

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
  const artifactState = useTaskArtifacts(taskState.taskId);
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
      void artifactState.refreshArtifacts();
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
    if (pendingEvents.some(shouldRefreshArtifacts)) {
      void artifactState.refreshArtifacts();
    }
    if (pendingEvents.some(shouldRefreshTrace)) {
      void loadTrace();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventState.events]);

  const finalReportArtifact = findFinalReportArtifact(
    taskState.task,
    artifactState.artifacts,
    eventState.events,
  );
  const finalReportArtifactId = finalReportArtifact?.artifact_id;

  useEffect(() => {
    if (
      finalReportArtifactId &&
      artifactState.contentById[finalReportArtifactId] === undefined
    ) {
      void artifactState.loadContent(finalReportArtifactId).catch(() => undefined);
    }
  }, [finalReportArtifactId, artifactState]);

  const workerCards = useMemo(
    () => buildWorkerCards(taskState.task, eventState.events, trace),
    [taskState.task, eventState.events, trace],
  );
  const subagentOnlineCount =
    subagentStatus.payload?.workers.filter((worker) => worker.online === true)
      .length ?? 0;
  const dockHeader = dockHeaderForTab({
    activeDockTab,
    artifactCount: artifactState.artifacts.length,
    eventCount: eventState.events.length,
    phase: taskState.task?.phase ?? "idle",
    subagentOnlineCount,
  });

  const refreshWorkspace = () => {
    void taskState.refreshTask();
    void artifactState.refreshArtifacts();
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
            onArtifactClick={(artifactId) => {
              void artifactState.loadContent(artifactId).catch(() => undefined);
              setActiveDockTab("artifacts");
            }}
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
                active={activeDockTab === "artifacts"}
                icon={Boxes}
                label="Artifacts"
                count={artifactState.artifacts.length}
                onClick={() => setActiveDockTab("artifacts")}
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
              {activeDockTab === "artifacts" ? (
                <ArtifactPanel
                  artifacts={artifactState.artifacts}
                  loading={artifactState.loading}
                  error={artifactState.error}
                  selectedArtifactId={artifactState.selectedArtifactId}
                  selectedContent={artifactState.selectedContent}
                  onSelect={(artifactId) => {
                    void artifactState.loadContent(artifactId).catch(() => undefined);
                  }}
                />
              ) : null}
              {activeDockTab === "trace" ? (
                <>
                  <ExecutionTimeline
                    events={eventState.events}
                    onArtifactClick={(artifactId) => {
                      void artifactState.loadContent(artifactId).catch(() => undefined);
                      setActiveDockTab("artifacts");
                    }}
                  />
                  <TraceView
                    trace={trace}
                    loading={traceLoading}
                    error={traceError}
                    onRefresh={() => void loadTrace()}
                    onArtifactClick={(artifactId) => {
                      void artifactState.loadContent(artifactId).catch(() => undefined);
                      setActiveDockTab("artifacts");
                    }}
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
  artifactCount,
  eventCount,
  phase,
  subagentOnlineCount,
}: {
  activeDockTab: DockTab;
  artifactCount: number;
  eventCount: number;
  phase: string;
  subagentOnlineCount: number;
}): {
  title: string;
  subtitle: string;
  badge: string;
} {
  if (activeDockTab === "artifacts") {
    return {
      title: "Artifacts",
      subtitle: artifactCount ? "Generated task files" : "No generated files yet",
      badge: `${artifactCount} artifacts`,
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
