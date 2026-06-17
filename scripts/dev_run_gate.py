"""Run Quality Gate assessment against a fixture task state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.router_schema import TaskState  # noqa: E402
from app.services.quality_gate import assess_quality_gate  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Quality Gate against a TaskState fixture.",
    )
    parser.add_argument(
        "--fixture",
        required=True,
        help="Fixture filename under backend/app/tests/fixtures or a JSON path.",
    )
    return parser.parse_args()


def load_fixture(path_or_name: str) -> dict[str, Any]:
    path = Path(path_or_name)
    if not path.exists():
        path = FIXTURE_DIR / path_or_name
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    state = TaskState.model_validate(load_fixture(args.fixture))
    assessment = assess_quality_gate(state)
    print(json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
