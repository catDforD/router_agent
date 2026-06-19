import { requestJson } from "./client";
import type { TaskTraceSummary } from "./types";

export async function getTaskTrace(taskId: string): Promise<TaskTraceSummary> {
  return requestJson<TaskTraceSummary>(
    `/api/tasks/${encodeURIComponent(taskId)}/trace`,
  );
}
