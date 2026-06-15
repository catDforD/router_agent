"""Export Router v1 Pydantic models as JSON Schema files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.models.router_schema import ROUTER_V1_SCHEMA_MODELS


SCHEMA_VERSION = "router.v1"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_BASE = "https://schemas.router.local/router.v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_DIR = REPO_ROOT / "schema"


@dataclass(frozen=True)
class SchemaExport:
    model_key: str
    file_name: str
    schema_id_name: str


SCHEMA_EXPORTS: tuple[SchemaExport, ...] = (
    SchemaExport("task_state", "task_state.schema.json", "task_state"),
    SchemaExport("worker_input", "worker_input.schema.json", "worker_input"),
    SchemaExport("worker_result", "worker_result.schema.json", "worker_result"),
    SchemaExport("artifact", "artifact.schema.json", "artifact"),
    SchemaExport("event", "router_event.schema.json", "router_event"),
    SchemaExport("event", "event.schema.json", "event"),
)


def build_json_schema(model: type[BaseModel], schema_id_name: str) -> dict[str, object]:
    """Build a JSON Schema document with Router contract metadata."""
    schema = model.model_json_schema()
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["$id"] = f"{SCHEMA_ID_BASE}/{schema_id_name}.schema.json"
    schema["x-schema-version"] = SCHEMA_VERSION
    return schema


def export_schemas(output_dir: Path = DEFAULT_SCHEMA_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    for schema_export in SCHEMA_EXPORTS:
        model = ROUTER_V1_SCHEMA_MODELS[schema_export.model_key]
        schema = build_json_schema(model, schema_export.schema_id_name)
        output_path = output_dir / schema_export.file_name
        output_path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written_paths.append(output_path)

    return written_paths


def main() -> None:
    for path in export_schemas():
        print(path.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
