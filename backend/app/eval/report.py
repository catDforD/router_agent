"""Markdown/JSON report helpers for PLC eval runs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable
import json


MAX_FAILURE_REASON_CHARS = 500
ROUTE_ORDER = [
    "clarify_before_dispatch",
    "qa_direct_answer",
    "dev_then_test",
    "dev_then_test_then_formal",
    "test_only_existing_code",
    "formal_only_existing_code",
]
SOURCE_THEME_ORDER = ["generic_plc", "st_codesys"]


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    passed: bool
    task_id: str | None
    expected_statuses: list[str]
    actual_status: str | None
    expected_route: str | None = None
    route_hint: str | None = None
    topic_family: str | None = None
    source_theme: str | None = None
    difficulty: str | None = None
    message: str | None = None
    expected_worker_sequence: list[str] = field(default_factory=list)
    worker_sequence: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    invariant_results: dict[str, str] = field(default_factory=dict)
    event_count: int = 0
    gate_count: int = 0
    final_report_present: bool = False
    current_file_roles: dict[str, str] = field(default_factory=dict)
    transcript_path: str | None = None
    transcript_json_path: str | None = None
    worker_sequence_match: bool | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    connectivity_pass: bool | None = None
    first_tool_pass: bool | None = None
    required_sequence_pass: bool | None = None
    over_orchestration: bool | None = None
    final_status_match: bool | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class EvalRunSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float = 0.0
    result_path: str | None = None


def write_eval_report(results: list[EvalCaseResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_eval_report(results), encoding="utf-8")


def write_eval_html_report(
    results: list[EvalCaseResult],
    path: Path,
    *,
    title: str = "PLC Eval Report",
    execution_mode: str | None = None,
    evaluation_profile: str | None = None,
    mcp_mode: str | None = None,
    model: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_eval_html_report(
            results,
            title=title,
            execution_mode=execution_mode,
            evaluation_profile=evaluation_profile,
            mcp_mode=mcp_mode,
            model=model,
        ),
        encoding="utf-8",
    )


def write_eval_json_report(results: list[EvalCaseResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_eval_report_payload(results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_inspect_eval_log(
    results: list[EvalCaseResult],
    path: Path,
    *,
    run_dir: Path,
    source_file: Path,
    execution_mode: str = "deterministic_mock",
    evaluation_profile: str = "strict",
    mcp_mode: str | None = None,
    model: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            build_inspect_eval_log_payload(
                results,
                run_dir=run_dir,
                source_file=source_file,
                execution_mode=execution_mode,
                evaluation_profile=evaluation_profile,
                mcp_mode=mcp_mode,
                model=model,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def build_eval_report_payload(results: list[EvalCaseResult]) -> dict[str, Any]:
    detailed = [_case_payload(result) for result in results]
    return {
        "schema_version": "router.plc_eval_report.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary_payload(results),
        "metrics": _orchestration_metrics(results),
        "token_usage": _token_usage_summary(results),
        "status_distribution": _counter_rows(
            Counter(result.actual_status or "unknown" for result in results)
        ),
        "breakdowns": {
            "by_route": _bucket_rows(
                results,
                key_fn=lambda result: result.expected_route or "unknown",
                sort_key=_route_sort_key,
            ),
            "by_topic_family": _bucket_rows(
                results,
                key_fn=lambda result: result.topic_family or "unknown",
            ),
            "by_source_theme": _bucket_rows(
                results,
                key_fn=lambda result: result.source_theme or "unknown",
                sort_key=_source_theme_sort_key,
            ),
            "by_difficulty": _bucket_rows(
                results,
                key_fn=lambda result: result.difficulty or "unknown",
            ),
        },
        "worker_usage": {
            "worker_types": _counter_rows(
                Counter(
                    worker_type
                    for result in results
                    for worker_type in set(result.worker_sequence)
                ),
                denominator=len(results),
            ),
            "worker_sequences": _counter_rows(
                Counter(_format_worker_sequence(result.worker_sequence) for result in results)
            ),
        },
        "artifact_usage": {
            "artifact_types": _counter_rows(
                Counter(
                    artifact_type
                    for result in results
                    for artifact_type in set(result.artifact_types)
                ),
                denominator=len(results),
            ),
            "final_report_present_cases": sum(
                1 for result in results if result.final_report_present
            ),
            "gate_recorded_cases": sum(1 for result in results if result.gate_count > 0),
        },
        "file_usage": {
            "current_file_roles": _counter_rows(
                Counter(
                    role
                    for result in results
                    for role in result.current_file_roles
                ),
                denominator=len(results),
            ),
            "final_report_present_cases": sum(
                1
                for result in results
                if "final_report" in result.current_file_roles
            ),
            "gate_report_present_cases": sum(
                1
                for result in results
                if "latest_gate_report" in result.current_file_roles
            ),
        },
        "samples": detailed,
        "failures": [row for row in detailed if not row["passed"]],
    }


def build_inspect_eval_log_payload(
    results: list[EvalCaseResult],
    *,
    run_dir: Path,
    source_file: Path,
    execution_mode: str = "deterministic_mock",
    evaluation_profile: str = "strict",
    mcp_mode: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = _summary_payload(results)
    metrics = _orchestration_metrics(results)
    report_payload = build_eval_report_payload(results)
    is_live = execution_mode == "live_provider"
    worker_mode = mcp_mode or ("mock" if not is_live else "configured")
    solver = "live_main_agent_provider" if is_live else "deterministic_router_runner"
    worker_solver = (
        f"{worker_mode}_worker_dispatch"
        if is_live
        else "mock_worker_dispatch"
    )
    if evaluation_profile == "smoke":
        scorer = "runtime_liveness_smoke"
    elif evaluation_profile == "workflow":
        scorer = "workflow_contract_with_over_orchestration"
    else:
        scorer = "exact_status_and_worker_sequence"
    tags = [
        "router",
        "plc",
        "workflow",
        execution_mode,
        evaluation_profile,
        f"mcp:{worker_mode}",
    ]
    return {
        "version": 2,
        "status": "success",
        "eval": {
            "eval_id": f"router-plc-question-bank-{generated_at}",
            "run_id": run_dir.name,
            "created": generated_at,
            "task": "router_plc_question_bank",
            "task_id": "plc_realistic_question_bank",
            "task_version": 2,
            "task_file": str(source_file),
            "task_display_name": "Router PLC Workflow Question Bank",
            "solver": solver,
            "dataset": {
                "name": source_file.name,
                "location": str(source_file),
                "samples": len(results),
            },
            "model": model or (
                "configured-live-provider-model"
                if is_live
                else "router-main-agent-deterministic-runner"
            ),
            "task_args": {
                "run_dir": str(run_dir),
                "execution_mode": execution_mode,
                "evaluation_profile": evaluation_profile,
                "mcp_mode": worker_mode,
                "mock_workers": worker_mode == "mock",
            },
            "tags": tags,
            "metadata": {
                "schema_version": "router.inspect_eval_log.v1",
                "format_note": (
                    "Inspect-style JSON log. It mirrors the EvalLog hierarchy "
                    "but is not Inspect's binary .eval container."
                ),
            },
        },
        "plan": {
            "name": "router_workflow_orchestration",
            "steps": [
                {"solver": "create_task", "params": {}},
                {"solver": "main_agent_route", "params": {}},
                {"solver": worker_solver, "params": {}},
                {"solver": "quality_gate", "params": {}},
                {"solver": "score_orchestration_contract", "params": {}},
            ],
            "finish": {"solver": "write_reports", "params": {}},
        },
        "results": {
            "total_samples": summary["total_cases"],
            "completed_samples": summary["total_cases"],
            "scores": [
                {
                    "name": "router_orchestration_contract",
                    "scorer": scorer,
                    "scored_samples": summary["total_cases"],
                    "unscored_samples": 0,
                    "metrics": {
                        "accuracy": {
                            "name": "accuracy",
                            "value": summary["pass_rate"],
                        },
                        "failed": {
                            "name": "failed",
                            "value": summary["failed_cases"],
                        },
                        "worker_sequence_match_rate": {
                            "name": "worker_sequence_match_rate",
                            "value": metrics["worker_sequence_match_rate"],
                        },
                        "status_match_rate": {
                            "name": "status_match_rate",
                            "value": metrics["status_match_rate"],
                        },
                        "final_report_presence_rate": {
                            "name": "final_report_presence_rate",
                            "value": metrics["final_report_presence_rate"],
                        },
                        "connectivity_pass_rate": {
                            "name": "connectivity_pass_rate",
                            "value": metrics["connectivity_pass_rate"],
                        },
                        "first_tool_pass_rate": {
                            "name": "first_tool_pass_rate",
                            "value": metrics["first_tool_pass_rate"],
                        },
                        "required_sequence_pass_rate": {
                            "name": "required_sequence_pass_rate",
                            "value": metrics["required_sequence_pass_rate"],
                        },
                        "over_orchestration_rate": {
                            "name": "over_orchestration_rate",
                            "value": metrics["over_orchestration_rate"],
                        },
                    },
                }
            ],
            "metadata": {
                "breakdowns": report_payload["breakdowns"],
                "worker_usage": report_payload["worker_usage"],
                "file_usage": report_payload["file_usage"],
            },
        },
        "stats": {
            "started_at": "",
            "completed_at": generated_at,
            "model_usage": {
                "main_agent": _token_usage_summary(results),
            },
            "role_usage": {
                "main_agent": _token_usage_summary(results)["totals"],
            },
        },
        "error": None,
        "invalidated": False,
        "tags": tags,
        "metadata": {
            "run_dir": str(run_dir),
            "markdown_report": str(run_dir / "report.md"),
            "json_report": str(run_dir / "report.json"),
            "execution_mode": execution_mode,
            "evaluation_profile": evaluation_profile,
            "mcp_mode": worker_mode,
        },
        "samples": [_inspect_sample_payload(result) for result in results],
        "reductions": [
            {
                "scorer": scorer,
                "reducer": "mean",
                "samples": [
                    {
                        "sample_id": result.case_id,
                        "value": 1 if result.passed else 0,
                        "answer": result.actual_status or "unknown",
                        "explanation": result.failure_reason,
                    }
                    for result in results
                ],
            }
        ],
    }


def render_eval_report(results: list[EvalCaseResult]) -> str:
    summary = _summary_payload(results)
    metrics = _orchestration_metrics(results)
    token_usage = _token_usage_summary(results)
    route_rows = _bucket_rows(
        results,
        key_fn=lambda result: result.expected_route or "unknown",
        sort_key=_route_sort_key,
    )
    topic_rows = _bucket_rows(
        results,
        key_fn=lambda result: result.topic_family or "unknown",
    )
    theme_rows = _bucket_rows(
        results,
        key_fn=lambda result: result.source_theme or "unknown",
        sort_key=_source_theme_sort_key,
    )
    difficulty_rows = _bucket_rows(
        results,
        key_fn=lambda result: result.difficulty or "unknown",
    )
    worker_type_rows = _counter_rows(
        Counter(
            worker_type
            for result in results
            for worker_type in set(result.worker_sequence)
        ),
        denominator=len(results),
    )
    sequence_rows = _counter_rows(
        Counter(_format_worker_sequence(result.worker_sequence) for result in results)
    )
    artifact_rows = _counter_rows(
        Counter(
            artifact_type
            for result in results
            for artifact_type in set(result.artifact_types)
        ),
        denominator=len(results),
    )
    file_role_rows = _counter_rows(
        Counter(
            role
            for result in results
            for role in result.current_file_roles
        ),
        denominator=len(results),
    )

    lines = [
        "# PLC Eval Report",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total cases | {summary['total_cases']} |",
        f"| Passed | {summary['passed_cases']} |",
        f"| Failed | {summary['failed_cases']} |",
        f"| Pass rate | {_format_percent(summary['pass_rate'])} |",
        f"| Main Agent input tokens | {token_usage['totals']['input_tokens']} |",
        f"| Main Agent output tokens | {token_usage['totals']['output_tokens']} |",
        f"| Main Agent total tokens | {token_usage['totals']['total_tokens']} |",
        f"| Avg total tokens / case | {token_usage['averages']['total_tokens_per_case']:.1f} |",
        "",
        "## Orchestration Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Final status match rate | {_format_percent(metrics['status_match_rate'])} |",
        f"| Worker sequence match rate | {_format_percent(metrics['worker_sequence_match_rate'])} |",
        f"| Connectivity pass rate | {_format_percent(metrics['connectivity_pass_rate'])} |",
        f"| First tool pass rate | {_format_percent(metrics['first_tool_pass_rate'])} |",
        f"| Required sequence pass rate | {_format_percent(metrics['required_sequence_pass_rate'])} |",
        f"| Over-orchestration rate | {_format_percent(metrics['over_orchestration_rate'])} |",
        f"| Final report presence rate (terminal cases) | {_format_percent(metrics['final_report_presence_rate'])} |",
        f"| Gate record presence rate (terminal cases) | {_format_percent(metrics['gate_record_presence_rate'])} |",
        f"| Average worker calls per case | {metrics['avg_worker_calls_per_case']:.2f} |",
        f"| Max worker calls in one case | {metrics['max_worker_calls_per_case']} |",
        "",
        "## Breakdown By Route",
        "",
        "| Route | Cases | Passed | Failed | Pass Rate | Avg Worker Calls | Worker Sequence Variants |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in route_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["name"]),
                    str(row["total"]),
                    str(row["passed"]),
                    str(row["failed"]),
                    _format_percent(row["pass_rate"]),
                    f"{row['avg_worker_calls']:.2f}",
                    str(len(row["worker_sequence_variants"])),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Breakdown By Slice",
            "",
            "### Topic Family",
            "",
            "| Topic | Cases | Passed | Failed | Pass Rate |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in topic_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['total']} | {row['passed']} | {row['failed']} | {_format_percent(row['pass_rate'])} |"
        )

    lines.extend(
        [
            "",
            "### Source Theme",
            "",
            "| Theme | Cases | Passed | Failed | Pass Rate |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in theme_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['total']} | {row['passed']} | {row['failed']} | {_format_percent(row['pass_rate'])} |"
        )

    lines.extend(
        [
            "",
            "### Difficulty",
            "",
            "| Difficulty | Cases | Passed | Failed | Pass Rate |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in difficulty_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['total']} | {row['passed']} | {row['failed']} | {_format_percent(row['pass_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## Worker Usage",
            "",
            "### Worker Types",
            "",
            "| Worker | Cases | Coverage |",
            "| --- | --- | --- |",
        ]
    )
    for row in worker_type_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['count']} | {_format_percent(row['rate'])} |"
        )

    lines.extend(
        [
            "",
            "### Worker Sequences",
            "",
            "| Sequence | Cases | Coverage |",
            "| --- | --- | --- |",
        ]
    )
    for row in sequence_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['count']} | {_format_percent(row['rate'])} |"
        )

    lines.extend(
        [
            "",
            "## Artifact Usage",
            "",
            "| Artifact | Cases | Coverage |",
            "| --- | --- | --- |",
        ]
    )
    for row in artifact_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['count']} | {_format_percent(row['rate'])} |"
        )
    if not artifact_rows:
        lines.append("| legacy artifact store | 0 | 0.0% |")
        lines.append("")
        lines.append("This eval uses the file-centric runtime; see File Usage below.")

    lines.extend(
        [
            "",
            "## File Usage",
            "",
            "| Current File Role | Cases | Coverage |",
            "| --- | --- | --- |",
        ]
    )
    for row in file_role_rows:
        lines.append(
            f"| {_cell(row['name'])} | {row['count']} | {_format_percent(row['rate'])} |"
        )

    lines.extend(["", "## Samples"])
    for route_row in route_rows:
        route_name = route_row["name"]
        route_results = [
            result for result in results if (result.expected_route or "unknown") == route_name
        ]
        lines.extend(
            [
                "",
                f"### {route_name}",
                "",
                f"- Cases: `{route_row['total']}`",
                f"- Pass rate: `{_format_percent(route_row['pass_rate'])}`",
            ]
        )
        for result in route_results:
            lines.extend(
                [
                    "",
                    f"- `{result.case_id}`",
                    f"  - Result: `{'passed' if result.passed else 'failed'}`",
                    f"  - Topic / Theme / Difficulty: `{result.topic_family or 'unknown'}` / `{result.source_theme or 'unknown'}` / `{result.difficulty or 'unknown'}`",
                    f"  - Route hint: {_bounded(result.route_hint or 'n/a')}",
                    f"  - Message: {_bounded(result.message or '')}",
                    f"  - Expected / Actual status: `{', '.join(result.expected_statuses)}` / `{result.actual_status or 'unknown'}`",
                    f"  - Expected / Actual workers: `{_format_worker_sequence(result.expected_worker_sequence)}` / `{_format_worker_sequence(result.worker_sequence)}`",
                    f"  - Worker sequence match: `{_render_bool(result.worker_sequence_match)}`",
                    f"  - Workflow checks: connectivity=`{_render_bool(result.connectivity_pass)}`, first_tool=`{_render_bool(result.first_tool_pass)}`, required_sequence=`{_render_bool(result.required_sequence_pass)}`, over_orchestration=`{_render_bool(result.over_orchestration)}`, final_status=`{_render_bool(result.final_status_match)}`",
                    f"  - Artifacts: `{(', '.join(sorted(set(result.artifact_types))) or 'none')}`",
                    f"  - Current files: `{_format_current_file_roles(result.current_file_roles)}`",
                    f"  - Events / Gates / Final report: `{result.event_count}` / `{result.gate_count}` / `{_render_bool(result.final_report_present)}`",
                    f"  - Main Agent tokens: input=`{result.token_usage.get('input_tokens', 0)}`, output=`{result.token_usage.get('output_tokens', 0)}`, total=`{result.token_usage.get('total_tokens', 0)}`",
                    f"  - Task: `{result.task_id or 'not-created'}`",
                ]
            )
            if result.failure_reason:
                lines.append(f"  - Failure reason: {_bounded(result.failure_reason)}")

    failures = [result for result in results if not result.passed]
    lines.extend(["", "## Failures", ""])
    if not failures:
        lines.append("No failing cases.")
    else:
        for result in failures:
            lines.extend(
                [
                    f"### {result.case_id}",
                    "",
                    f"- Route: `{result.expected_route or 'unknown'}`",
                    f"- Topic / Theme: `{result.topic_family or 'unknown'}` / `{result.source_theme or 'unknown'}`",
                    f"- Message: {_bounded(result.message or '')}",
                    f"- Expected status: `{', '.join(result.expected_statuses)}`",
                    f"- Actual status: `{result.actual_status or 'unknown'}`",
                    f"- Expected workers: `{_format_worker_sequence(result.expected_worker_sequence)}`",
                    f"- Actual workers: `{_format_worker_sequence(result.worker_sequence)}`",
                    f"- Reason: {_bounded(result.failure_reason or 'unknown failure')}",
                    "",
                ]
            )

    return "\n".join(lines) + "\n"


def render_eval_html_report(
    results: list[EvalCaseResult],
    *,
    title: str = "PLC Eval Report",
    execution_mode: str | None = None,
    evaluation_profile: str | None = None,
    mcp_mode: str | None = None,
    model: str | None = None,
) -> str:
    summary = _summary_payload(results)
    metrics = _orchestration_metrics(results)
    token_usage = _token_usage_summary(results)
    route_rows = _bucket_rows(
        results,
        key_fn=lambda result: result.expected_route or "unknown",
        sort_key=_route_sort_key,
    )
    failures = [result for result in results if not result.passed]
    generated_at = datetime.now(timezone.utc).isoformat()
    context_items = [
        ("Execution", execution_mode or "unknown"),
        ("Profile", evaluation_profile or "unknown"),
        ("MCP", mcp_mode or "unknown"),
        ("Model", model or "unknown"),
        ("Generated", generated_at),
    ]
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        "<style>",
        _html_styles(),
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        '<section class="hero">',
        f"<h1>{escape(title)}</h1>",
        '<div class="context">',
        *[
            f'<span><strong>{escape(label)}</strong>{escape(str(value))}</span>'
            for label, value in context_items
        ],
        "</div>",
        "</section>",
        '<section class="cards">',
        _metric_card("Total", summary["total_cases"]),
        _metric_card("Passed", summary["passed_cases"], "good"),
        _metric_card("Failed", summary["failed_cases"], "bad" if failures else "good"),
        _metric_card("Pass Rate", _format_percent(summary["pass_rate"])),
        _metric_card("Total Tokens", token_usage["totals"]["total_tokens"]),
        _metric_card("Input Tokens", token_usage["totals"]["input_tokens"]),
        _metric_card("Output Tokens", token_usage["totals"]["output_tokens"]),
        _metric_card("Connectivity", _format_percent(metrics["connectivity_pass_rate"])),
        _metric_card("Required Seq", _format_percent(metrics["required_sequence_pass_rate"])),
        _metric_card("Over-Orch", _format_percent(metrics["over_orchestration_rate"]), "bad" if metrics["over_orchestration_rate"] else ""),
        _metric_card("Worker Match", _format_percent(metrics["worker_sequence_match_rate"])),
        _metric_card("Final Report", _format_percent(metrics["final_report_presence_rate"])),
        "</section>",
        '<section class="panel">',
        "<h2>Token Usage</h2>",
        '<table>',
        "<thead><tr><th>Scope</th><th>Cases With Usage</th><th>Input</th><th>Output</th><th>Total</th><th>Avg Total / Case</th><th>Max Case Total</th></tr></thead>",
        "<tbody>",
        "<tr>"
        "<td>Main Agent provider</td>"
        f"<td>{token_usage['cases_with_usage']}</td>"
        f"<td>{token_usage['totals']['input_tokens']}</td>"
        f"<td>{token_usage['totals']['output_tokens']}</td>"
        f"<td>{token_usage['totals']['total_tokens']}</td>"
        f"<td>{token_usage['averages']['total_tokens_per_case']:.1f}</td>"
        f"<td>{token_usage['max_case_total_tokens']}</td>"
        "</tr>",
        "</tbody>",
        "</table>",
        '<p class="muted">Token usage is aggregated from Main Agent provider usage recorded in per-case replay logs. PLC subagent internal provider usage is not included unless a future worker contract reports it separately.</p>',
        "</section>",
        '<section class="panel">',
        "<h2>Workflow Contract</h2>",
        '<table>',
        "<thead><tr><th>Metric</th><th>Rate</th><th>Meaning</th></tr></thead>",
        "<tbody>",
        f"<tr><td>Connectivity</td><td>{escape(_format_percent(metrics['connectivity_pass_rate']))}</td><td>Expected PLC worker jobs returned through the configured MCP/subagent route. Business failures do not count as connectivity failures.</td></tr>",
        f"<tr><td>First Tool</td><td>{escape(_format_percent(metrics['first_tool_pass_rate']))}</td><td>The first PLC worker matched the workflow entry point.</td></tr>",
        f"<tr><td>Required Sequence</td><td>{escape(_format_percent(metrics['required_sequence_pass_rate']))}</td><td>The expected PLC worker sequence appeared in order.</td></tr>",
        f"<tr><td>Over-Orchestration</td><td>{escape(_format_percent(metrics['over_orchestration_rate']))}</td><td>The Main Agent called extra PLC worker stages beyond the expected workflow.</td></tr>",
        f"<tr><td>Final Status</td><td>{escape(_format_percent(metrics['workflow_final_status_match_rate']))}</td><td>The task ended in the expected terminal state.</td></tr>",
        "</tbody>",
        "</table>",
        "</section>",
        '<section class="panel">',
        "<h2>Route Breakdown</h2>",
        '<table>',
        "<thead><tr><th>Route</th><th>Cases</th><th>Passed</th><th>Failed</th><th>Pass Rate</th><th>Avg Workers</th><th>Variants</th></tr></thead>",
        "<tbody>",
    ]
    for row in route_rows:
        lines.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td>{row['total']}</td>"
            f"<td>{row['passed']}</td>"
            f"<td>{row['failed']}</td>"
            f"<td>{escape(_format_percent(row['pass_rate']))}</td>"
            f"<td>{row['avg_worker_calls']:.2f}</td>"
            f"<td>{escape('; '.join(row['worker_sequence_variants']))}</td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>", "</section>"])

    lines.extend(
        [
            '<section class="panel">',
            "<h2>Failures</h2>",
        ]
    )
    if failures:
        lines.append('<div class="failure-list">')
        for result in failures:
            lines.append(
                '<article class="failure">'
                f"<h3>{escape(result.case_id)}</h3>"
                f"<p>{escape(result.failure_reason or 'unknown failure')}</p>"
                f"<p><strong>Route:</strong> {escape(result.expected_route or 'unknown')} "
                f"<strong>Status:</strong> {escape(result.actual_status or 'unknown')} "
                f"<strong>Workers:</strong> {escape(_format_worker_sequence(result.worker_sequence))}</p>"
                f"{_transcript_link(result)}"
                "</article>"
            )
        lines.append("</div>")
    else:
        lines.append('<p class="muted">No failing cases.</p>')
    lines.append("</section>")

    lines.extend(
        [
            '<section class="panel">',
            "<h2>Samples</h2>",
            '<table class="samples">',
            "<thead><tr><th>Case</th><th>Result</th><th>Route</th><th>Status</th><th>Workflow</th><th>Workers</th><th>Tokens</th><th>Events</th><th>Transcript</th><th>Prompt</th></tr></thead>",
            "<tbody>",
        ]
    )
    for result in results:
        status_class = "pass" if result.passed else "fail"
        lines.append(
            "<tr>"
            f"<td><code>{escape(result.case_id)}</code></td>"
            f'<td><span class="pill {status_class}">{"passed" if result.passed else "failed"}</span></td>'
            f"<td>{escape(result.expected_route or 'unknown')}</td>"
            f"<td>{escape(result.actual_status or 'unknown')}</td>"
            f"<td>{_workflow_badges(result)}</td>"
            f"<td>{escape(_format_worker_sequence(result.worker_sequence))}</td>"
            f"<td>{result.token_usage.get('total_tokens', 0)}</td>"
            f"<td>{result.event_count}</td>"
            f"<td>{_transcript_link(result)}</td>"
            f"<td>{escape(_bounded(result.message or ''))}</td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>", "</section>", "</main>", "</body>", "</html>"])
    return "\n".join(lines) + "\n"


def render_case_transcript_html(transcript: dict[str, Any]) -> str:
    case = _dict_value(transcript, "case")
    result = _dict_value(transcript, "result")
    run = _dict_value(transcript, "run")
    task = _dict_value(transcript, "task")
    main_agent = _dict_value(transcript, "main_agent")
    replay_log = _dict_value(main_agent, "replay_log")
    entries = _list_value(replay_log, "entries")
    provider_turns = _list_value(replay_log, "provider_transcript")
    worker_jobs = _list_value(transcript, "worker_jobs")
    events = _list_value(transcript, "events")
    artifacts = _list_value(transcript, "artifacts")
    gate_results = _list_value(transcript, "gate_results")
    current_files = _dict_value(transcript, "current_files")
    case_id = str(case.get("id") or "unknown-case")
    status_class = "pass" if bool(result.get("passed")) else "fail"

    sections: list[dict[str, str]] = []
    body_parts: list[str] = []

    def add_section(section_id: str, title: str, kind: str, html: str) -> None:
        sections.append({"id": section_id, "title": title, "kind": kind})
        body_parts.append(
            f'<section class="thread-section" id="{escape(section_id)}">'
            f'<div class="section-kicker">{escape(kind)}</div>'
            f"<h2>{escape(title)}</h2>"
            f"{html}"
            "</section>"
        )

    add_section(
        "case",
        "User Request",
        "input",
        _message_block(
            role="user",
            title="initial prompt",
            content=str(case.get("message") or ""),
        ),
    )

    provider_by_turn = _items_by_turn(provider_turns)
    entries_by_turn = _items_by_turn(entries)
    turn_indices = sorted(set(provider_by_turn) | set(entries_by_turn))
    for turn_index in turn_indices:
        html_parts: list[str] = []
        for provider in provider_by_turn.get(turn_index, []):
            html_parts.append(_provider_turn_block(provider))
        for entry in entries_by_turn.get(turn_index, []):
            html_parts.append(_replay_entry_block(entry))
        add_section(
            f"turn-{turn_index}",
            f"Turn {turn_index}",
            "main agent",
            "\n".join(html_parts),
        )

    add_section(
        "workers",
        "Worker Jobs",
        "subagents",
        _worker_jobs_html(worker_jobs),
    )
    add_section(
        "files",
        "Files And Artifacts",
        "state",
        _files_and_artifacts_html(current_files, artifacts, gate_results),
    )
    add_section(
        "events",
        "Event Stream",
        "runtime",
        _events_html(events),
    )
    add_section(
        "final",
        "Final Report",
        "output",
        _final_report_html(_dict_value(main_agent, "final_report"), result, task),
    )

    header_context = [
        ("Result", "passed" if bool(result.get("passed")) else "failed"),
        ("Route", str(case.get("expected_route") or "unknown")),
        ("Status", str(result.get("actual_status") or "unknown")),
        ("Workers", _format_worker_sequence(result.get("worker_sequence") or [])),
        ("Tokens", str(_dict_value(result, "token_usage").get("total_tokens", 0))),
        ("Connectivity", _render_bool(result.get("workflow_contract", {}).get("connectivity_pass"))),
        ("Required Seq", _render_bool(result.get("workflow_contract", {}).get("required_sequence_pass"))),
        ("Over-Orch", _render_bool(result.get("workflow_contract", {}).get("over_orchestration"))),
        ("Mode", str(run.get("execution_mode") or "unknown")),
        ("Model", str(run.get("model") or "unknown")),
    ]
    toc = "\n".join(
        '<a href="#{id}"><span>{kind}</span>{title}</a>'.format(
            id=escape(section["id"]),
            kind=escape(section["kind"]),
            title=escape(section["title"]),
        )
        for section in sections
    )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(case_id)} transcript</title>",
            "<style>",
            _transcript_styles(),
            "</style>",
            "</head>",
            "<body>",
            '<header class="transcript-header">',
            '<div class="header-inner">',
            f"<h1>{escape(case_id)}</h1>",
            f'<span class="pill {status_class}">{"passed" if bool(result.get("passed")) else "failed"}</span>',
            '<div class="header-grid">',
            *[
                f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
                for label, value in header_context
            ],
            "</div>",
            "</div>",
            "</header>",
            '<main class="transcript-shell">',
            '<article class="conversation">',
            *body_parts,
            "</article>",
            '<aside class="directory">',
            "<h2>Transcript</h2>",
            toc,
            '<div class="directory-note">Raw provider messages appear only when the eval was run with capture enabled.</div>',
            "</aside>",
            "</main>",
            "</body>",
            "</html>",
        ]
    ) + "\n"


def _summary_payload(results: list[EvalCaseResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    failed = total - passed
    return {
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": failed,
        "pass_rate": _ratio(passed, total),
    }


def _orchestration_metrics(results: list[EvalCaseResult]) -> dict[str, Any]:
    terminal_results = [
        result
        for result in results
        if "waiting_user" not in result.expected_statuses
    ]
    return {
        "status_match_rate": _ratio(
            sum(
                1
                for result in results
                if result.actual_status is not None
                and result.actual_status in result.expected_statuses
            ),
            len(results),
        ),
        "worker_sequence_match_rate": _ratio(
            sum(1 for result in results if result.worker_sequence_match is True),
            len(results),
        ),
        "connectivity_pass_rate": _nullable_bool_ratio(
            results,
            lambda result: result.connectivity_pass,
        ),
        "first_tool_pass_rate": _nullable_bool_ratio(
            results,
            lambda result: result.first_tool_pass,
        ),
        "required_sequence_pass_rate": _nullable_bool_ratio(
            results,
            lambda result: result.required_sequence_pass,
        ),
        "over_orchestration_rate": _nullable_bool_ratio(
            results,
            lambda result: result.over_orchestration,
        ),
        "workflow_final_status_match_rate": _nullable_bool_ratio(
            results,
            lambda result: result.final_status_match,
        ),
        "final_report_presence_rate": _ratio(
            sum(1 for result in terminal_results if result.final_report_present),
            len(terminal_results),
        ),
        "gate_record_presence_rate": _ratio(
            sum(1 for result in terminal_results if result.gate_count > 0),
            len(terminal_results),
        ),
        "avg_worker_calls_per_case": mean(
            [len(result.worker_sequence) for result in results]
        )
        if results
        else 0.0,
        "max_worker_calls_per_case": max(
            (len(result.worker_sequence) for result in results),
            default=0,
        ),
    }


def _token_usage_summary(results: list[EvalCaseResult]) -> dict[str, Any]:
    totals = {
        "input_tokens": sum(
            int(result.token_usage.get("input_tokens") or 0) for result in results
        ),
        "output_tokens": sum(
            int(result.token_usage.get("output_tokens") or 0) for result in results
        ),
        "total_tokens": sum(
            int(result.token_usage.get("total_tokens") or 0) for result in results
        ),
    }
    cases_with_usage = sum(
        1
        for result in results
        if any(int(result.token_usage.get(name) or 0) > 0 for name in totals)
    )
    denominator = len(results) or 1
    return {
        "scope": "main_agent_provider",
        "totals": totals,
        "cases_with_usage": cases_with_usage,
        "averages": {
            "input_tokens_per_case": totals["input_tokens"] / denominator,
            "output_tokens_per_case": totals["output_tokens"] / denominator,
            "total_tokens_per_case": totals["total_tokens"] / denominator,
        },
        "max_case_total_tokens": max(
            (int(result.token_usage.get("total_tokens") or 0) for result in results),
            default=0,
        ),
    }


def _bucket_rows(
    results: list[EvalCaseResult],
    *,
    key_fn: Callable[[EvalCaseResult], str],
    sort_key: Callable[[str], Any] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[EvalCaseResult]] = defaultdict(list)
    for result in results:
        grouped[key_fn(result)].append(result)

    key_sorter = sort_key or (lambda value: value)
    rows: list[dict[str, Any]] = []
    for name in sorted(grouped, key=key_sorter):
        items = grouped[name]
        passed = sum(1 for item in items if item.passed)
        rows.append(
            {
                "name": name,
                "total": len(items),
                "passed": passed,
                "failed": len(items) - passed,
                "pass_rate": _ratio(passed, len(items)),
                "avg_worker_calls": mean(
                    [len(item.worker_sequence) for item in items]
                )
                if items
                else 0.0,
                "worker_sequence_variants": sorted(
                    {
                        _format_worker_sequence(item.worker_sequence)
                        for item in items
                    }
                ),
            }
        )
    return rows


def _counter_rows(
    counter: Counter[str],
    *,
    denominator: int | None = None,
) -> list[dict[str, Any]]:
    total = denominator if denominator is not None else sum(counter.values())
    return [
        {
            "name": name,
            "count": count,
            "rate": _ratio(count, total),
        }
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _case_payload(result: EvalCaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "passed": result.passed,
        "task_id": result.task_id,
        "expected_route": result.expected_route,
        "route_hint": result.route_hint,
        "topic_family": result.topic_family,
        "source_theme": result.source_theme,
        "difficulty": result.difficulty,
        "message": result.message,
        "expected_statuses": list(result.expected_statuses),
        "actual_status": result.actual_status,
        "expected_worker_sequence": list(result.expected_worker_sequence),
        "worker_sequence": list(result.worker_sequence),
        "worker_sequence_match": result.worker_sequence_match,
        "token_usage": dict(result.token_usage),
        "workflow_contract": {
            "connectivity_pass": result.connectivity_pass,
            "first_tool_pass": result.first_tool_pass,
            "required_sequence_pass": result.required_sequence_pass,
            "over_orchestration": result.over_orchestration,
            "final_status_match": result.final_status_match,
        },
        "artifact_types": sorted(set(result.artifact_types)),
        "invariant_results": dict(result.invariant_results),
        "event_count": result.event_count,
        "gate_count": result.gate_count,
        "final_report_present": result.final_report_present,
        "current_file_roles": dict(result.current_file_roles),
        "transcript_path": result.transcript_path,
        "transcript_json_path": result.transcript_json_path,
        "failure_reason": result.failure_reason,
    }


def _inspect_sample_payload(result: EvalCaseResult) -> dict[str, Any]:
    expected_status = (
        result.expected_statuses[0]
        if len(result.expected_statuses) == 1
        else list(result.expected_statuses)
    )
    return {
        "id": result.case_id,
        "epoch": 1,
        "input": result.message or "",
        "target": {
            "status": expected_status,
            "worker_sequence": list(result.expected_worker_sequence),
        },
        "scores": {
            "router_orchestration_contract": {
                "value": "C" if result.passed else "I",
                "answer": result.actual_status or "unknown",
                "explanation": result.failure_reason,
                "metadata": {
                    "status_match": (
                        result.actual_status in result.expected_statuses
                        if result.actual_status is not None
                        else False
                    ),
                    "worker_sequence_match": result.worker_sequence_match,
                    "token_usage": dict(result.token_usage),
                    "connectivity_pass": result.connectivity_pass,
                    "first_tool_pass": result.first_tool_pass,
                    "required_sequence_pass": result.required_sequence_pass,
                    "over_orchestration": result.over_orchestration,
                    "final_status_match": result.final_status_match,
                    "final_report_present": result.final_report_present,
                },
            }
        },
        "metadata": {
            "expected_route": result.expected_route,
            "route_hint": result.route_hint,
            "topic_family": result.topic_family,
            "source_theme": result.source_theme,
            "difficulty": result.difficulty,
            "task_id": result.task_id,
            "actual_status": result.actual_status,
            "expected_statuses": list(result.expected_statuses),
            "worker_sequence": list(result.worker_sequence),
            "expected_worker_sequence": list(result.expected_worker_sequence),
            "token_usage": dict(result.token_usage),
            "workflow_contract": {
                "connectivity_pass": result.connectivity_pass,
                "first_tool_pass": result.first_tool_pass,
                "required_sequence_pass": result.required_sequence_pass,
                "over_orchestration": result.over_orchestration,
                "final_status_match": result.final_status_match,
            },
            "artifact_types": sorted(set(result.artifact_types)),
            "current_file_roles": dict(result.current_file_roles),
            "transcript_path": result.transcript_path,
            "transcript_json_path": result.transcript_json_path,
            "event_count": result.event_count,
            "gate_count": result.gate_count,
        },
        "store": {
            "worker_sequence": list(result.worker_sequence),
            "current_files": dict(result.current_file_roles),
            "invariant_results": dict(result.invariant_results),
            "token_usage": dict(result.token_usage),
            "transcript_path": result.transcript_path,
            "transcript_json_path": result.transcript_json_path,
        },
        "messages": [
            {
                "role": "user",
                "content": result.message or "",
            }
        ],
        "output": {
            "completion": _inspect_output_text(result),
        },
        "completed": True,
        "error": None if result.passed else result.failure_reason,
    }


def _route_sort_key(value: str) -> tuple[int, str]:
    try:
        return (ROUTE_ORDER.index(value), value)
    except ValueError:
        return (len(ROUTE_ORDER), value)


def _source_theme_sort_key(value: str) -> tuple[int, str]:
    try:
        return (SOURCE_THEME_ORDER.index(value), value)
    except ValueError:
        return (len(SOURCE_THEME_ORDER), value)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _nullable_bool_ratio(
    results: list[EvalCaseResult],
    key_fn: Callable[[EvalCaseResult], bool | None],
) -> float:
    values = [key_fn(result) for result in results]
    applicable = [value for value in values if value is not None]
    return _ratio(sum(1 for value in applicable if value is True), len(applicable))


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_worker_sequence(values: Iterable[str]) -> str:
    items = list(values)
    if not items:
        return "none"
    return " -> ".join(items)


def _format_current_file_roles(values: dict[str, str]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{name}={path}" for name, path in sorted(values.items()))


def _inspect_output_text(result: EvalCaseResult) -> str:
    status = "passed" if result.passed else "failed"
    return (
        f"{status}; actual_status={result.actual_status or 'unknown'}; "
        f"workers={_format_worker_sequence(result.worker_sequence)}"
    )


def _render_bool(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"


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


def _metric_card(label: str, value: Any, tone: str = "") -> str:
    tone_class = f" {tone}" if tone else ""
    return (
        f'<article class="card{tone_class}">'
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(str(value))}</strong>"
        "</article>"
    )


def _transcript_link(result: EvalCaseResult) -> str:
    if not result.transcript_path:
        return '<span class="muted">none</span>'
    json_link = (
        f' <a class="link secondary" href="{escape(result.transcript_json_path)}">json</a>'
        if result.transcript_json_path
        else ""
    )
    return (
        f'<a class="link" href="{escape(result.transcript_path)}">'
        "transcript"
        "</a>"
        f"{json_link}"
    )


def _workflow_badges(result: EvalCaseResult) -> str:
    items = [
        ("conn", result.connectivity_pass, False),
        ("first", result.first_tool_pass, False),
        ("seq", result.required_sequence_pass, False),
        ("over", result.over_orchestration, True),
        ("status", result.final_status_match, False),
    ]
    return " ".join(
        _workflow_badge(label, value, true_is_bad=true_is_bad)
        for label, value, true_is_bad in items
    )


def _workflow_badge(
    label: str,
    value: bool | None,
    *,
    true_is_bad: bool,
) -> str:
    if value is None:
        return f'<span class="mini-pill muted-pill">{escape(label)} n/a</span>'
    is_bad = value if true_is_bad else not value
    css_class = "bad-pill" if is_bad else "good-pill"
    text = "yes" if value else "no"
    return (
        f'<span class="mini-pill {css_class}">'
        f"{escape(label)} {escape(text)}"
        "</span>"
    )


def _dict_value(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key) if isinstance(source, dict) else None
    return value if isinstance(value, dict) else {}


def _list_value(source: dict[str, Any], key: str) -> list[Any]:
    value = source.get(key) if isinstance(source, dict) else None
    return value if isinstance(value, list) else []


def _items_by_turn(items: list[Any]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if not isinstance(item, dict):
            continue
        turn_index = item.get("turn_index")
        if turn_index is None and isinstance(item.get("payload"), dict):
            turn_index = item["payload"].get("turn_index")
        try:
            index = int(turn_index)
        except (TypeError, ValueError):
            index = 0
        grouped[index].append(item)
    return grouped


def _message_block(
    *,
    role: str,
    title: str,
    content: str,
    tone: str = "",
) -> str:
    tone_class = f" {tone}" if tone else ""
    return (
        f'<article class="message {escape(role)}{tone_class}">'
        '<div class="message-meta">'
        f"<strong>{escape(role)}</strong>"
        f"<span>{escape(title)}</span>"
        "</div>"
        f"<pre>{escape(content)}</pre>"
        "</article>"
    )


def _provider_turn_block(provider: dict[str, Any]) -> str:
    request = _dict_value(provider, "request")
    response = provider.get("response")
    assistant_turn = _dict_value(provider, "assistant_turn")
    messages = _list_value(request, "messages")
    tool_count = len(_list_value(request, "tools"))
    parts = [
        '<div class="provider-block">',
        '<div class="block-title"><strong>Provider Request / Response</strong>'
        f"<span>{len(messages)} messages · {tool_count} tools</span></div>",
    ]
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "message")
        content = _message_content_text(message)
        parts.append(
            _message_block(
                role=role,
                title=f"request message {index}",
                content=content,
                tone="provider",
            )
        )
        tool_calls = message.get("tool_calls")
        if tool_calls:
            parts.append(
                _details_block(
                    "assistant tool calls in request history",
                    tool_calls,
                    css_class="compact-json",
                )
            )
    if assistant_turn:
        assistant_content = str(assistant_turn.get("content") or "")
        parts.append(
            _message_block(
                role="assistant",
                title="parsed provider response",
                content=assistant_content or "(no text content)",
                tone="provider-response",
            )
        )
        tool_calls = assistant_turn.get("tool_calls")
        if tool_calls:
            parts.append(
                _details_block(
                    "parsed tool calls",
                    tool_calls,
                    open_by_default=True,
                    css_class="compact-json",
                )
            )
    parts.append(_details_block("raw provider request", request))
    parts.append(_details_block("raw provider response", response))
    parts.append("</div>")
    return "\n".join(parts)


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        return "(no text content)"
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def _replay_entry_block(entry: dict[str, Any]) -> str:
    entry_type = str(entry.get("type") or "entry")
    created_at = str(entry.get("created_at") or "")
    payload = _dict_value(entry, "payload")
    title = _entry_title(entry_type, payload)
    summary = _entry_summary(entry_type, payload)
    parts = [
        f'<article class="entry {escape(entry_type)}">',
        '<div class="entry-head">',
        f"<strong>{escape(title)}</strong>",
        f"<span>{escape(created_at)}</span>",
        "</div>",
    ]
    if summary:
        parts.append(f"<p>{escape(summary)}</p>")
    parts.append(_details_block("entry payload", payload, css_class="compact-json"))
    parts.append("</article>")
    return "\n".join(parts)


def _entry_title(entry_type: str, payload: dict[str, Any]) -> str:
    if entry_type == "tool_called":
        return f"Tool call: {payload.get('tool_name') or 'unknown'}"
    if entry_type == "tool_result":
        return f"Tool result: {payload.get('tool_name') or 'unknown'}"
    if entry_type == "message":
        return "Main Agent message"
    if entry_type == "final_response":
        return "Final response"
    if entry_type == "stop_blocked":
        return "Stop blocked"
    if entry_type == "token_usage":
        return "Token usage"
    return entry_type.replace("_", " ").title()


def _entry_summary(entry_type: str, payload: dict[str, Any]) -> str:
    if entry_type == "tool_called":
        return str(payload.get("rationale_summary") or "")
    if entry_type == "tool_result":
        return str(payload.get("summary") or "")
    if entry_type in {"message", "final_response"}:
        return str(payload.get("content") or "")
    if entry_type == "stop_blocked":
        return str(payload.get("reason") or "")
    if entry_type == "token_usage":
        return json.dumps(payload.get("token_usage_delta") or {}, ensure_ascii=False)
    return ""


def _worker_jobs_html(worker_jobs: list[Any]) -> str:
    if not worker_jobs:
        return '<p class="muted">No worker jobs recorded.</p>'
    rows: list[str] = []
    for index, job in enumerate(worker_jobs, start=1):
        if not isinstance(job, dict):
            continue
        rows.append(
            '<article class="worker-card">'
            '<div class="entry-head">'
            f"<strong>{escape(str(job.get('worker_type') or 'worker'))}</strong>"
            f"<span>job {index}</span>"
            "</div>"
            '<dl class="kv">'
            f"<div><dt>Status</dt><dd>{escape(str(job.get('status') or 'unknown'))}</dd></div>"
            f"<div><dt>Outcome</dt><dd>{escape(str(job.get('outcome_status') or 'unknown'))}</dd></div>"
            f"<div><dt>Job ID</dt><dd>{escape(str(job.get('worker_job_id') or job.get('job_id') or 'unknown'))}</dd></div>"
            "</dl>"
            f"{_details_block('worker job payload', job, css_class='compact-json')}"
            "</article>"
        )
    return "\n".join(rows) if rows else '<p class="muted">No worker jobs recorded.</p>'


def _files_and_artifacts_html(
    current_files: dict[str, Any],
    artifacts: list[Any],
    gate_results: list[Any],
) -> str:
    roles = _dict_value(current_files, "roles")
    manifest = _list_value(current_files, "manifest")
    parts = ['<div class="two-col">']
    parts.append("<div><h3>Current file roles</h3>")
    parts.append(_key_value_list(roles))
    parts.append("</div>")
    parts.append("<div><h3>Workspace manifest</h3>")
    parts.append(_small_table(manifest, ["role", "path", "size_bytes"]))
    parts.append("</div></div>")
    parts.append("<h3>Artifacts</h3>")
    parts.append(_small_table(artifacts, ["type", "artifact_id", "name", "version"]))
    parts.append("<h3>Gate results</h3>")
    parts.append(_small_table(gate_results, ["gate_id", "status", "summary"]))
    return "\n".join(parts)


def _events_html(events: list[Any]) -> str:
    if not events:
        return '<p class="muted">No events recorded.</p>'
    return _small_table(events, ["seq", "type", "title", "message", "created_at"])


def _final_report_html(
    final_report: dict[str, Any],
    result: dict[str, Any],
    task: dict[str, Any],
) -> str:
    parts = [
        '<dl class="kv">',
        f"<div><dt>Task ID</dt><dd>{escape(str(task.get('task_id') or 'unknown'))}</dd></div>",
        f"<div><dt>Actual status</dt><dd>{escape(str(result.get('actual_status') or 'unknown'))}</dd></div>",
        f"<div><dt>Expected status</dt><dd>{escape(str(result.get('expected_status') or 'unknown'))}</dd></div>",
        f"<div><dt>Failure reason</dt><dd>{escape(str(result.get('failure_reason') or 'none'))}</dd></div>",
        "</dl>",
    ]
    if final_report:
        summary = final_report.get("summary") or final_report.get("message") or ""
        if summary:
            parts.append(
                _message_block(
                    role="assistant",
                    title="final report summary",
                    content=str(summary),
                    tone="final",
                )
            )
        parts.append(_details_block("raw final report", final_report, open_by_default=True))
    else:
        parts.append('<p class="muted">No final report artifact was captured.</p>')
    return "\n".join(parts)


def _key_value_list(values: dict[str, Any]) -> str:
    if not values:
        return '<p class="muted">none</p>'
    rows = ["<dl class=\"kv\">"]
    for key, value in sorted(values.items()):
        rows.append(
            f"<div><dt>{escape(str(key))}</dt><dd>{escape(str(value))}</dd></div>"
        )
    rows.append("</dl>")
    return "\n".join(rows)


def _small_table(rows: list[Any], columns: list[str]) -> str:
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if not dict_rows:
        return '<p class="muted">none</p>'
    head = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body_rows = []
    for row in dict_rows:
        cells = "".join(
            f"<td>{escape(_table_value(row.get(column)))}</td>" for column in columns
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        '<div class="mini-table"><table>'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )


def _table_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _details_block(
    label: str,
    value: Any,
    *,
    open_by_default: bool = False,
    css_class: str = "",
) -> str:
    open_attr = " open" if open_by_default else ""
    class_attr = f' class="{escape(css_class)}"' if css_class else ""
    return (
        f"<details{open_attr}{class_attr}>"
        f"<summary>{escape(label)}</summary>"
        f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre>"
        "</details>"
    )


def _html_styles() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --text: #17202a;
  --muted: #667085;
  --line: #d9dee7;
  --good: #147a4b;
  --good-bg: #e8f6ef;
  --bad: #b42318;
  --bad-bg: #fdecec;
  --accent: #315efb;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.45;
}
main { max-width: 1280px; margin: 0 auto; padding: 28px; }
.hero { margin-bottom: 18px; }
h1 { margin: 0 0 12px; font-size: 28px; letter-spacing: 0; }
h2 { margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }
h3 { margin: 0 0 8px; font-size: 15px; letter-spacing: 0; }
.context { display: flex; flex-wrap: wrap; gap: 8px; }
.context span {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  color: var(--muted);
}
.context strong { color: var(--text); }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.card {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 14px;
}
.card span { display: block; color: var(--muted); margin-bottom: 6px; }
.card strong { font-size: 24px; letter-spacing: 0; }
.card.good strong { color: var(--good); }
.card.bad strong { color: var(--bad); }
.panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 16px;
  margin: 14px 0;
  overflow-x: auto;
}
table { width: 100%; border-collapse: collapse; min-width: 780px; }
th, td {
  border-bottom: 1px solid var(--line);
  padding: 9px 8px;
  text-align: left;
  vertical-align: top;
}
th {
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  background: #fbfcfe;
}
td code {
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
}
.samples td:last-child {
  max-width: 420px;
  min-width: 260px;
}
.pill {
  display: inline-block;
  min-width: 58px;
  padding: 2px 7px;
  border-radius: 999px;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
}
.pill.pass { color: var(--good); background: var(--good-bg); }
.pill.fail { color: var(--bad); background: var(--bad-bg); }
.mini-pill {
  display: inline-block;
  margin: 1px 3px 1px 0;
  padding: 2px 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
}
.good-pill { color: var(--good); background: var(--good-bg); }
.bad-pill { color: var(--bad); background: var(--bad-bg); }
.muted-pill { color: var(--muted); background: #edf0f4; }
.failure-list { display: grid; gap: 10px; }
.failure {
  border: 1px solid #f4b7b0;
  border-radius: 8px;
  background: var(--bad-bg);
  padding: 12px;
}
.failure p { margin: 6px 0; }
.link { color: var(--accent); text-decoration: none; font-weight: 600; }
.link.secondary { color: var(--muted); font-weight: 500; margin-left: 8px; }
.link:hover { text-decoration: underline; }
.muted { color: var(--muted); }
""".strip()


