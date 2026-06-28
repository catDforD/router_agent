"""Load and group the 100-case PLC question bank."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


DEFAULT_QUESTION_BANK_FILE = Path(__file__).resolve().parents[1] / "tests" / "eval" / "plc_realistic_question_bank_100.json"


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

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value or value[0].isdigit():
            raise ValueError("question bank case id must start with a letter")
        return value


def load_question_bank_cases(path: Path = DEFAULT_QUESTION_BANK_FILE) -> list[QuestionBankCase]:
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
    if len(cases) != 100:
        raise QuestionBankValidationError(
            f"{source}: question bank must contain exactly 100 cases"
        )
    route_counts = {
        route: len(items)
        for route, items in group_question_bank_cases(cases).items()
    }
    expected_routes = {
        "clarify_before_dispatch",
        "qa_direct_answer",
        "dev_then_test",
        "dev_then_test_then_formal",
        "test_only_existing_code",
        "formal_only_existing_code",
        "repair_after_test_then_test",
        "repair_after_formal_then_test_then_formal",
    }
    missing_routes = sorted(expected_routes - set(route_counts))
    if missing_routes:
        raise QuestionBankValidationError(
            f"{source}: missing expected routes: {missing_routes}"
        )
    bad_counts = {}
    for route, count in route_counts.items():
        expected_count = 20 if route in {"clarify_before_dispatch", "qa_direct_answer"} else 10
        if count != expected_count:
            bad_counts[route] = count
    if bad_counts:
        raise QuestionBankValidationError(
            f"{source}: unexpected route bucket sizes: {bad_counts}"
        )
