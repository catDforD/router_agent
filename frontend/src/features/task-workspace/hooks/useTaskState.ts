import { useCallback, useEffect, useMemo, useState } from "react";

import { getHealth, RouterApiError } from "../../../api/router/client";
import {
  appendUserMessage,
  cancelTask,
  createTask,
  getTask,
} from "../../../api/router/tasks";
import type {
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
  const [task, setTask] = useState<TaskState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [mutation, setMutation] = useState<TaskMutationState>({ loading: false });
  const [health, setHealth] = useState<BackendHealthState>({ status: "checking" });

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

  const refreshTask = useCallback(
    async (overrideTaskId = taskId) => {
      if (!overrideTaskId) {
        return null;
      }
      setLoading(true);
      setError(undefined);
      try {
        const next = await getTask(overrideTaskId);
        setTask(next);
        setTaskId(next.task_id);
        return next;
      } catch (err) {
        setError(readableError(err));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [taskId],
  );

  const createNewTask = useCallback(
    async (message: string, projectContext: ProjectContext) => {
      setMutation({ loading: true });
      setError(undefined);
      try {
        const created = await createTask(message, projectContext);
        setTaskId(created.task_id);
        await refreshTask(created.task_id);
        setMutation({ loading: false });
        return created;
      } catch (err) {
        setMutation({ loading: false, error: readableError(err) });
        throw err;
      }
    },
    [refreshTask],
  );

  const appendMessage = useCallback(
    async (message: string) => {
      if (!taskId) {
        return null;
      }
      setMutation({ loading: true });
      try {
        const result = await appendUserMessage(taskId, message);
        setTask(result.task);
        setMutation({ loading: false });
        return result;
      } catch (err) {
        setMutation({ loading: false, error: readableError(err) });
        throw err;
      }
    },
    [taskId],
  );

  const cancelCurrentTask = useCallback(async () => {
    if (!taskId) {
      return null;
    }
    setMutation({ loading: true });
    try {
      const result = await cancelTask(taskId);
      setTask(result);
      setMutation({ loading: false });
      return result;
    } catch (err) {
      setMutation({ loading: false, error: readableError(err) });
      throw err;
    }
  }, [taskId]);

  const terminal = task ? TERMINAL_STATUSES.includes(task.status) : false;
  const canAppendMessage = Boolean(task && !terminal);
  const canCancel =
    task?.status === "created" ||
    task?.status === "running" ||
    task?.status === "waiting_user";

  return useMemo(
    () => ({
      taskId,
      task,
      loading,
      error,
      health,
      mutation,
      terminal,
      canAppendMessage,
      canCancel,
      setTaskId,
      refreshTask,
      refreshHealth,
      createNewTask,
      appendMessage,
      cancelCurrentTask,
    }),
    [
      taskId,
      task,
      loading,
      error,
      health,
      mutation,
      terminal,
      canAppendMessage,
      canCancel,
      refreshTask,
      refreshHealth,
      createNewTask,
      appendMessage,
      cancelCurrentTask,
    ],
  );
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
