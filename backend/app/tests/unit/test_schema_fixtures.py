import json
from pathlib import Path
import re

import pytest
from pydantic import BaseModel

from app.models.router_schema import (
    Artifact,
    EventType,
    RouterEvent,
    WorkerConfig,
    TaskState,
    WorkerInput,
    WorkerResult,
)
from app.schemas.json_schema_export import (
    JSON_SCHEMA_DIALECT,
    SCHEMA_VERSION,
    build_json_schema,
    export_schemas,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"

FIXTURE_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("task_state.valid.json", TaskState),
    ("worker_input.plc_dev.valid.json", WorkerInput),
    ("worker_result.test_failed.valid.json", WorkerResult),
    ("artifact.plc_code.valid.json", Artifact),
    ("event.worker_started.valid.json", RouterEvent),
)

CORE_MODELS: tuple[tuple[str, type[BaseModel], str, str], ...] = (
    ("task_state", TaskState, "TaskState", "task_state"),
    ("worker_input", WorkerInput, "WorkerInput", "worker_input"),
    ("worker_result", WorkerResult, "WorkerResult", "worker_result"),
    ("artifact", Artifact, "Artifact", "artifact"),
    ("router_event", RouterEvent, "RouterEvent", "router_event"),
)


@pytest.mark.parametrize(("file_name", "model"), FIXTURE_MODELS)
def test_valid_schema_fixture_parses(file_name: str, model: type[BaseModel]) -> None:
    payload = json.loads((FIXTURE_DIR / file_name).read_text(encoding="utf-8"))

    parsed = model.model_validate(payload)

    assert parsed.schema_version == "router.v1"


def test_json_schema_export_writes_required_files(tmp_path: Path) -> None:
    written_paths = export_schemas(tmp_path)
    written_names = {path.name for path in written_paths}

    assert {
        "task_state.schema.json",
        "worker_input.schema.json",
        "worker_result.schema.json",
        "artifact.schema.json",
        "router_event.schema.json",
        "event.schema.json",
    } <= written_names

    for path in written_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == JSON_SCHEMA_DIALECT
        assert schema["$id"].startswith("https://schemas.router.local/router.v1/")
        assert schema["x-schema-version"] == SCHEMA_VERSION


def test_typescript_contract_mentions_core_pydantic_field_surface() -> None:
    ts_contract = (REPO_ROOT / "schema/ts/router_contract.d.ts").read_text(
        encoding="utf-8"
    )

    for schema_id_name, model, interface_name, json_schema_id in CORE_MODELS:
        json_schema = build_json_schema(model, json_schema_id)
        json_fields = set(json_schema["properties"])
        pydantic_fields = set(model.model_fields)
        interface_body = extract_interface_body(ts_contract, interface_name)

        assert pydantic_fields <= json_fields, schema_id_name
        for field_name in pydantic_fields:
            assert re.search(rf"\b{re.escape(field_name)}\??:", interface_body), (
                schema_id_name,
                field_name,
            )


def test_worker_config_parses_and_exports() -> None:
    json_schema = build_json_schema(WorkerConfig, "worker_config")

    assert "target_language" in json_schema["properties"]
    assert "llm" in json_schema["properties"]


def test_typescript_contract_mentions_main_agent_observability_events() -> None:
    ts_contract = (REPO_ROOT / "schema/ts/router_contract.d.ts").read_text(
        encoding="utf-8"
    )

    for event_type in (
        EventType.MAIN_AGENT_TURN_STARTED,
        EventType.MAIN_AGENT_MESSAGE,
        EventType.MAIN_AGENT_TOOL_CALLED,
        EventType.MAIN_AGENT_TOOL_RESULT,
        EventType.MAIN_AGENT_COMPLETED,
    ):
        assert f'"{event_type.value}"' in ts_contract


def extract_interface_body(ts_contract: str, interface_name: str) -> str:
    marker = f"export interface {interface_name} {{"
    start = ts_contract.index(marker) + len(marker)
    depth = 1
    position = start

    while position < len(ts_contract):
        char = ts_contract[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return ts_contract[start:position]
        position += 1

    raise AssertionError(f"interface not closed: {interface_name}")
