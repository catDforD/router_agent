"""Load and group the PLC workflow question bank."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


DEFAULT_QUESTION_BANK_FILE = Path(__file__).resolve().parents[1] / "tests" / "eval" / "plc_realistic_question_bank_100.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_ROUTE_COUNTS = {
    "clarify_before_dispatch": 20,
    "qa_direct_answer": 20,
    "dev_then_test": 10,
    "dev_then_test_then_formal": 10,
    "test_only_existing_code": 10,
    "formal_only_existing_code": 10,
}
EXECUTION_ROUTES = {
    "dev_then_test",
    "dev_then_test_then_formal",
    "test_only_existing_code",
    "formal_only_existing_code",
}


class QuestionBankValidationError(ValueError):
    """Raised when the question bank is malformed."""


class QuestionBankBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class QuestionBankCase(QuestionBankBaseModel):
    id: str
    message: str = Field(min_length=1)
    topic_family: str
    route_hint: str
    expected_route: str
    source_theme: str
    difficulty: str
    benchmark_id: str | None = None
    benchmark_st_path: str | None = None
    validation_focus: str | None = None
    formal_properties: list[dict[str, object]] | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value or value[0].isdigit():
            raise ValueError("question bank case id must start with a letter")
        return value


def load_question_bank_cases(
    path: Path = DEFAULT_QUESTION_BANK_FILE,
) -> list[QuestionBankCase]:
    return parse_question_bank_cases_text(
        path.read_text(encoding="utf-8"),
        source=str(path),
    )


def parse_question_bank_cases_text(
    text: str,
    *,
    source: str = "<memory>",
) -> list[QuestionBankCase]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise QuestionBankValidationError(
            f"{source}: question bank must be JSON: {exc}"
        ) from exc

    if not isinstance(raw, list):
        raise QuestionBankValidationError(f"{source}: question bank must be a list")

    cases: list[QuestionBankCase] = []
    for index, item in enumerate(raw, start=1):
        try:
            cases.append(QuestionBankCase.model_validate(item))
        except ValidationError as exc:
            raise QuestionBankValidationError(
                f"{source}: invalid question bank case at index {index}: {exc}"
            ) from exc

    _validate_unique_ids(cases, source=source)
    _validate_coverage(cases, source=source)
    return cases


def group_question_bank_cases(
    cases: list[QuestionBankCase],
) -> dict[str, list[QuestionBankCase]]:
    grouped: dict[str, list[QuestionBankCase]] = {}
    for case in cases:
        grouped.setdefault(case.expected_route, []).append(case)
    return grouped


def _validate_unique_ids(cases: list[QuestionBankCase], *, source: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for case in cases:
        if case.id in seen:
            duplicates.append(case.id)
        seen.add(case.id)
    if duplicates:
        raise QuestionBankValidationError(
            f"{source}: duplicate question bank ids: {sorted(set(duplicates))}"
        )


def _validate_coverage(cases: list[QuestionBankCase], *, source: str) -> None:
    if len(cases) != 80:
        raise QuestionBankValidationError(
            f"{source}: question bank must contain exactly 80 cases"
        )
    route_counts = {
        route: len(items)
        for route, items in group_question_bank_cases(cases).items()
    }
    expected_routes = set(EXPECTED_ROUTE_COUNTS)
    unknown_routes = sorted(set(route_counts) - expected_routes)
    if unknown_routes:
        raise QuestionBankValidationError(
            f"{source}: unknown expected routes: {unknown_routes}"
        )
    missing_routes = sorted(expected_routes - set(route_counts))
    if missing_routes:
        raise QuestionBankValidationError(
            f"{source}: missing expected routes: {missing_routes}"
        )
    bad_counts: dict[str, int] = {}
    for route, expected_count in EXPECTED_ROUTE_COUNTS.items():
        count = route_counts.get(route, 0)
        if count != expected_count:
            bad_counts[route] = count
    if bad_counts:
        raise QuestionBankValidationError(
            f"{source}: unexpected route bucket sizes: {bad_counts}"
        )
    _validate_benchmark_metadata(cases, source=source)


def _validate_benchmark_metadata(
    cases: list[QuestionBankCase],
    *,
    source: str,
) -> None:
    missing_metadata: list[str] = []
    bad_paths: list[str] = []
    for case in cases:
        if case.expected_route not in EXECUTION_ROUTES:
            continue
        if not case.benchmark_id or not case.benchmark_st_path:
            missing_metadata.append(case.id)
            continue
        path = Path(case.benchmark_st_path)
        if path.is_absolute() or ".." in path.parts:
            bad_paths.append(f"{case.id}: {case.benchmark_st_path}")
            continue
        if not (
            _is_allowed_st_path(case.benchmark_st_path)
            and (REPO_ROOT / path).is_file()
        ):
            bad_paths.append(f"{case.id}: {case.benchmark_st_path}")

    if missing_metadata:
        raise QuestionBankValidationError(
            f"{source}: execution route cases missing benchmark metadata: {missing_metadata}"
        )
    if bad_paths:
        raise QuestionBankValidationError(
            f"{source}: invalid benchmark ST paths: {bad_paths}"
        )


def _is_allowed_st_path(value: str) -> bool:
    if not value.endswith(".st"):
        return False
    if (
        value.startswith("data/raw-data/benchmark_raw/")
        and "/plc_st_files/" in value
    ):
        return True
    return value.startswith("backend/app/tests/eval/formal_st_files/")
