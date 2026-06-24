import { useEffect, useMemo, useRef, useState } from "react";

import { getTaskTrace } from "../../api/router/trace";
import type { TaskState, TaskTraceSummary } from "../../api/router/types";
import { AgentCards } from "./AgentCards";
import { ArtifactPanel } from "./ArtifactPanel";
import { ChatPanel, type TaskListItem } from "./ChatPanel";
import { FinalReportView } from "./FinalReportView";
import { SseConsole } from "./SseConsole";
import { TraceView } from "./TraceView";
import { useTaskArtifacts } from "./hooks/useTaskArtifacts";
import { useTaskEvents } from "./hooks/useTaskEvents";
import { readableError, useTaskState } from "./hooks/useTaskState";
import {
  buildGateSummaries,
  buildWorkerCards,
  findFinalReportArtifact,
  getRepairSummary,
  shouldRefreshArtifacts,
  shouldRefreshTask,
  shouldRefreshTrace,
} from "./selectors";

export function TaskWorkspace() {
  const taskState = useTaskState();
  const eventStreamId = taskState.sessionId ?? taskState.taskId;
  const eventState = useTaskEvents(
    eventStreamId,
    Boolean(eventStreamId),
    taskState.sessionId ? "session" : "task",
  );
  const artifactState = useTaskArtifacts(taskState.taskId);
  const [trace, setTrace] = useState<TaskTraceSummary | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | undefined>();
  const [debugOpen, setDebugOpen] = useState(false);
  const [taskItems, setTaskItems] = useState<TaskListItem[]>([]);
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

  useEffect(() => {
    const task = taskState.task;
    if (!task) {
      return;
    }
    setTaskItems((current) =>
      upsertTaskItem(current, task, taskState.sessionId ?? undefined),
    );
  }, [taskState.task, taskState.sessionId]);

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
  const gateSummaries = useMemo(
    () => buildGateSummaries(taskState.task?.gates, trace),
    [taskState.task?.gates, trace],
  );
  const repairSummary = useMemo(
    () => getRepairSummary(taskState.task),
    [taskState.task],
  );
  const finalReportContent =
    finalReportArtifactId !== undefined
      ? artifactState.contentById[finalReportArtifactId]?.response
      : undefined;

  const refreshWorkspace = () => {
    void taskState.refreshTask();
    void artifactState.refreshArtifacts();
    void loadTrace();
  };

  return (
    <main className="workspace">
      <section className="workspace-frame">
        <div className="workspace-grid">
          <ChatPanel
            task={taskState.task}
            taskItems={taskItems}
            health={taskState.health}
            loading={taskState.loading || taskState.mutation.loading}
            error={taskState.error ?? taskState.mutation.error}
            canAppendMessage={taskState.canAppendMessage}
            onCreateTask={taskState.createNewTask}
            onAppendMessage={taskState.appendMessage}
            onRefreshHealth={taskState.refreshHealth}
            onSelectTask={(taskId) => {
              const selected = taskItems.find((item) => item.taskId === taskId);
              if (selected?.sessionId) {
                taskState.setSessionId(selected.sessionId);
              }
              taskState.setTaskId(taskId);
              void taskState.refreshTask(taskId);
            }}
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
            traceOpen={debugOpen}
            onCreateTask={taskState.createNewTask}
            onAppendMessage={taskState.appendMessage}
            onRefresh={refreshWorkspace}
            onToggleTrace={() => setDebugOpen((value) => !value)}
            onCancel={() => void taskState.cancelCurrentTask()}
            onArtifactClick={(artifactId) => {
              void artifactState.loadContent(artifactId).catch(() => undefined);
            }}
          />

          <aside className="subagent-dock">
            <div className="dock-header">
              <div>
                <h2>Subagents</h2>
                <p>{taskState.task?.phase ?? "idle"}</p>
              </div>
              <span className="pill">{artifactState.artifacts.length} artifacts</span>
            </div>
            <div className="dock-scroll">
              <AgentCards
                workers={workerCards}
                gates={gateSummaries}
                repair={repairSummary}
              />
              <FinalReportView
                finalReport={finalReportContent}
                loading={
                  finalReportArtifactId !== undefined &&
                  artifactState.contentById[finalReportArtifactId]?.loading === true
                }
              />
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
              {debugOpen ? (
                <TraceView
                  trace={trace}
                  loading={traceLoading}
                  error={traceError}
                  onRefresh={() => void loadTrace()}
                  onArtifactClick={(artifactId) => {
                    void artifactState.loadContent(artifactId).catch(() => undefined);
                  }}
                />
              ) : null}
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

function upsertTaskItem(
  current: TaskListItem[],
  task: TaskState,
  sessionId?: string,
): TaskListItem[] {
  const item: TaskListItem = {
    taskId: task.task_id,
    sessionId,
    title: task.title ?? firstLine(task.normalized_goal ?? task.raw_user_request),
    status: task.status,
    phase: task.phase,
    updatedAt: task.updated_at,
  };
  const sameConversation = (existing: TaskListItem) =>
    sessionId ? existing.sessionId === sessionId : existing.taskId === task.task_id;
  return [
    item,
    ...current.filter((existing) => !sameConversation(existing)),
  ].slice(0, 8);
}

function firstLine(value: string): string {
  return value.split(/\r?\n/)[0]?.trim() || "Untitled task";
}
