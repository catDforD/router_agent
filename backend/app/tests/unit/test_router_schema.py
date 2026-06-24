from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.router_schema import (
    Artifact,
    EventType,
    RouterEvent,
    TaskState,
    WorkerInput,
    WorkerResult,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(file_name: str) -> dict:
    return json.loads((FIXTURE_DIR / file_name).read_text(encoding="utf-8"))


def test_valid_task_state_can_be_created() -> None:
    task_state = TaskState.model_validate(load_fixture("task_state.valid.json"))

    assert task_state.schema_version == "router.v1"
    assert task_state.task_id == "task-001"
    assert task_state.status == "created"
    assert task_state.phase == "intake"
    assert task_state.current_artifacts.all_artifact_ids == [
        "artifact-raw-request-001"
    ]
    assert task_state.event_seq == 0


@pytest.mark.parametrize(
    ("file_name", "model"),
    [
        ("task_state.valid.json", TaskState),
        ("worker_input.plc_dev.valid.json", WorkerInput),
        ("worker_result.test_failed.valid.json", WorkerResult),
        ("artifact.plc_code.valid.json", Artifact),
        ("event.worker_started.valid.json", RouterEvent),
    ],
)
def test_invalid_schema_version_is_rejected(file_name: str, model: type) -> None:
    payload = load_fixture(file_name)
    payload["schema_version"] = "router.v2"

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_worker_input_missing_task_id_is_rejected() -> None:
    payload = load_fixture("worker_input.plc_dev.valid.json")
    payload.pop("task_id")

    with pytest.raises(ValidationError):
        WorkerInput.model_validate(payload)


def test_worker_input_with_worker_config_parses() -> None:
    payload = load_fixture("worker_input.plc_dev.valid.json")

    worker_input = WorkerInput.model_validate(payload)

    assert worker_input.worker_config is not None
    assert worker_input.worker_config.target_language == "ST"
    assert worker_input.worker_config.llm is not None
    assert worker_input.worker_config.llm.model == "deepseek-worker"


def test_worker_input_worker_config_reloads_with_null_fields() -> None:
    payload = load_fixture("worker_input.plc_dev.valid.json")
    payload["worker_config"].update(
        {
            "fuzz_method": None,
            "case_count": None,
            "enable_fuzz_test": None,
            "properties": None,
            "repair_source": None,
            "repair_targets": None,
        }
    )

    worker_input = WorkerInput.model_validate(payload)

    assert worker_input.worker_config is not None
    assert worker_input.worker_config.target_language == "ST"


def test_worker_input_rejects_unsupported_non_null_worker_config_fields() -> None:
    payload = load_fixture("worker_input.plc_dev.valid.json")
    payload["worker_config"]["case_count"] = 10

    with pytest.raises(ValidationError, match="not supported"):
        WorkerInput.model_validate(payload)


def test_worker_result_execution_status_and_outcome_status_are_distinct() -> None:
    worker_result = WorkerResult.model_validate(
        load_fixture("worker_result.test_failed.valid.json")
    )

    assert worker_result.execution_status == "completed"
    assert worker_result.outcome.status == "failed"
    assert worker_result.execution_status != worker_result.outcome.status


def test_artifact_allows_externalized_large_content() -> None:
    payload = load_fixture("artifact.plc_code.valid.json")

    assert "inline_content" not in payload
    artifact = Artifact.model_validate(payload)
    assert artifact.storage.uri == "local://artifacts/task-001/pump_interlock.st"
    assert artifact.inline_content is None


def test_router_event_seq_must_be_integer() -> None:
    payload = deepcopy(load_fixture("event.worker_started.valid.json"))
    payload["seq"] = "not-an-integer"

    with pytest.raises(ValidationError):
        RouterEvent.model_validate(payload)


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.MAIN_AGENT_TURN_STARTED,
        EventType.MAIN_AGENT_MESSAGE,
        EventType.MAIN_AGENT_TOOL_CALLED,
        EventType.MAIN_AGENT_TOOL_RESULT,
        EventType.MAIN_AGENT_COMPLETED,
    ],
)
def test_main_agent_observability_event_types_validate(
    event_type: EventType,
) -> None:
    payload = deepcopy(load_fixture("event.worker_started.valid.json"))
    payload.update(
        {
            "event_id": f"event-{event_type.value.replace('.', '-')}",
            "type": event_type.value,
            "source": {"type": "main_agent", "id": "main-agent"},
            "title": "Main Agent observability",
            "message": "Main Agent observability event.",
            "payload": {
                "task_id": payload["task_id"],
                "turn_index": 1,
                "tool_name": "call_plc_dev",
                "rationale_summary": "Start by generating PLC code.",
                "final_report_artifact_id": "artifact-final-report-001",
                "main_agent_log_artifact_id": "artifact-main-agent-log-001",
                "final_task_status": "succeeded",
            },
            "correlation": {
                "main_agent_run_id": "main-agent-run-001",
                "artifact_ids": [
                    "artifact-final-report-001",
                    "artifact-main-agent-log-001",
                ],
            },
        }
    )

    event = RouterEvent.model_validate(payload)

    assert event.type == event_type


def test_final_report_and_main_agent_log_artifact_types_validate() -> None:
    base = load_fixture("artifact.plc_code.valid.json")

    for artifact_type in ("final_report", "main_agent_log"):
        payload = deepcopy(base)
        payload.update(
            {
                "artifact_id": f"artifact-{artifact_type}",
                "type": artifact_type,
                "name": f"{artifact_type}.json",
                "summary": f"{artifact_type} artifact.",
                "storage": {
                    **payload["storage"],
                    "uri": (
                        f"local://artifacts/task-001/{artifact_type}/v1/"
                        f"artifact-{artifact_type}__{artifact_type}.json"
                    ),
                    "path": (
                        f"task-001/{artifact_type}/v1/"
                        f"artifact-{artifact_type}__{artifact_type}.json"
                    ),
                    "mime_type": "application/json",
                },
            }
        )

        artifact = Artifact.model_validate(payload)

        assert artifact.type == artifact_type
