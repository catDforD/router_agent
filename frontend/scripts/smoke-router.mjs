#!/usr/bin/env node

const baseUrl = process.env.ROUTER_API_BASE_URL ?? "http://127.0.0.1:8000";

async function main() {
  const health = await requestJson("/api/health");
  assert(health.status === "ok", "backend health did not return ok");

  const created = await requestJson("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message:
        "Smoke task: create conveyor control with emergency stop and manual reset.",
      project_context: {
        target_plc_language: "ST",
        target_platform: "Codesys",
      },
    }),
  });
  assert(created.task_id, "task creation did not return task_id");
  assert(created.events_url, "task creation did not return events_url");

  const eventFrame = await readFirstEvent(created.events_url);
  assert(
    eventFrame.includes("event: task.created"),
    "SSE stream did not replay task.created",
  );

  const task = await requestJson(`/api/tasks/${created.task_id}`);
  assert(task.task_id === created.task_id, "task state id mismatch");

  const artifacts = await requestJson(`/api/tasks/${created.task_id}/artifacts`);
  assert(Array.isArray(artifacts.artifacts), "artifact list is not an array");
  const raw = artifacts.artifacts.find(
    (artifact) => artifact.type === "raw_user_request",
  );
  assert(raw, "raw user request artifact missing");

  const rawContent = await requestJson(`/api/artifacts/${raw.artifact_id}`);
  assert(rawContent.content, "raw artifact content missing");

  const trace = await requestJson(`/api/tasks/${created.task_id}/trace`);
  assert(trace.task_id === created.task_id, "trace task id mismatch");

  console.log(
    JSON.stringify(
      {
        status: "ok",
        task_id: created.task_id,
        first_event: "task.created",
        artifacts: artifacts.artifacts.length,
        trace_events: trace.events.length,
      },
      null,
      2,
    ),
  );
}

async function requestJson(path, init) {
  const response = await fetch(`${baseUrl}${path}`, init);
  if (!response.ok) {
    throw new Error(`${path} failed with HTTP ${response.status}`);
  }
  return response.json();
}

async function readFirstEvent(eventsUrl) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${baseUrl}${eventsUrl}`, {
      signal: controller.signal,
      headers: { Accept: "text/event-stream" },
    });
    if (!response.ok || response.body === null) {
      throw new Error(`event stream failed with HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = "";
    while (!text.includes("\n\n")) {
      const chunk = await reader.read();
      if (chunk.done) {
        break;
      }
      text += decoder.decode(chunk.value, { stream: true });
    }
    await reader.cancel();
    return text;
  } finally {
    clearTimeout(timeout);
  }
}

function assert(value, message) {
  if (!value) {
    throw new Error(message);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
