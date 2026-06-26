import { useCallback, useEffect, useMemo, useState } from "react";

import { getHealth, RouterApiError } from "../../../api/router/client";
import { cancelTask, deleteTask, getTask, listTasks } from "../../../api/router/tasks";
import {
  appendSessionMessage,
  createSession,
  deleteSession,
  getSession,
  listSessions,
} from "../../../api/router/sessions";
import type {
  AgentSession,
  HealthResponse,
  ProjectContext,
  TaskState,
  TaskStatus,
} from "../../../api/router/types";

const TERMINAL_STATUSES: TaskStatus[] = [
  "succeeded",
  "partial_failed",
  "failed",
  "cancelled",
];
const ACTIVE_TASK_STORAGE_KEY = "router-agent.active-task-id";
const ACTIVE_SESSION_STORAGE_KEY = "router-agent.active-session-id";

export interface TaskSummaryItem {
  taskId: string;
  sessionId?: string;
  title: string;
  status: string;
  phase: string;
  updatedAt: string;
}

export interface BackendHealthState {
  status: "checking" | "ok" | "error";
  detail?: string;
  payload?: HealthResponse;
}

export interface TaskMutationState {
  loading: boolean;
  error?: string;
}

export function useTaskState() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [agentSession, setAgentSession] = useState<AgentSession | null>(null);
  const [task, setTask] = useState<TaskState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [mutation, setMutation] = useState<TaskMutationState>({ loading: false });
  const [health, setHealth] = useState<BackendHealthState>({ status: "checking" });
  const [recentTasks, setRecentTasks] = useState<TaskSummaryItem[]>([]);

  const refreshHealth = useCallback(async () => {
    setHealth({ status: "checking" });
    try {
      const payload = await getHealth();
      setHealth({ status: "ok", payload });
    } catch (err) {
      setHealth({ status: "error", detail: readableError(err) });
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
  }, [refreshHealth]);

  const applyTask = useCallback(
    (next: TaskState, nextSession?: AgentSession | null) => {
      setTask(next);
      setTaskId(next.task_id);
      persistActiveTaskId(next.task_id);

      const resolvedSessionId = nextSession?.session_id ?? next.session_id ?? null;
      if (resolvedSessionId) {
        setSessionId(resolvedSessionId);
        persistActiveSessionId(resolvedSessionId);
      }
      if (nextSession !== undefined) {
        setAgentSession(nextSession);
      }

      setRecentTasks((current) =>
        upsertTaskSummary(current, next, nextSession),
      );
    },
    [],
  );

  const refreshTask = useCallback(
    async (overrideTaskId = taskId) => {
      if (!overrideTaskId) {
        return null;
      }
      setLoading(true);
      setError(undefined);
      try {
        const next = await getTask(overrideTaskId);
        let nextSession: AgentSession | null | undefined;
        if (next.session_id) {
          try {
            const sessionPayload = await getSession(next.session_id);
            nextSession = sessionPayload.session;
          } catch {
            nextSession = null;
          }
        }
        applyTask(next, nextSession);
        return next;
      } catch (err) {
        setError(readableError(err));
        if (err instanceof RouterApiError && err.status === 404) {
          setTaskId((current) => (current === overrideTaskId ? null : current));
          setTask((current) =>
            current?.task_id === overrideTaskId ? null : current,
          );
          clearActiveTaskId();
        }
        return null;
      } finally {
        setLoading(false);
      }
    },
    [applyTask, taskId],
  );

  const refreshRecentTasks = useCallback(async () => {
    try {
      const sessionsPayload = await listSessions();
      const sessionItems = sessionsPayload.sessions.map(sessionSummary);
      setRecentTasks(sessionItems);
      return sessionItems;
    } catch (sessionErr) {
      try {
        const payload = await listTasks();
        const taskItems = payload.tasks.map((item) => taskSummary(item));
        setRecentTasks(taskItems);
        return taskItems;
      } catch {
        setError(readableError(sessionErr));
        return [];
      }
    }
  }, []);

  const createNewTask = useCallback(
    async (message: string, projectContext: ProjectContext) => {
      setMutation({ loading: true });
      setError(undefined);
      try {
        const created = await createSession(message, projectContext);
        setSessionId(created.session.session_id);
        setAgentSession(created.session);
        persistActiveSessionId(created.session.session_id);
        applyTask(created.task, created.session);
        setMutation({ loading: false });
        return created;
      } catch (err) {
        setMutation({ loading: false, error: readableError(err) });
        throw err;
      }
    },
    [applyTask],
  );

  const startBlankTask = useCallback(() => {
    setTaskId(null);
    setSessionId(null);
    setAgentSession(null);
    setTask(null);
    setLoading(false);
    setError(undefined);
    setMutation({ loading: false });
    clearActiveTaskId();
    clearActiveSessionId();
  }, []);

  const appendMessage = useCallback(
    async (message: string) => {
      if (!taskId && !sessionId) {
        return null;
      }
      setMutation({ loading: true });
      setError(undefined);
      try {
        if (sessionId) {
          const result = await appendSessionMessage(sessionId, message);
          setSessionId(result.session.session_id);
          setAgentSession(result.session);
          persistActiveSessionId(result.session.session_id);
          applyTask(result.task, result.session);
          setMutation({ loading: false });
          return result;
        }
        const result = await createSession(message, task?.project_context ?? {});
        setSessionId(result.session.session_id);
        setAgentSession(result.session);
        persistActiveSessionId(result.session.session_id);
        applyTask(result.task, result.session);
        setMutation({ loading: false });
        return result;
      } catch (err) {
        setMutation({ loading: false, error: readableError(err) });
        throw err;
      }
    },
    [applyTask, sessionId, task?.project_context, taskId],
  );

  const cancelCurrentTask = useCallback(async () => {
    if (!taskId) {
      return null;
    }
    setMutation({ loading: true });
    setError(undefined);
    try {
      const result = await cancelTask(taskId);
      applyTask(result);
      setMutation({ loading: false });
      return result;
    } catch (err) {
      setMutation({ loading: false, error: readableError(err) });
      throw err;
    }
  }, [applyTask, taskId]);

  const deleteTaskById = useCallback(
    async (targetTaskId: string) => {
      const target = recentTasks.find((item) => item.taskId === targetTaskId);
      const deletingCurrentTask =
        taskId === targetTaskId ||
        Boolean(target?.sessionId && target.sessionId === sessionId);
      const previousTask = task;
      const previousSession = agentSession;

      setMutation({ loading: true });
      setError(undefined);
      if (deletingCurrentTask) {
        setTaskId(null);
        setSessionId(null);
        setAgentSession(null);
        setTask(null);
        clearActiveTaskId();
        clearActiveSessionId();
      }
      try {
        if (target?.sessionId) {
          await deleteSession(target.sessionId);
          setRecentTasks((current) =>
            current.filter((item) => item.sessionId !== target.sessionId),
          );
        } else {
          await deleteTask(targetTaskId);
          setRecentTasks((current) =>
            current.filter((item) => item.taskId !== targetTaskId),
          );
        }
        setMutation({ loading: false });
      } catch (err) {
        if (deletingCurrentTask && previousTask) {
          setTaskId(previousTask.task_id);
          setTask(previousTask);
          persistActiveTaskId(previousTask.task_id);
          setSessionId(previousSession?.session_id ?? previousTask.session_id);
          setAgentSession(previousSession);
          if (previousSession?.session_id ?? previousTask.session_id) {
            persistActiveSessionId(
              previousSession?.session_id ?? previousTask.session_id,
            );
          }
        }
        setMutation({ loading: false, error: readableError(err) });
        throw err;
      }
    },
    [agentSession, recentTasks, sessionId, task, taskId],
  );

  const terminal = task ? TERMINAL_STATUSES.includes(task.status) : false;
  const canAppendMessage = Boolean(
    agentSession?.status === "active" || (task && !terminal),
  );
  const canCancel =
    task?.status === "created" ||
    task?.status === "running" ||
    task?.status === "waiting_user";

  useEffect(() => {
    let cancelled = false;

    const restoreTask = async () => {
      setLoading(true);
      setError(undefined);
      try {
        const savedSessionId = readActiveSessionId();
        if (savedSessionId) {
          try {
            const restoredSession = await getSession(savedSessionId);
            if (cancelled) {
              return;
            }
            setSessionId(restoredSession.session.session_id);
            setAgentSession(restoredSession.session);
            persistActiveSessionId(restoredSession.session.session_id);
            setRecentTasks((current) =>
              upsertSessionSummary(current, restoredSession.session),
            );
            if (restoredSession.latest_task) {
              applyTask(restoredSession.latest_task, restoredSession.session);
            }
            return;
          } catch (err) {
            if (!(err instanceof RouterApiError && err.status === 404)) {
              throw err;
            }
            clearActiveSessionId();
          }
        }

        const savedTaskId = readActiveTaskId();
        if (savedTaskId) {
          try {
            const restored = await getTask(savedTaskId);
            if (cancelled) {
              return;
            }
            let restoredSession: AgentSession | null | undefined;
            if (restored.session_id) {
              try {
                restoredSession = (await getSession(restored.session_id)).session;
              } catch {
                restoredSession = null;
              }
            }
            applyTask(restored, restoredSession);
            return;
          } catch (err) {
            if (!(err instanceof RouterApiError && err.status === 404)) {
              throw err;
            }
            clearActiveTaskId();
          }
        }

        const sessionsPayload = await listSessions();
        if (cancelled) {
          return;
        }
        setRecentTasks(sessionsPayload.sessions.map(sessionSummary));
        const latestSession = sessionsPayload.sessions[0];
        if (latestSession?.latest_task_id) {
          const latestTask = await getTask(latestSession.latest_task_id);
          if (cancelled) {
            return;
          }
          setSessionId(latestSession.session_id);
          setAgentSession(latestSession);
          persistActiveSessionId(latestSession.session_id);
          applyTask(latestTask, latestSession);
        }
      } catch (err) {
        try {
          const fallback = await listTasks();
          if (cancelled) {
            return;
          }
          setRecentTasks(fallback.tasks.map((item) => taskSummary(item)));
          const latest = fallback.tasks[0];
          if (latest) {
            applyTask(latest);
          }
        } catch {
          if (!cancelled) {
            setError(readableError(err));
            clearActiveTaskId();
            clearActiveSessionId();
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void restoreTask();
    return () => {
      cancelled = true;
    };
  }, [applyTask]);

  return useMemo(
    () => ({
      taskId,
      sessionId,
      agentSession,
      task,
      loading,
      error,
      health,
      mutation,
      recentTasks,
      terminal,
      canAppendMessage,
      canCancel,
      setTaskId,
      setSessionId,
      refreshTask,
      refreshHealth,
      refreshRecentTasks,
      createNewTask,
      startBlankTask,
      appendMessage,
      cancelCurrentTask,
      deleteTaskById,
    }),
    [
      taskId,
      sessionId,
      agentSession,
      task,
      loading,
      error,
      health,
      mutation,
      recentTasks,
      terminal,
      canAppendMessage,
      canCancel,
      refreshTask,
      refreshHealth,
      refreshRecentTasks,
      createNewTask,
      startBlankTask,
      appendMessage,
      cancelCurrentTask,
      deleteTaskById,
    ],
  );
}

function sessionSummary(session: AgentSession): TaskSummaryItem {
  const latestRun = session.runs.at(-1);
  return {
    taskId: session.latest_task_id ?? latestRun?.task_id ?? session.session_id,
    sessionId: session.session_id,
    title:
      session.title ??
      firstLine(latestRun?.user_message ?? session.summary ?? "Untitled session"),
    status: latestRun?.status ?? session.status,
    phase: session.status,
    updatedAt: session.updated_at,
  };
}

function taskSummary(task: TaskState, session?: AgentSession | null): TaskSummaryItem {
  const sessionItem = session ? sessionSummary(session) : null;
  return {
    taskId: task.task_id,
    sessionId: session?.session_id ?? task.session_id ?? undefined,
    title:
      sessionItem?.title ??
      task.title ??
      firstLine(task.normalized_goal ?? task.raw_user_request),
    status: task.status,
    phase: task.phase,
    updatedAt: task.updated_at,
  };
}

function upsertTaskSummary(
  current: TaskSummaryItem[],
  task: TaskState,
  session?: AgentSession | null,
): TaskSummaryItem[] {
  const item = taskSummary(task, session);
  const sameConversation = (existing: TaskSummaryItem) =>
    item.sessionId
      ? existing.sessionId === item.sessionId
      : existing.taskId === item.taskId;
  return [item, ...current.filter((existing) => !sameConversation(existing))].slice(
    0,
    30,
  );
}

function upsertSessionSummary(
  current: TaskSummaryItem[],
  session: AgentSession,
): TaskSummaryItem[] {
  const item = sessionSummary(session);
  return [
    item,
    ...current.filter((existing) => existing.sessionId !== session.session_id),
  ].slice(0, 30);
}

function firstLine(value: string): string {
  return value.split(/\r?\n/)[0]?.trim() || "Untitled task";
}

function readActiveTaskId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistActiveTaskId(taskId: string): void {
  try {
    window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, taskId);
  } catch {
    // Ignore storage failures; backend state is still authoritative.
  }
}

function clearActiveTaskId(): void {
  try {
    window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
  } catch {
    // Ignore storage failures; backend state is still authoritative.
  }
}

function readActiveSessionId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistActiveSessionId(sessionId: string | null | undefined): void {
  if (!sessionId) {
    return;
  }
  try {
    window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Ignore storage failures; backend state is still authoritative.
  }
}

function clearActiveSessionId(): void {
  try {
    window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
  } catch {
    // Ignore storage failures; backend state is still authoritative.
  }
}

export function readableError(error: unknown): string {
  if (error instanceof RouterApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error";
}
