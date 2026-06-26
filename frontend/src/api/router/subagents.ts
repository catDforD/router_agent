import { requestJson } from "./client";
import type { SubagentStatusResponse } from "./types";

export async function getSubagentStatus(): Promise<SubagentStatusResponse> {
  return requestJson<SubagentStatusResponse>("/api/subagents/status");
}
