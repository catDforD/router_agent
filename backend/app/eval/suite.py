"""Executes the PLC eval question bank against the local runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

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
from app.main import create_app
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import ArtifactType, EventType, TaskState, TaskStatus
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactStore
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeRunResult, RuntimeService
from app.services.task_service import TaskService
from app.eval.question_bank import QuestionBankCase, load_question_bank_cases
from app.eval.report import EvalCaseResult, EvalRunSummary, write_eval_report


@dataclass(frozen=True)
class EvalTaskAudit:
    task: TaskState
    worker_jobs: list[WorkerJobRow]
    artifacts: list[Any]
    events: list[Any]
    gate_results: list[Any]
    final_report: dict[str, Any] | None


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

    def to_report_row(self) -> EvalCaseResult:
        return EvalCaseResult(
            case_id=self.case.id,
            passed=self.passed,
            task_id=self.task_id,
            expected_statuses=[_expected_final_status_for_route(self.case.expected_route)],
            actual_status=self.actual_status,
            worker_sequence=list(self.worker_sequence),
            artifact_types=list(self.artifact_types),
            invariant_results=dict(self.invariant_results),
            failure_reason=self.failure_reason,
        )


@dataclass(frozen=True)
class EvalRunResult:
    run_dir: Path
    summary: EvalRunSummary
    cases: list[EvalCaseOutcome]
    markdown_report_path: Path
    json_report_path: Path


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
        self._seed_context_artifacts(tools, task_id)
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

    def _seed_context_artifacts(self, tools: AgentToolService, task_id: str) -> None:
        if self.case.expected_route in {"test_only_existing_code", "formal_only_existing_code", "repair_after_test_then_test", "repair_after_formal_then_test_then_formal"}:
            tools.write_artifact(
                task_id,
                name="requirements_ir_seed.json",
                content={
                    "source": self.case.source_theme,
                    "topic_family": self.case.topic_family,
                    "message": self.case.message,
                },
                summary="Seeded requirements IR for eval route.",
                artifact_type=ArtifactType.REQUIREMENTS_IR.value,
                mime_type="application/json",
            )
            tools.write_artifact(
                task_id,
                name="plc_code_seed.st" if self.case.source_theme == "st_codesys" else "plc_code_seed.txt",
                content=_seed_code_content(self.case),
                summary="Seeded existing PLC code for eval route.",
                artifact_type=ArtifactType.PLC_CODE.value,
                mime_type="text/plain",
            )

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
) -> EvalRunResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact_root = run_dir / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    db_url = database_url or f"sqlite+pysqlite:///{run_dir / 'router_eval.db'}"
    settings = Settings(
        app_env="test",
        database_url=db_url,
        artifact_root=artifact_root,
    )
    engine = get_engine_for_url(db_url)
    Base.metadata.create_all(engine)
    session_factory = get_session_factory_for_url(db_url)
    selected_cases = cases or load_question_bank_cases()
    outcomes: list[EvalCaseOutcome] = []
    try:
        for case in selected_cases:
            outcome = _run_case(settings, session_factory, case)
            outcomes.append(outcome)
        report_rows = [outcome.to_report_row() for outcome in outcomes]
        markdown_report_path = run_dir / "report.md"
        json_report_path = run_dir / "report.json"
        write_eval_report(report_rows, markdown_report_path)
        json_report_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "total_cases": len(outcomes),
                        "passed_cases": sum(1 for outcome in outcomes if outcome.passed),
                        "failed_cases": sum(1 for outcome in outcomes if not outcome.passed),
                        "run_dir": str(run_dir),
                    },
                    "cases": [
                        {
                            "case_id": outcome.case.id,
                            "passed": outcome.passed,
                            "task_id": outcome.task_id,
                            "actual_status": outcome.actual_status,
                            "worker_sequence": outcome.worker_sequence,
                            "artifact_types": outcome.artifact_types,
                            "failure_reason": outcome.failure_reason,
                            "route": outcome.case.expected_route,
                        }
                        for outcome in outcomes
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        summary = EvalRunSummary(
            total_cases=len(outcomes),
            passed_cases=sum(1 for outcome in outcomes if outcome.passed),
            failed_cases=sum(1 for outcome in outcomes if not outcome.passed),
            result_path=str(json_report_path),
        )
        return EvalRunResult(
            run_dir=run_dir,
            summary=summary,
            cases=outcomes,
            markdown_report_path=markdown_report_path,
            json_report_path=json_report_path,
        )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _run_case(
    settings: Settings,
    session_factory: sessionmaker[Session],
    case: QuestionBankCase,
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
        runner = QuestionBankEvalRunner(case)
        runtime = RuntimeService(
            settings=settings,
            session_factory=session_factory,
            artifact_root=settings.artifact_root,
            mock_scenario=_mock_scenario_for_route(case.expected_route),
            runner=runner,
        )
        result = runtime.start_task(created.task.task_id)
        audit = _load_audit(settings, session_factory, created.task.task_id)
        passed, invariant_results, failure_reason = _evaluate_case(case, audit, result)
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
        if task.current_artifacts.final_report is not None:
            stored = ArtifactStore(
                session=session,
                artifact_root=settings.artifact_root,
            ).read_artifact_content(task.current_artifacts.final_report.artifact_id)
            final_report = json.loads(stored.content)
        return EvalTaskAudit(
            task=task,
            worker_jobs=_worker_rows(session, task_id),
            artifacts=artifacts,
            events=EventService(session).list_visible_events(task_id),
            gate_results=GateResultRepository(session).list_results(task_id),
            final_report=final_report,
        )


def _evaluate_case(
    case: QuestionBankCase,
    audit: EvalTaskAudit,
    result: RuntimeRunResult,
) -> tuple[bool, dict[str, str], str | None]:
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
    if expected_final != "waiting_user" and audit.task.current_artifacts.final_report is None:
        return False, invariant_results, "final report artifact is missing"
    return True, invariant_results, None


def _worker_rows(session: Session, task_id: str) -> list[WorkerJobRow]:
    return list(
        session.execute(
            select(WorkerJobRow)
            .where(WorkerJobRow.task_id == task_id)
            .order_by(WorkerJobRow.created_at, WorkerJobRow.id)
        ).scalars()
    )


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


def _seed_code_content(case: QuestionBankCase) -> str:
    if case.source_theme == "st_codesys":
        return (
            "FUNCTION_BLOCK FB_QuestionBankSeed\n"
            "VAR\n"
            "    SeedFlag : BOOL;\n"
            "END_VAR\n"
            "SeedFlag := TRUE;\n"
        )
    return (
        "Seeded PLC logic placeholder for existing code routes.\n"
        f"Topic: {case.topic_family}\n"
    )


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
        return tools.call_plc_repair(task_id)
    if action == "gate":
        return tools.run_quality_gate(task_id)
    raise AssertionError(f"unknown scripted action: {action}")
