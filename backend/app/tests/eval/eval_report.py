"""Markdown report helpers for backend eval runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


MAX_FAILURE_REASON_CHARS = 500


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    passed: bool
    task_id: str | None
    expected_statuses: list[str]
    actual_status: str | None
    worker_sequence: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    invariant_results: dict[str, str] = field(default_factory=dict)
    failure_reason: str | None = None


def write_eval_report(results: list[EvalCaseResult], path: Path) -> None:
    """Write a compact Markdown report for eval execution."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_eval_report(results), encoding="utf-8")


def render_eval_report(results: list[EvalCaseResult]) -> str:
    passed = sum(1 for result in results if result.passed)
    lines = [
        "# Backend Eval Report",
        "",
        f"Cases: {len(results)}",
        f"Passed: {passed}",
        f"Failed: {len(results) - passed}",
        "",
        "| Case | Result | Task | Expected Status | Actual Status | Workers | Artifacts | Invariants |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(result.case_id),
                    "passed" if result.passed else "failed",
                    _cell(result.task_id or ""),
                    _cell(", ".join(result.expected_statuses)),
                    _cell(result.actual_status or ""),
                    _cell(" -> ".join(result.worker_sequence)),
                    _cell(", ".join(sorted(set(result.artifact_types)))),
                    _cell(_invariant_summary(result.invariant_results)),
                ]
            )
            + " |"
        )

    failures = [result for result in results if not result.passed]
    if failures:
        lines.extend(["", "## Failures", ""])
        for result in failures:
            lines.extend(
                [
                    f"### {result.case_id}",
                    "",
                    f"- Task: `{result.task_id or 'not-created'}`",
                    f"- Expected status: `{', '.join(result.expected_statuses)}`",
                    f"- Actual status: `{result.actual_status or 'unknown'}`",
                    f"- Workers: `{(' -> '.join(result.worker_sequence)) or 'none'}`",
                    f"- Reason: {_bounded(result.failure_reason or 'unknown failure')}",
                    "",
                ]
            )

    return "\n".join(lines) + "\n"


def _invariant_summary(results: dict[str, str]) -> str:
    if not results:
        return ""
    return ", ".join(f"{name}:{status}" for name, status in sorted(results.items()))


def _bounded(value: str) -> str:
    if len(value) <= MAX_FAILURE_REASON_CHARS:
        return value
    return value[: MAX_FAILURE_REASON_CHARS - 3] + "..."


def _cell(value: str) -> str:
    return _bounded(value).replace("|", "\\|").replace("\n", " ")
