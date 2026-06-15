from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.router_schema import (
    Artifact,
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
