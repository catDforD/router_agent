import { requestJson } from "./client";
import type {
  AppendSessionMessageResponse,
  CreateSessionResponse,
  ListSessionsResponse,
  ProjectContext,
  SessionResponse,
} from "./types";

export async function createSession(
  message: string,
  projectContext: ProjectContext,
): Promise<CreateSessionResponse> {
  return requestJson<CreateSessionResponse>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      message,
      project_context: projectContext,
    }),
  });
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}`,
  );
}

export async function listSessions(limit = 50): Promise<ListSessionsResponse> {
  return requestJson<ListSessionsResponse>(`/api/sessions?limit=${limit}`);
}

export async function appendSessionMessage(
  sessionId: string,
  message: string,
): Promise<AppendSessionMessageResponse> {
  return requestJson<AppendSessionMessageResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ message }),
    },
  );
}

export async function deleteSession(sessionId: string): Promise<void> {
  await requestJson<void>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}
