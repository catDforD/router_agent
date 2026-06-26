import json
from pathlib import Path

from app.models.router_schema import AgentSession, TaskState, WorkerInput, WorkerResult
from app.repositories._helpers import (
    sanitize_legacy_agent_session_payload,
    sanitize_legacy_task_state_payload,
    sanitize_legacy_worker_input_payload,
    sanitize_legacy_worker_result_payload,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def test_legacy_agent_session_project_context_artifact_fields_parse() -> None:
    payload = {
        "schema_version": "router.v2",
        "session_id": "session-legacy",
        "title": "Legacy session",
        "status": "active",
        "project_context": {
            "target_plc_language": "ST",
            "coding_style_artifact_id": "artifact-style",
            "project_memory_artifact_ids": ["artifact-memory"],
        },
        "event_seq": 0,
        "runs": [],
        "created_at": "2026-06-15T10:00:00Z",
        "updated_at": "2026-06-15T10:00:00Z",
    }

    session = AgentSession.model_validate(
        sanitize_legacy_agent_session_payload(payload)
    )

    assert session.project_context.target_plc_language == "ST"
    assert "coding_style_artifact_id" not in session.model_dump(mode="json")


def test_legacy_task_state_artifact_fields_parse() -> None:
    payload = _load_fixture("task_state.valid.json")
    payload["project_context"]["coding_style_artifact_id"] = "artifact-style"
    payload["project_context"]["project_memory_artifact_ids"] = ["artifact-memory"]
    payload["current_artifacts"] = {
        "raw_user_request": {
            "artifact_id": "artifact-request",
            "type": "raw_user_request",
            "version": 1,
        },
        "all_artifact_ids": ["artifact-request"],
    }
    payload["failures"] = [
        {
            "failure_id": "failure-legacy",
            "source": "test",
            "severity": "blocking",
            "title": "Legacy failure",
            "description": "Failure stored before file-centric migration.",
            "evidence_artifact_ids": ["artifact-report"],
            "resolved_by_artifact_id": "artifact-patch",
            "reproduction": {
                "input_trace_artifact_id": "artifact-trace",
                "counterexample_artifact_id": "artifact-counterexample",
            },
            "status": "open",
            "created_at": "2026-06-15T10:02:00Z",
        }
    ]

    task = TaskState.model_validate(sanitize_legacy_task_state_payload(payload))

    assert task.current_files.all_paths == []
    assert task.failures[0].evidence_paths == [".router/legacy/artifact-report.json"]
    assert (
        task.failures[0].reproduction is not None
        and task.failures[0].reproduction.input_trace_path
        == ".router/legacy/artifact-trace.json"
    )


def test_legacy_worker_input_artifacts_are_mapped_to_input_paths() -> None:
    payload = _load_fixture("worker_input.plc_dev.valid.json")
    payload["input_paths"] = []
    payload["input_artifacts"] = [
        {
            "artifact_id": "artifact-request",
            "type": "raw_user_request",
            "version": 1,
            "uri": "workspace://.router/requests/request.json",
        }
    ]
    payload.pop("workspace_root")
    payload.pop("current_directory")
    payload.pop("expected_outputs")

    worker_input = WorkerInput.model_validate(
        sanitize_legacy_worker_input_payload(payload)
    )

    assert worker_input.workspace_root == "."
    assert worker_input.current_directory == "."
    assert worker_input.input_paths == [".router/requests/request.json"]


def test_legacy_worker_result_artifacts_are_mapped_to_paths() -> None:
    payload = _load_fixture("worker_result.test_failed.valid.json")
    payload.pop("written_paths")
    payload.pop("report_paths")
    payload["produced_artifacts"] = [
        {
            "artifact_id": "artifact-report",
            "type": "test_report",
            "version": 1,
            "uri": "workspace://.router/reports/worker-job-test-001/test_report.json",
        }
    ]
    payload["diagnostics"][0]["related_artifact_ids"] = ["artifact-report"]
    payload["diagnostics"][0]["location"] = {
        "artifact_id": "artifact-code",
        "line_start": 3,
    }
    payload["failures"][0].pop("evidence_paths")
    payload["failures"][0]["evidence_artifact_ids"] = ["artifact-report"]

    worker_result = WorkerResult.model_validate(
        sanitize_legacy_worker_result_payload(payload)
    )

    assert worker_result.written_paths == [
        ".router/reports/worker-job-test-001/test_report.json"
    ]
    assert worker_result.report_paths == [
        ".router/reports/worker-job-test-001/test_report.json"
    ]
    assert worker_result.failures[0].evidence_paths == [
        ".router/reports/worker-job-test-001/test_report.json"
    ]
    assert worker_result.diagnostics[0].location is not None
    assert worker_result.diagnostics[0].location.file_path is None


def _load_fixture(file_name: str) -> dict:
    return json.loads((FIXTURE_DIR / file_name).read_text(encoding="utf-8"))
