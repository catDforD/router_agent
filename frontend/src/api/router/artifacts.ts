import { requestJson } from "./client";
import type { ArtifactContentResponse, ArtifactListResponse } from "./types";

export async function listTaskArtifacts(
  taskId: string,
): Promise<ArtifactListResponse> {
  return requestJson<ArtifactListResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/artifacts`,
  );
}

export async function readArtifactContent(
  artifactId: string,
): Promise<ArtifactContentResponse> {
  return requestJson<ArtifactContentResponse>(
    `/api/artifacts/${encodeURIComponent(artifactId)}`,
  );
}

export function parseArtifactContent(payload: ArtifactContentResponse): unknown {
  if (payload.mime_type === "application/json") {
    return JSON.parse(payload.content);
  }
  return payload.content;
}
