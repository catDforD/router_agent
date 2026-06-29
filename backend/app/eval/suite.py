"""Executes the PLC eval question bank against the local runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.ids import prefixed_id
from app.agents.main_agent import (
    build_main_agent_event,
    episode_output_from_task,
)
from app.agents.output_schema import (
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentPlanStep,
)
from app.agents.tools import AgentToolContext, AgentToolResult, AgentToolService
from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.core.time import utc_now
from app.mcp.mock_worker import (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
)
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import EventType, TaskState, TaskStatus
from app.models.router_schema import Failure, FailureSource, FailureStatus, Severity
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeRunResult, RuntimeService
from app.services.task_service import TaskService
from app.eval.question_bank import (
    DEFAULT_QUESTION_BANK_FILE,
    QuestionBankCase,
    load_question_bank_cases,
)
from app.eval.report import (
    EvalCaseResult,
    EvalRunSummary,
    build_eval_report_payload,
    render_case_transcript_html,
    write_eval_html_report,
    write_inspect_eval_log,
    write_eval_report,
)


EXECUTION_MODE_DETERMINISTIC_MOCK = "deterministic_mock"
EXECUTION_MODE_LIVE_PROVIDER = "live_provider"
EVALUATION_PROFILE_STRICT = "strict"
EVALUATION_PROFILE_SMOKE = "smoke"
EVALUATION_PROFILE_WORKFLOW = "workflow"
EXECUTION_MODES = {
    EXECUTION_MODE_DETERMINISTIC_MOCK,
    EXECUTION_MODE_LIVE_PROVIDER,
}
EVALUATION_PROFILES = {
    EVALUATION_PROFILE_STRICT,
    EVALUATION_PROFILE_SMOKE,
    EVALUATION_PROFILE_WORKFLOW,
}


@dataclass(frozen=True)
class EvalTaskAudit:
    task: TaskState
    worker_jobs: list[WorkerJobRow]
    artifacts: list[Any]
    events: list[Any]
    gate_results: list[Any]
    final_report: dict[str, Any] | None
    replay_log: dict[str, Any] | None
    workspace_files: list[dict[str, Any]]


@dataclass(frozen=True)
class EvalCaseOutcome:
    case: QuestionBankCase
    passed: bool
    task_id: str | None
    actual_status: str | None
    worker_sequence: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    invariant_results: dict[str, str] = field(default_factory=dict)
    failure_reason: str | None = None
    audit: EvalTaskAudit | None = None
    transcript_path: str | None = None
    transcript_json_path: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    connectivity_pass: bool | None = None
    first_tool_pass: bool | None = None
    required_sequence_pass: bool | None = None
    over_orchestration: bool | None = None
    final_status_match: bool | None = None

    def to_report_row(self) -> EvalCaseResult:
        expected_workers = _expected_worker_sequence_for_route(self.case.expected_route) or []
        return EvalCaseResult(
            case_id=self.case.id,
            passed=self.passed,
            task_id=self.task_id,
            expected_statuses=[_expected_final_status_for_route(self.case.expected_route)],
            actual_status=self.actual_status,
            expected_route=self.case.expected_route,
            route_hint=self.case.route_hint,
            topic_family=self.case.topic_family,
            source_theme=self.case.source_theme,
            difficulty=self.case.difficulty,
            message=self.case.message,
            expected_worker_sequence=expected_workers,
            worker_sequence=list(self.worker_sequence),
            artifact_types=list(self.artifact_types),
            invariant_results=dict(self.invariant_results),
            event_count=len(self.audit.events) if self.audit is not None else 0,
            gate_count=len(self.audit.gate_results) if self.audit is not None else 0,
            final_report_present=self.audit.final_report is not None if self.audit is not None else False,
            current_file_roles=(
                _current_file_roles(self.audit.task)
                if self.audit is not None
                else {}
            ),
            transcript_path=self.transcript_path,
            transcript_json_path=self.transcript_json_path,
            worker_sequence_match=list(self.worker_sequence) == expected_workers,
            token_usage=dict(self.token_usage),
            connectivity_pass=self.connectivity_pass,
            first_tool_pass=self.first_tool_pass,
            required_sequence_pass=self.required_sequence_pass,
            over_orchestration=self.over_orchestration,
            final_status_match=self.final_status_match,
            failure_reason=self.failure_reason,
        )


@dataclass(frozen=True)
class EvalRunResult:
    run_dir: Path
    summary: EvalRunSummary
    cases: list[EvalCaseOutcome]
    markdown_report_path: Path
    json_report_path: Path
    inspect_log_path: Path
    html_report_path: Path
    transcript_dir: Path


class QuestionBankEvalRunner:
    """Deterministic runner that replays a scripted route for one question-bank case."""

    uses_tool_loop_side_effects = False

    def __init__(self, case: QuestionBankCase) -> None:
        self.case = case
        self.calls: list[str] = []

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        self.calls.append("orchestration")
        task_id = run_config.group_id
        tools = AgentToolService(context)
        if self.case.expected_route == "clarify_before_dispatch":
            self.uses_tool_loop_side_effects = True
            result = tools.request_clarification(
                task_id,
                questions=[
                    {
                        "question": "Which PLC platform and I/O names should be used?",
                        "reason": "The route case is intentionally missing detail.",
                        "required": True,
                    }
                ],
                rationale_summary=f"Question bank case {self.case.id} needs clarification.",
            )
            if result.status != "applied":
                raise RuntimeError(result.model_dump_json(indent=2))
            task = TaskRepository(context.session).get_task(task_id)
            return episode_output_from_task(
                task,
                main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
                summary=f"Question bank case {self.case.id} paused for clarification.",
            )

        plan_kwargs = _plan_kwargs_for_route(self.case.expected_route)
        plan_result = tools.update_plan(
            task_id,
            summary=f"Question bank case {self.case.id} prepared for route {self.case.expected_route}.",
            plan=[{"order": 1, "action": "route scenario"}],
            **plan_kwargs,
        )
        if plan_result.status != "applied":
            raise RuntimeError(plan_result.model_dump_json(indent=2))

        tool_results: list[AgentToolResult] = []
        for action in _sequence_for_route(self.case.expected_route):
            if action == "finalizing":
                self._emit_finalizing(context, task_id)
                continue
            result = _run_tool(tools, task_id, action)
            tool_results.append(result)

        task = TaskRepository(context.session).get_task(task_id)
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary=f"Question bank case {self.case.id} completed.",
            decisions=[
                MainAgentDecision(
                    decision_type="question_bank_route",
                    summary=f"Executed route {self.case.expected_route}.",
                    action="finish",
                    artifact_refs=_output_artifact_refs(tool_results),
                    details={
                        "case_id": self.case.id,
                        "route": self.case.expected_route,
                        "tools": [result.tool for result in tool_results],
                        "statuses": [str(result.status) for result in tool_results],
                    },
                )
            ],
            artifact_refs=_output_artifact_refs(tool_results),
        )
        final_status = _expected_final_status_for_route(self.case.expected_route)
        if final_status is not None:
            output = output.model_copy(
                update={
                    "final_task_status": final_status,
                    "phase": "completed"
                    if final_status != TaskStatus.WAITING_USER.value
                    else task.phase,
                    "next_recommended_action": "none",
                }
            )
        return output

    def _emit_finalizing(self, context: AgentToolContext, task_id: str) -> None:
        task = TaskRepository(context.session).get_task(task_id)
        EventService(context.session).append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message="Question bank route runner is running Quality Gate before finish.",
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id, "case_id": self.case.id},
                created_at=utc_now(),
            )
        )
        if context.checkpoint is not None:
            context.checkpoint()


def run_question_bank_suite(
    *,
    cases: list[QuestionBankCase] | None = None,
    run_dir: Path,
    database_url: str | None = None,
    execution_mode: str = EXECUTION_MODE_DETERMINISTIC_MOCK,
    settings: Settings | None = None,
    evaluation_profile: str = EVALUATION_PROFILE_STRICT,
    mcp_mode: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    subagent_timeout_seconds: int | None = None,
    stop_on_failure: bool = False,
) -> EvalRunResult:
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(f"unsupported execution_mode: {execution_mode!r}")
    if evaluation_profile not in EVALUATION_PROFILES:
        raise ValueError(f"unsupported evaluation_profile: {evaluation_profile!r}")
    run_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_reports(run_dir)
    artifact_root = run_dir / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    db_url = database_url or f"sqlite+pysqlite:///{run_dir / 'router_eval.db'}"
    run_settings = settings or Settings(
        app_env="test",
        database_url=db_url,
        artifact_root=artifact_root,
        session_workspace_root=run_dir / "workspaces",
    )
    run_settings = run_settings.model_copy(
            update={
                "app_env": "test",
                "database_url": db_url,
                "artifact_root": artifact_root,
                "session_workspace_root": run_dir / "workspaces",
                **(
                    {"subagent_timeout_seconds": subagent_timeout_seconds}
                    if subagent_timeout_seconds is not None
                    else {}
                ),
            }
        )
    if execution_mode == EXECUTION_MODE_DETERMINISTIC_MOCK:
        run_settings = run_settings.model_copy(
            update={
                "mcp_mode": "mock",
                "plc_dev_mode": None,
                "plc_test_mode": None,
                "plc_formal_mode": None,
                "plc_repair_mode": None,
                "main_agent_stream": False,
            }
        )
    elif mcp_mode in {"mock", "real", "subagent"}:
        run_settings = run_settings.model_copy(
            update={
                "mcp_mode": mcp_mode,
                "plc_dev_mode": mcp_mode,
                "plc_test_mode": mcp_mode,
                "plc_formal_mode": mcp_mode,
                "plc_repair_mode": mcp_mode,
            }
        )
    runtime_mcp_mode = (
        "mock"
        if execution_mode == EXECUTION_MODE_DETERMINISTIC_MOCK
        else (mcp_mode or run_settings.mcp_mode)
    )
    engine = get_engine_for_url(db_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session_factory = get_session_factory_for_url(db_url)
    selected_cases = cases or load_question_bank_cases()
    outcomes: list[EvalCaseOutcome] = []
    report_written = False
    try:
        for case in selected_cases:
            outcome = _run_case(
                run_settings,
                session_factory,
                case,
                execution_mode=execution_mode,
                evaluation_profile=evaluation_profile,
                mcp_mode=runtime_mcp_mode,
                model=model,
                max_turns=max_turns,
            )
            outcomes.append(outcome)
            if stop_on_failure and not outcome.passed:
                break
        report = _write_run_reports(
            outcomes,
            run_dir=run_dir,
            execution_mode=execution_mode,
            evaluation_profile=evaluation_profile,
            mcp_mode=runtime_mcp_mode,
            model=model or run_settings.main_agent_model,
            metadata={
                "subagent_timeout_seconds": run_settings.subagent_timeout_seconds,
                "max_turns": max_turns or run_settings.main_agent_max_turns,
            },
        )
        report_written = True
        return report
    except BaseException:
        if outcomes and not report_written:
            _write_run_reports(
                outcomes,
                run_dir=run_dir,
                execution_mode=execution_mode,
                evaluation_profile=evaluation_profile,
                mcp_mode=runtime_mcp_mode,
                model=model or run_settings.main_agent_model,
                metadata={
                    "subagent_timeout_seconds": run_settings.subagent_timeout_seconds,
                    "max_turns": max_turns or run_settings.main_agent_max_turns,
                },
                partial=True,
            )
        raise
    finally:
        engine.dispose()


def _write_run_reports(
    outcomes: list[EvalCaseOutcome],
    *,
    run_dir: Path,
    execution_mode: str,
    evaluation_profile: str,
    mcp_mode: str | None,
    model: str | None,
    metadata: dict[str, Any] | None = None,
    partial: bool = False,
) -> EvalRunResult:
    transcript_dir = run_dir / "transcripts"
    _clear_directory_files(transcript_dir, suffix=".json")
    _clear_directory_files(transcript_dir, suffix=".html")
    outcomes = [
        _outcome_with_transcript(
            outcome,
            transcript_dir=transcript_dir,
            run_dir=run_dir,
            execution_mode=execution_mode,
            evaluation_profile=evaluation_profile,
            mcp_mode=mcp_mode,
            model=model,
        )
        for outcome in outcomes
    ]
    report_rows = [outcome.to_report_row() for outcome in outcomes]
    markdown_report_path = run_dir / "report.md"
    json_report_path = run_dir / "report.json"
    inspect_log_path = run_dir / "report.eval.json"
    html_report_path = run_dir / "report.html"
    title = "Router PLC Eval Report"
    if partial:
        title += " (Partial)"
    write_eval_report(report_rows, markdown_report_path)
    write_eval_html_report(
        report_rows,
        html_report_path,
        title=title,
        execution_mode=execution_mode,
        evaluation_profile=evaluation_profile,
        mcp_mode=mcp_mode,
        model=model,
    )
    payload = build_eval_report_payload(report_rows)
    payload["summary"]["run_dir"] = str(run_dir)
    payload["summary"]["execution_mode"] = execution_mode
    payload["summary"]["evaluation_profile"] = evaluation_profile
    payload["summary"]["html_report_path"] = str(html_report_path)
    payload["summary"]["transcript_dir"] = str(transcript_dir)
    payload["summary"]["partial"] = partial
    if metadata:
        payload["summary"]["run_metadata"] = dict(metadata)
    json_report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_inspect_eval_log(
        report_rows,
        inspect_log_path,
        run_dir=run_dir,
        source_file=DEFAULT_QUESTION_BANK_FILE,
        execution_mode=execution_mode,
        evaluation_profile=evaluation_profile,
        mcp_mode=mcp_mode,
        model=model,
    )
    summary = EvalRunSummary(
        total_cases=len(outcomes),
        passed_cases=sum(1 for outcome in outcomes if outcome.passed),
        failed_cases=sum(1 for outcome in outcomes if not outcome.passed),
        pass_rate=round(
            sum(1 for outcome in outcomes if outcome.passed) / len(outcomes),
            4,
        )
        if outcomes
        else 0.0,
        result_path=str(json_report_path),
    )
    return EvalRunResult(
        run_dir=run_dir,
        summary=summary,
        cases=outcomes,
        markdown_report_path=markdown_report_path,
        json_report_path=json_report_path,
        inspect_log_path=inspect_log_path,
        html_report_path=html_report_path,
        transcript_dir=transcript_dir,
    )


def _clear_previous_reports(run_dir: Path) -> None:
    for name in ("report.md", "report.json", "report.eval.json", "report.html"):
        path = run_dir / name
        if path.exists():
            path.unlink()


def _clear_directory_files(path: Path, *, suffix: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_file() and child.name.endswith(suffix):
            child.unlink()


def _run_case(
    settings: Settings,
    session_factory: sessionmaker[Session],
    case: QuestionBankCase,
    *,
    execution_mode: str,
    evaluation_profile: str,
    mcp_mode: str | None,
    model: str | None,
    max_turns: int | None,
) -> EvalCaseOutcome:
    with session_factory() as session:
        task_service = TaskService(session=session, artifact_root=settings.artifact_root)
        created = task_service.create_task(
            message=case.message,
            project_context={
                "target_plc_language": "ST"
                if case.source_theme == "st_codesys"
                else "unknown",
                "target_platform": "Codesys" if case.source_theme == "st_codesys" else "Generic PLC",
            },
        )
        session.commit()
        _seed_context_files_for_case(settings, session_factory, created.task.task_id, case)
        runner = (
            QuestionBankEvalRunner(case)
            if execution_mode == EXECUTION_MODE_DETERMINISTIC_MOCK
            else None
        )
        runtime = RuntimeService(
            settings=settings,
            session_factory=session_factory,
            artifact_root=settings.artifact_root,
            mcp_mode=mcp_mode,
            mock_scenario=_mock_scenario_for_route(case.expected_route),
            model=model,
            max_turns=max_turns,
            runner=runner,
        )
        result = runtime.start_task(created.task.task_id)
        audit = _load_audit(settings, session_factory, created.task.task_id)
        passed, invariant_results, failure_reason = _evaluate_case(
            case,
            audit,
            result,
            evaluation_profile=evaluation_profile,
        )
        workflow_checks = _workflow_checks(case, audit)
        return EvalCaseOutcome(
            case=case,
            passed=passed,
            task_id=audit.task.task_id,
            actual_status=audit.task.status,
            worker_sequence=[row.worker_type for row in audit.worker_jobs],
            artifact_types=[str(artifact.type) for artifact in audit.artifacts],
            invariant_results=invariant_results,
            failure_reason=failure_reason,
            audit=audit,
            token_usage=_extract_token_usage(audit),
            connectivity_pass=workflow_checks["connectivity_pass"],
            first_tool_pass=workflow_checks["first_tool_pass"],
            required_sequence_pass=workflow_checks["required_sequence_pass"],
            over_orchestration=workflow_checks["over_orchestration"],
            final_status_match=workflow_checks["final_status_match"],
        )


def _load_audit(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
) -> EvalTaskAudit:
    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        artifacts = ArtifactRepository(session).list_task_artifacts(task_id)
        final_report = None
        if task.current_files.final_report is not None and task.workspace is not None:
            final_report_path = Path(task.workspace.root) / task.current_files.final_report
            try:
                final_report = json.loads(final_report_path.read_text(encoding="utf-8"))
            except Exception:
                final_report = None
        replay_log = None
        if task.current_files.main_agent_log is not None and task.workspace is not None:
            replay_log_path = Path(task.workspace.root) / task.current_files.main_agent_log
            try:
                replay_log = json.loads(replay_log_path.read_text(encoding="utf-8"))
            except Exception:
                replay_log = None
        return EvalTaskAudit(
            task=task,
            worker_jobs=_worker_rows(session, task_id),
            artifacts=artifacts,
            events=EventService(session).list_visible_events(task_id),
            gate_results=GateResultRepository(session).list_results(task_id),
            final_report=final_report,
            replay_log=replay_log,
            workspace_files=_workspace_file_manifest(task),
        )


def _outcome_with_transcript(
    outcome: EvalCaseOutcome,
    *,
    transcript_dir: Path,
    run_dir: Path,
    execution_mode: str,
    evaluation_profile: str,
    mcp_mode: str | None,
    model: str | None,
) -> EvalCaseOutcome:
    if outcome.audit is None:
        return outcome
    transcript_json_path = transcript_dir / f"{outcome.case.id}.json"
    transcript_html_path = transcript_dir / f"{outcome.case.id}.html"
    transcript = _build_case_transcript(
        outcome,
        run_dir=run_dir,
        execution_mode=execution_mode,
        evaluation_profile=evaluation_profile,
        mcp_mode=mcp_mode,
        model=model,
    )
    transcript_json_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    transcript_html_path.write_text(
        render_case_transcript_html(transcript),
        encoding="utf-8",
    )
    return EvalCaseOutcome(
        case=outcome.case,
        passed=outcome.passed,
        task_id=outcome.task_id,
        actual_status=outcome.actual_status,
        worker_sequence=list(outcome.worker_sequence),
        artifact_types=list(outcome.artifact_types),
        invariant_results=dict(outcome.invariant_results),
        failure_reason=outcome.failure_reason,
        audit=outcome.audit,
        transcript_path=transcript_html_path.relative_to(run_dir).as_posix(),
        transcript_json_path=transcript_json_path.relative_to(run_dir).as_posix(),
        token_usage=dict(outcome.token_usage),
        connectivity_pass=outcome.connectivity_pass,
        first_tool_pass=outcome.first_tool_pass,
        required_sequence_pass=outcome.required_sequence_pass,
        over_orchestration=outcome.over_orchestration,
        final_status_match=outcome.final_status_match,
    )


def _build_case_transcript(
    outcome: EvalCaseOutcome,
    *,
    run_dir: Path,
    execution_mode: str,
    evaluation_profile: str,
    mcp_mode: str | None,
    model: str | None,
) -> dict[str, Any]:
    audit = outcome.audit
    if audit is None:
        raise ValueError("cannot build transcript without audit")
    task = audit.task
    workspace_root = Path(task.workspace.root) if task.workspace is not None else None
    return {
        "schema_version": "router.plc_eval_transcript.v1",
        "generated_at": utc_now().isoformat(),
        "run": {
            "run_dir": str(run_dir),
            "execution_mode": execution_mode,
            "evaluation_profile": evaluation_profile,
            "mcp_mode": mcp_mode,
            "model": model,
        },
        "case": {
            "id": outcome.case.id,
            "message": outcome.case.message,
            "expected_route": outcome.case.expected_route,
            "route_hint": outcome.case.route_hint,
            "topic_family": outcome.case.topic_family,
            "source_theme": outcome.case.source_theme,
            "difficulty": outcome.case.difficulty,
        },
        "result": {
            "passed": outcome.passed,
            "failure_reason": outcome.failure_reason,
            "actual_status": outcome.actual_status,
            "expected_status": _expected_final_status_for_route(outcome.case.expected_route),
            "worker_sequence": list(outcome.worker_sequence),
            "expected_worker_sequence": _expected_worker_sequence_for_route(
                outcome.case.expected_route
            )
            or [],
            "artifact_types": list(outcome.artifact_types),
            "invariant_results": dict(outcome.invariant_results),
            "token_usage": dict(outcome.token_usage),
            "workflow_contract": {
                "connectivity_pass": outcome.connectivity_pass,
                "first_tool_pass": outcome.first_tool_pass,
                "required_sequence_pass": outcome.required_sequence_pass,
                "over_orchestration": outcome.over_orchestration,
                "final_status_match": outcome.final_status_match,
            },
        },
        "task": task.model_dump(mode="json"),
        "current_files": {
            "roles": _current_file_roles(task),
            "all_paths": list(task.current_files.all_paths),
            "workspace_root": str(workspace_root) if workspace_root is not None else None,
            "manifest": list(audit.workspace_files),
        },
        "main_agent": {
            "replay_log": audit.replay_log,
            "final_report": audit.final_report,
        },
        "worker_jobs": [_worker_job_payload(row) for row in audit.worker_jobs],
        "events": [_event_payload(event) for event in audit.events],
        "gate_results": [_gate_result_payload(result) for result in audit.gate_results],
        "artifacts": [_artifact_payload(artifact) for artifact in audit.artifacts],
    }


def _workspace_file_manifest(task: TaskState) -> list[dict[str, Any]]:
    if task.workspace is None:
        return []
    root = Path(task.workspace.root)
    if not root.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        files.append(
            {
                "path": rel_path,
                "size_bytes": path.stat().st_size,
                "role": _role_for_path(task, rel_path),
            }
        )
    return files


def _role_for_path(task: TaskState, path: str) -> str | None:
    for role, value in _current_file_roles(task).items():
        if value == path:
            return role
    return None


def _worker_job_payload(row: WorkerJobRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "worker_type": row.worker_type,
        "status": row.status,
        "idempotency_key": row.idempotency_key,
        "input": row.input_json,
        "result": row.result_json,
        "started_at": _iso_or_none(row.started_at),
        "completed_at": _iso_or_none(row.completed_at),
        "created_at": _iso_or_none(row.created_at),
        "updated_at": _iso_or_none(row.updated_at),
    }


def _event_payload(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    return _json_safe(event)


def _gate_result_payload(result: Any) -> dict[str, Any]:
    return {
        "id": result.id,
        "task_id": result.task_id,
        "gate_type": result.gate_type,
        "status": result.status,
        "blocking": result.blocking,
        "evidence_artifact_ids": list(result.evidence_artifact_ids),
        "result": result.result,
        "created_at": _iso_or_none(result.created_at),
    }


def _artifact_payload(artifact: Any) -> dict[str, Any]:
    if hasattr(artifact, "model_dump"):
        return artifact.model_dump(mode="json")
    return _json_safe(artifact)


def _iso_or_none(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _extract_token_usage(audit: EvalTaskAudit) -> dict[str, int]:
    """Extract cumulative Main Agent provider token usage from recorded audit data."""

    candidates: list[dict[str, Any]] = []
    replay_log = audit.replay_log if isinstance(audit.replay_log, dict) else {}
    for entry in replay_log.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if entry.get("type") == "token_usage":
            usage = payload.get("token_usage_total")
            if isinstance(usage, dict):
                candidates.append(usage)
        elif entry.get("type") == "completed":
            usage = payload.get("token_usage")
            if isinstance(usage, dict):
                candidates.append(usage)

    if not candidates:
        for event in audit.events:
            event_payload = (
                event.model_dump(mode="json")
                if hasattr(event, "model_dump")
                else event
            )
            if not isinstance(event_payload, dict):
                continue
            payload = event_payload.get("payload")
            if not isinstance(payload, dict):
                continue
            usage = payload.get("token_usage")
            if isinstance(usage, dict):
                candidates.append(usage)

    if not candidates:
        return {}
    return _normalize_token_usage(candidates[-1])


def _normalize_token_usage(usage: dict[str, Any]) -> dict[str, int]:
    input_tokens = _int_token_count(usage.get("input_tokens"))
    output_tokens = _int_token_count(usage.get("output_tokens"))
    total_tokens = _int_token_count(usage.get("total_tokens"))
    if total_tokens is None:
        parts = [value for value in (input_tokens, output_tokens) if value is not None]
        total_tokens = sum(parts) if parts else None
    payload = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def _int_token_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _evaluate_case(
    case: QuestionBankCase,
    audit: EvalTaskAudit,
    result: RuntimeRunResult,
    *,
    evaluation_profile: str,
) -> tuple[bool, dict[str, str], str | None]:
    if evaluation_profile == EVALUATION_PROFILE_SMOKE:
        return _evaluate_smoke_case(case, audit, result)
    if evaluation_profile == EVALUATION_PROFILE_WORKFLOW:
        return _evaluate_workflow_case(case, audit, result)

    expected_final = _expected_final_status_for_route(case.expected_route)
    if audit.task.status != expected_final:
        return False, {}, f"expected status {expected_final!r}, got {audit.task.status!r}"
    worker_sequence = [row.worker_type for row in audit.worker_jobs]
    expected_workers = _expected_worker_sequence_for_route(case.expected_route)
    if expected_workers is not None and worker_sequence != expected_workers:
        return False, {}, f"expected worker sequence {expected_workers!r}, got {worker_sequence!r}"
    if result.status == "error":
        return False, {}, f"runtime returned error: {result.reason}"
    invariant_results: dict[str, str] = {}
    if expected_final != "waiting_user" and audit.task.current_files.final_report is None:
        return False, invariant_results, "final report artifact is missing"
    return True, invariant_results, None


def _evaluate_workflow_case(
    case: QuestionBankCase,
    audit: EvalTaskAudit,
    result: RuntimeRunResult,
) -> tuple[bool, dict[str, str], str | None]:
    checks = _workflow_checks(case, audit)
    invariant_results = {
        key: _workflow_check_status(value)
        for key, value in checks.items()
    }
    if result.status == "error":
        return False, invariant_results, f"runtime returned error: {result.reason}"
    if result.output is not None and result.output.error_code is not None:
        return (
            False,
            invariant_results,
            f"main agent error {result.output.error_code}: {result.output.error_message}",
        )

    failures: list[str] = []
    if checks["connectivity_pass"] is False:
        failures.append("expected PLC worker jobs did not complete through the configured MCP/subagent route")
    if checks["first_tool_pass"] is False:
        failures.append("first PLC worker does not match the expected workflow entry point")
    if checks["required_sequence_pass"] is False:
        failures.append("required PLC worker sequence was not completed in order")
    if checks["over_orchestration"] is True:
        failures.append("main agent continued into extra PLC worker stages after the expected workflow")
    if checks["final_status_match"] is False:
        expected = _expected_final_status_for_route(case.expected_route)
        failures.append(f"expected final status {expected!r}, got {audit.task.status!r}")
    if (
        _expected_final_status_for_route(case.expected_route) != TaskStatus.WAITING_USER.value
        and audit.task.current_files.final_report is None
    ):
        failures.append("final report artifact is missing")

    if failures:
        return False, invariant_results, "; ".join(failures)
    return True, invariant_results, None


def _evaluate_smoke_case(
    case: QuestionBankCase,
    audit: EvalTaskAudit,
    result: RuntimeRunResult,
) -> tuple[bool, dict[str, str], str | None]:
    _ = case
    if result.status == "error":
        return False, {}, f"runtime returned error: {result.reason}"
    if result.output is not None and result.output.error_code is not None:
        return (
            False,
            {},
            f"main agent error {result.output.error_code}: {result.output.error_message}",
        )
    if audit.task.status == TaskStatus.CREATED.value:
        return False, {}, "task never left created status"
    if not audit.task.trace.main_agent_run_ids:
        return False, {}, "main agent run was not recorded"
    if not audit.events:
        return False, {}, "no runtime events were recorded"
    return True, {"live_provider_smoke": "passed"}, None


def _workflow_checks(
    case: QuestionBankCase,
    audit: EvalTaskAudit,
) -> dict[str, bool | None]:
    expected_workers = _expected_worker_sequence_for_route(case.expected_route) or []
    worker_sequence = [row.worker_type for row in audit.worker_jobs]
    healthy_worker_sequence = [
        row.worker_type
        for row in audit.worker_jobs
        if _worker_job_returned(row)
    ]
    final_status_match = audit.task.status == _expected_final_status_for_route(
        case.expected_route
    )

    if not expected_workers:
        return {
            "connectivity_pass": None,
            "first_tool_pass": not worker_sequence,
            "required_sequence_pass": not worker_sequence,
            "over_orchestration": bool(worker_sequence),
            "final_status_match": final_status_match,
        }

    required_sequence_pass = _contains_ordered_subsequence(
        worker_sequence,
        expected_workers,
    )
    return {
        "connectivity_pass": _contains_worker_multiset(
            healthy_worker_sequence,
            expected_workers,
        ),
        "first_tool_pass": bool(worker_sequence)
        and worker_sequence[0] == expected_workers[0],
        "required_sequence_pass": required_sequence_pass,
        "over_orchestration": required_sequence_pass
        and len(worker_sequence) > len(expected_workers),
        "final_status_match": final_status_match,
    }


def _worker_job_returned(row: WorkerJobRow) -> bool:
    return row.result_json is not None and row.status in {
        "completed",
        "partial",
    }


def _contains_ordered_subsequence(values: list[str], expected: list[str]) -> bool:
    if not expected:
        return not values
    cursor = 0
    for value in values:
        if value == expected[cursor]:
            cursor += 1
            if cursor >= len(expected):
                return True
    return False


def _contains_worker_multiset(values: list[str], expected: list[str]) -> bool:
    remaining = list(values)
    for expected_value in expected:
        try:
            index = remaining.index(expected_value)
        except ValueError:
            return False
        remaining.pop(index)
    return True


def _workflow_check_status(value: bool | None) -> str:
    if value is None:
        return "not_applicable"
    return "passed" if value else "failed"


def _worker_rows(session: Session, task_id: str) -> list[WorkerJobRow]:
    return list(
        session.execute(
            select(WorkerJobRow)
            .where(WorkerJobRow.task_id == task_id)
            .order_by(WorkerJobRow.created_at, WorkerJobRow.id)
        ).scalars()
    )


def select_stratified_cases(
    cases: list[QuestionBankCase],
    *,
    sample_size: int,
) -> list[QuestionBankCase]:
    """Select a deterministic route-stratified subset for live smoke runs."""

    if sample_size <= 0 or sample_size >= len(cases):
        return list(cases)

    route_order = [
        "clarify_before_dispatch",
        "qa_direct_answer",
        "dev_then_test",
        "dev_then_test_then_formal",
        "test_only_existing_code",
        "formal_only_existing_code",
        "repair_after_test_then_test",
        "repair_after_formal_then_test_then_formal",
    ]
    by_route: dict[str, list[QuestionBankCase]] = {route: [] for route in route_order}
    for case in cases:
        by_route.setdefault(case.expected_route, []).append(case)

    selected: list[QuestionBankCase] = []
    indexes = {route: 0 for route in by_route}
    while len(selected) < sample_size:
        added = False
        for route in route_order:
            items = by_route.get(route, [])
            index = indexes.get(route, 0)
            if index >= len(items):
                continue
            selected.append(items[index])
            indexes[route] = index + 1
            added = True
            if len(selected) >= sample_size:
                break
        if not added:
            break
    return selected


def _sequence_for_route(route: str) -> list[str]:
    if route == "clarify_before_dispatch":
        return []
    if route == "qa_direct_answer":
        return ["finalizing", "gate"]
    if route == "dev_then_test":
        return ["dev", "test", "finalizing", "gate"]
    if route == "dev_then_test_then_formal":
        return ["dev", "test", "formal", "finalizing", "gate"]
    if route == "test_only_existing_code":
        return ["test", "finalizing", "gate"]
    if route == "formal_only_existing_code":
        return ["formal", "finalizing", "gate"]
    if route == "repair_after_test_then_test":
        return ["test", "repair", "test", "finalizing", "gate"]
    if route == "repair_after_formal_then_test_then_formal":
        return ["test", "formal", "repair", "test", "formal", "finalizing", "gate"]
    raise ValueError(f"unsupported route: {route}")


def _expected_worker_sequence_for_route(route: str) -> list[str] | None:
    if route == "clarify_before_dispatch":
        return []
    if route == "qa_direct_answer":
        return []
    if route == "dev_then_test":
        return ["plc-dev", "plc-test"]
    if route == "dev_then_test_then_formal":
        return ["plc-dev", "plc-test", "plc-formal"]
    if route == "test_only_existing_code":
        return ["plc-test"]
    if route == "formal_only_existing_code":
        return ["plc-formal"]
    if route == "repair_after_test_then_test":
        return ["plc-test", "plc-repair", "plc-test"]
    if route == "repair_after_formal_then_test_then_formal":
        return ["plc-test", "plc-formal", "plc-repair", "plc-test", "plc-formal"]
    return None


def _expected_final_status_for_route(route: str) -> str:
    if route == "clarify_before_dispatch":
        return "waiting_user"
    if route == "repair_after_test_then_test":
        return "succeeded"
    if route == "repair_after_formal_then_test_then_formal":
        return "succeeded"
    return "succeeded"


def _plan_kwargs_for_route(route: str) -> dict[str, Any]:
    if route == "qa_direct_answer":
        return {
            "normalized_goal": "Answer directly without spawning worker routes.",
            "task_type": "qa",
            "requires_test": False,
            "requires_formal": False,
        }
    if route == "test_only_existing_code":
        return {
            "normalized_goal": "Inspect existing PLC code with a test-only route.",
            "task_type": "test_existing_code",
            "requires_test": True,
            "requires_formal": False,
        }
    if route == "formal_only_existing_code":
        return {
            "normalized_goal": "Inspect existing PLC code with a formal-only route.",
            "task_type": "formal_verify_existing_code",
            "requires_test": False,
            "requires_formal": True,
        }
    if route == "repair_after_test_then_test":
        return {
            "normalized_goal": "Repair after a failing test and rerun regression.",
            "task_type": "repair_existing_code",
            "requires_test": True,
            "requires_formal": False,
        }
    if route == "repair_after_formal_then_test_then_formal":
        return {
            "normalized_goal": "Repair after a failing formal check and rerun both validation stages.",
            "task_type": "repair_existing_code",
            "requires_test": True,
            "requires_formal": True,
        }
    if route == "dev_then_test_then_formal":
        return {
            "normalized_goal": "Create PLC code, test it, and verify it formally.",
            "task_type": "new_plc_development",
            "requires_test": True,
            "requires_formal": True,
        }
    return {
        "normalized_goal": "Create PLC code and test it.",
        "task_type": "new_plc_development",
        "requires_test": True,
        "requires_formal": False,
    }


def _mock_scenario_for_route(route: str) -> str:
    if route == "repair_after_test_then_test":
        return SCENARIO_TEST_FAILED_THEN_REPAIR_PASS
    if route == "repair_after_formal_then_test_then_formal":
        return SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS
    return SCENARIO_DEV_TEST_PASS


def _seed_context_files_for_case(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
    case: QuestionBankCase,
) -> None:
    if case.expected_route not in {
        "test_only_existing_code",
        "formal_only_existing_code",
        "repair_after_test_then_test",
        "repair_after_formal_then_test_then_formal",
    }:
        return

    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        tools = AgentToolService(
            AgentToolContext(
                session=session,
                artifact_root=settings.artifact_root,
                workspace_root=Path(task.workspace.root) if task.workspace else None,
                execution_mode="local_full_access",
                mcp_mode=settings.mcp_mode,
                mock_scenario=_mock_scenario_for_route(case.expected_route),
                checkpoint=lambda: _checkpoint_session(session),
            )
        )

        seed_dir = f".router/eval_seed/{case.id}"
        requirements_path = f"{seed_dir}/requirements_v1.json"
        code_path = f"src/eval_seed/{case.id}/plc_code_v1.st"
        _write_seed_file(
            tools,
            task_id,
            path=requirements_path,
            content=json.dumps(
                {
                    "schema_version": "router.eval_seed.v1",
                    "source_theme": case.source_theme,
                    "topic_family": case.topic_family,
                    "message": case.message,
                    "requirements": _seed_requirements(case),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        _write_seed_file(
            tools,
            task_id,
            path=code_path,
            content=_seed_code_content(case),
        )

        if case.expected_route == "repair_after_test_then_test":
            _seed_open_repair_failure(
                tools.context,
                task_id,
                source=FailureSource.TEST.value,
                evidence_paths=[
                    _write_seed_file(
                        tools,
                        task_id,
                        path=f"{seed_dir}/test_report_failed_v1.json",
                        content=json.dumps(
                            _seed_failed_test_report(case),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ),
                    _write_seed_file(
                        tools,
                        task_id,
                        path=f"{seed_dir}/failing_trace_v1.json",
                        content=json.dumps(
                            _seed_failing_trace(case),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ),
                ],
            )
        elif case.expected_route == "repair_after_formal_then_test_then_formal":
            _seed_open_repair_failure(
                tools.context,
                task_id,
                source=FailureSource.FORMAL.value,
                evidence_paths=[
                    _write_seed_file(
                        tools,
                        task_id,
                        path=f"{seed_dir}/formal_report_failed_v1.json",
                        content=json.dumps(
                            _seed_failed_formal_report(case),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ),
                    _write_seed_file(
                        tools,
                        task_id,
                        path=f"{seed_dir}/counterexample_v1.json",
                        content=json.dumps(
                            _seed_counterexample(case),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ),
                ],
            )
        session.commit()


def _checkpoint_session(session: Session) -> None:
    session.commit()
    session.expire_all()


def _write_seed_file(
    tools: AgentToolService,
    task_id: str,
    *,
    path: str,
    content: str,
) -> str:
    result = tools.write_file(
        task_id,
        path=path,
        content=content,
        create_dirs=True,
    )
    if result.status != "applied":
        raise RuntimeError(result.model_dump_json(indent=2))
    return str(result.details.get("path") or path)


def _seed_open_repair_failure(
    context: AgentToolContext,
    task_id: str,
    *,
    source: str,
    evidence_paths: list[str],
) -> None:
    repository = TaskRepository(context.session)
    task = repository.get_task(task_id)
    is_formal = source == FailureSource.FORMAL.value
    now = utc_now()
    failure = Failure(
        failure_id=prefixed_id("failure"),
        source=source,
        severity=Severity.BLOCKING,
        title=(
            "Seeded formal counterexample"
            if is_formal
            else "Seeded test regression failure"
        ),
        description=(
            "Question bank repair route starts with existing formal failure evidence."
            if is_formal
            else "Question bank repair route starts with existing failing test evidence."
        ),
        expected=(
            "EmergencyStop implies MotorRun is false."
            if is_formal
            else "MotorRun is false when EmergencyStop is true."
        ),
        actual=(
            "Counterexample keeps MotorRun true."
            if is_formal
            else "Failing trace keeps MotorRun true."
        ),
        evidence_paths=evidence_paths,
        status=FailureStatus.OPEN,
        created_by_worker_job_id="eval-seed",
        created_at=now,
    )
    gate_updates: dict[str, Any] = {
        "has_blocking_failure": True,
        "can_finish_as_success": False,
    }
    if is_formal:
        gate_updates.update(
            {
                "formal_required": True,
                "latest_formal_passed": False,
                "formal_regression_required": True,
            }
        )
    else:
        gate_updates.update(
            {
                "test_required": True,
                "latest_test_passed": False,
                "regression_required": True,
            }
        )
    repository.update_task_state(
        task.model_copy(
            deep=True,
            update={
                "gates": task.gates.model_copy(update=gate_updates),
                "failures": [*task.failures, failure],
                "updated_at": now,
            },
        )
    )
    if context.checkpoint is not None:
        context.checkpoint()


def _seed_requirements(case: QuestionBankCase) -> list[dict[str, str]]:
    return [
        {
            "id": "REQ-001",
            "text": "启动命令有效且无急停、故障时允许电机运行。",
        },
        {
            "id": "REQ-002",
            "text": "停止、急停或故障任一条件有效时必须立即断开电机输出。",
        },
        {
            "id": "REQ-003",
            "text": f"测试主题覆盖 {case.topic_family} 的基础现场逻辑。",
        },
    ]


def _seed_code_content(case: QuestionBankCase) -> str:
    return (
        "FUNCTION_BLOCK FB_MotorControl\n"
        "VAR_INPUT\n"
        "    StartCmd : BOOL;\n"
        "    StopCmd : BOOL;\n"
        "    EmergencyStop : BOOL;\n"
        "    FaultActive : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    MotorRun : BOOL;\n"
        "END_VAR\n\n"
        "IF StopCmd OR FaultActive THEN\n"
        "    MotorRun := FALSE;\n"
        "ELSIF StartCmd THEN\n"
        "    MotorRun := TRUE;\n"
        "END_IF;\n"
        "END_FUNCTION_BLOCK\n"
        f"(* eval_source_theme: {case.source_theme}; topic: {case.topic_family} *)\n"
    )


def _seed_failed_test_report(case: QuestionBankCase) -> dict[str, Any]:
    return {
        "schema_version": "router.eval_seed.v1",
        "status": "failed",
        "topic_family": case.topic_family,
        "total": 4,
        "passed": 3,
        "failed": 1,
        "failed_case": "emergency_stop_forces_motor_off",
        "summary": "EmergencyStop 有效后 MotorRun 没有被强制断开。",
    }


def _seed_failing_trace(case: QuestionBankCase) -> dict[str, Any]:
    return {
        "schema_version": "router.eval_seed.v1",
        "topic_family": case.topic_family,
        "case": "emergency_stop_forces_motor_off",
        "steps": [
            {"StartCmd": True, "EmergencyStop": False, "MotorRun": True},
            {"StartCmd": True, "EmergencyStop": True, "MotorRun": True},
        ],
        "expected": "EmergencyStop 为 TRUE 时 MotorRun 应为 FALSE。",
        "actual": "MotorRun 保持 TRUE。",
    }


def _seed_failed_formal_report(case: QuestionBankCase) -> dict[str, Any]:
    return {
        "schema_version": "router.eval_seed.v1",
        "status": "failed",
        "topic_family": case.topic_family,
        "total_properties": 3,
        "passed_properties": 2,
        "failed_properties": 1,
        "failed_property": "EmergencyStop -> NOT MotorRun",
        "summary": "急停安全性质存在反例。",
    }


def _seed_counterexample(case: QuestionBankCase) -> dict[str, Any]:
    return {
        "schema_version": "router.eval_seed.v1",
        "topic_family": case.topic_family,
        "property": "EmergencyStop -> NOT MotorRun",
        "trace": [
            {"t": 0, "StartCmd": True, "EmergencyStop": False, "MotorRun": True},
            {"t": 1, "StartCmd": True, "EmergencyStop": True, "MotorRun": True},
        ],
    }


def _output_artifact_refs(
    results: list[AgentToolResult],
) -> list[MainAgentArtifactReference]:
    refs: list[MainAgentArtifactReference] = []
    seen: set[str] = set()
    for result in results:
        for artifact_ref in result.artifact_refs:
            if artifact_ref.artifact_id in seen:
                continue
            seen.add(artifact_ref.artifact_id)
            refs.append(
                MainAgentArtifactReference.model_validate(
                    artifact_ref.model_dump(mode="json")
                )
            )
    return refs


def _run_tool(
    tools: AgentToolService,
    task_id: str,
    action: str,
) -> AgentToolResult:
    if action == "dev":
        return tools.call_plc_dev(task_id)
    if action == "test":
        return tools.call_plc_test(task_id)
    if action == "formal":
        return tools.call_plc_formal(task_id)
    if action == "repair":
        result = tools.call_plc_repair(task_id)
        if result.status == "applied":
            _promote_repaired_code_version(tools.context, task_id)
        return result
    if action == "gate":
        return tools.run_quality_gate(task_id)
    raise AssertionError(f"unknown scripted action: {action}")


def _promote_repaired_code_version(
    context: AgentToolContext,
    task_id: str,
) -> None:
    repository = TaskRepository(context.session)
    task = repository.get_task(task_id)
    code_path = task.current_files.current_code
    if code_path is None or task.workspace is None:
        return

    old_path = Path(task.workspace.root) / code_path
    if not old_path.is_file():
        return
    if code_path.endswith("_v2.st"):
        return
    if code_path.endswith("_v1.st"):
        promoted_path = code_path.removesuffix("_v1.st") + "_v2.st"
    elif code_path.endswith(".st"):
        promoted_path = code_path.removesuffix(".st") + "_v2.st"
    else:
        promoted_path = f"{code_path}_v2.st"

    target = Path(task.workspace.root) / promoted_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(old_path.read_text(encoding="utf-8"), encoding="utf-8")
    all_paths = list(task.current_files.all_paths)
    if promoted_path not in all_paths:
        all_paths.append(promoted_path)
    repository.update_task_state(
        task.model_copy(
            deep=True,
            update={
                "current_files": task.current_files.model_copy(
                    update={
                        "current_code": promoted_path,
                        "all_paths": all_paths,
                    }
                ),
                "updated_at": utc_now(),
            },
        )
    )
    if context.checkpoint is not None:
        context.checkpoint()


def _current_file_roles(task: TaskState) -> dict[str, str]:
    return {
        field_name: value
        for field_name, value in task.current_files
        if field_name != "all_paths" and value is not None
    }