def _transcript_styles() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --surface: #ffffff;
  --surface-soft: #fafbfc;
  --text: #151b23;
  --muted: #64748b;
  --line: #d8dee7;
  --line-strong: #b8c0cc;
  --accent: #2457d6;
  --good: #147a4b;
  --good-bg: #e8f6ef;
  --bad: #b42318;
  --bad-bg: #fdecec;
  --code-bg: #f6f8fa;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.45;
}
.transcript-header {
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.96);
}
.header-inner {
  max-width: 1440px;
  margin: 0 auto;
  padding: 18px 28px;
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  gap: 14px;
  align-items: start;
}
h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.2;
  letter-spacing: 0;
  overflow-wrap: anywhere;
}
h2 { margin: 3px 0 14px; font-size: 18px; letter-spacing: 0; }
h3 { margin: 12px 0 8px; font-size: 14px; letter-spacing: 0; }
.header-grid {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 8px;
}
.header-grid div {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-soft);
  padding: 8px 10px;
  min-width: 0;
}
.header-grid span,
.section-kicker,
.directory a span,
.message-meta span,
.entry-head span,
dt {
  display: block;
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0;
}
.header-grid strong {
  display: block;
  margin-top: 3px;
  overflow-wrap: anywhere;
}
.pill {
  align-self: start;
  display: inline-flex;
  min-width: 70px;
  justify-content: center;
  padding: 4px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}
