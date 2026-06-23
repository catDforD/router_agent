import { apiUrl } from "./client";
import type { EventType, RouterEvent } from "./types";

export type StreamState = "idle" | "connecting" | "connected" | "reconnecting" | "closed" | "error";

export interface TaskEventStreamOptions {
  afterSeq?: number;
  onOpen?: () => void;
  onEvent: (event: RouterEvent) => void;
  onError?: (error: Event) => void;
}

export interface TaskEventStream {
  source: EventSource;
  close: () => void;
}

export const ROUTER_EVENT_TYPES: EventType[] = [
  "task.created",
  "task.updated",
  "task.waiting_user",
  "task.succeeded",
  "task.partial_failed",
  "task.failed",
  "task.cancelled",
  "agent.started",
  "agent.decision",
  "agent.plan_updated",
  "agent.clarification_requested",
  "agent.finalizing",
  "agent.turn_started",
  "agent.message",
  "agent.tool_called",
  "agent.tool_result",
  "agent.completed",
  "worker.job_created",
  "worker.started",
  "worker.progress",
  "worker.completed",
  "worker.partial",
  "worker.error",
  "worker.timeout",
  "worker.cancelled",
  "artifact.processing",
  "artifact.created",
  "artifact.available",
  "artifact.failed",
  "gate.started",
  "gate.passed",
  "gate.failed",
  "repair.round_started",
  "repair.round_completed",
  "repair.round_failed",
];

export function eventStreamUrl(taskId: string, afterSeq = 0): string {
  const encoded = encodeURIComponent(taskId);
  return apiUrl(`/api/tasks/${encoded}/events?after_seq=${afterSeq}`);
}

export function openTaskEventStream(
  taskId: string,
  options: TaskEventStreamOptions,
): TaskEventStream {
  const source = new EventSource(eventStreamUrl(taskId, options.afterSeq ?? 0));

  source.onopen = () => options.onOpen?.();
  source.onerror = (error) => options.onError?.(error);

  for (const eventType of ROUTER_EVENT_TYPES) {
    source.addEventListener(eventType, (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as RouterEvent;
      options.onEvent(event);
    });
  }

  return {
    source,
    close: () => source.close(),
  };
}

export function isTerminalEvent(event: RouterEvent): boolean {
  return (
    event.type === "task.succeeded" ||
    event.type === "task.partial_failed" ||
    event.type === "task.failed" ||
    event.type === "task.cancelled"
  );
}
