import type { ApiErrorPayload, HealthResponse } from "./types";

export class RouterApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload?: unknown) {
    super(message);
    this.name = "RouterApiError";
    this.status = status;
    this.payload = payload;
  }
}

export const API_BASE_URL =
  import.meta.env.VITE_ROUTER_API_BASE_URL?.replace(/\/$/, "") ?? "";

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${normalized}`;
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(apiUrl(path), {
    ...init,
    headers,
  });

  if (!response.ok) {
    throw await apiError(response);
  }

  return (await response.json()) as T;
}

export async function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/health");
}

async function apiError(response: Response): Promise<RouterApiError> {
  let payload: ApiErrorPayload | undefined;
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    payload = undefined;
  }

  const detail = payload?.detail;
  const message =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg ?? JSON.stringify(item)).join("; ")
        : `Router API request failed with HTTP ${response.status}`;

  return new RouterApiError(message, response.status, payload);
}
