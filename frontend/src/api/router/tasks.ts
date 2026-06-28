import { requestJson } from "./client";
import type {
  AppendUserMessageResponse,
  CreateTaskResponse,
  ProjectContext,
  TaskListResponse,
  TaskState,
} from "./types";

export async function createTask(
  message: string,
  projectContext: ProjectContext,
): Promise<CreateTaskResponse> {
  return requestJson<CreateTaskResponse>("/api/tasks", {
    method: "POST",
    body: JSON.stringify({
      message,
      project_context: projectContext,
    }),
  });
}

export async function getTask(taskId: string): Promise<TaskState> {
  return requestJson<TaskState>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function listTasks(limit = 20): Promise<TaskListResponse> {
  return requestJson<TaskListResponse>(
    `/api/tasks?limit=${encodeURIComponent(String(limit))}`,
  );
}

export async function appendUserMessage(
  taskId: string,
  message: string,
): Promise<AppendUserMessageResponse> {
  return requestJson<AppendUserMessageResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ message }),
    },
  );
}

export async function cancelTask(taskId: string): Promise<TaskState> {
  return requestJson<TaskState>(
    `/api/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST" },
  );
}

export async function deleteTask(taskId: string): Promise<void> {
  await requestJson<void>(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}