.pill.pass { color: var(--good); background: var(--good-bg); }
.pill.fail { color: var(--bad); background: var(--bad-bg); }
.transcript-shell {
  max-width: 1440px;
  margin: 0 auto;
  padding: 22px 28px 40px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 18px;
  align-items: start;
}
.conversation { min-width: 0; }
.thread-section {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  padding: 16px;
  margin-bottom: 14px;
  scroll-margin-top: 138px;
}
.directory {
  position: sticky;
  top: 128px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  padding: 12px;
  max-height: calc(100vh - 150px);
  overflow: auto;
}
.directory h2 {
  margin: 0 0 8px;
  font-size: 14px;
}
.directory a {
  display: block;
  padding: 8px;
  border-radius: 6px;
  color: var(--text);
  text-decoration: none;
}
.directory a:hover { background: var(--surface-soft); }
.directory-note {
  margin-top: 10px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
  color: var(--muted);
  font-size: 12px;
}
.provider-block {
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: #fbfcff;
  padding: 12px;
  margin: 10px 0;
}
.block-title,
.entry-head,
.message-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
  margin-bottom: 8px;
}
.block-title span { color: var(--muted); font-size: 12px; }
.message {
  border: 1px solid var(--line);
  border-left: 4px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface);
  padding: 11px;
  margin: 9px 0;
}
.message.user { border-left-color: #2457d6; }
.message.system { border-left-color: #6b7280; }
.message.assistant { border-left-color: #0f766e; }
.message.tool { border-left-color: #a16207; }
.message.provider-response { background: #f7fffc; }
.message.final { background: #f8fffa; }
pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
}
.message pre {
  border-radius: 6px;
  background: var(--code-bg);
  padding: 10px;
}
.entry,
.worker-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-soft);
  padding: 11px;
  margin: 9px 0;
}
.entry p {
  margin: 0 0 8px;
  color: var(--text);
  overflow-wrap: anywhere;
}
details {
  margin: 8px 0;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface);
  overflow: hidden;
}
summary {
  cursor: pointer;
  padding: 8px 10px;
  color: var(--accent);
  font-weight: 650;
}
details pre {
  border-top: 1px solid var(--line);
  padding: 10px;
  max-height: 520px;
  overflow: auto;
  background: var(--code-bg);
}
.compact-json pre { max-height: 260px; }
.kv {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
  margin: 0;
}
.kv div {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-soft);
  padding: 8px;
  min-width: 0;
}
dd {
  margin: 3px 0 0;
  overflow-wrap: anywhere;
}
.two-col {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 12px;
}
.mini-table { overflow-x: auto; }
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 520px;
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 8px;
  text-align: left;
  vertical-align: top;
}
th {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  background: var(--surface-soft);
}
td { overflow-wrap: anywhere; }
.muted { color: var(--muted); }
@media (max-width: 900px) {
  .header-inner,
  .transcript-shell {
    padding-left: 16px;
    padding-right: 16px;
  }
  .transcript-shell { grid-template-columns: 1fr; }
  .directory {
    position: static;
    max-height: none;
    order: -1;
  }
  .two-col { grid-template-columns: 1fr; }
  .thread-section { scroll-margin-top: 170px; }
}
""".strip()
