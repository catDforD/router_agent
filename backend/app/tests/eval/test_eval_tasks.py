from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.main_agent import build_main_agent_event, episode_output_from_task
from app.agents.output_schema import (
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentPlanStep,
)
from app.agents.tools import AgentToolContext, AgentToolResult, AgentToolService
from app.api import tasks as tasks_api
from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.core.time import utc_now
from app.main import create_app
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import EventType, TaskState, TaskStatus
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactStore
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeRunResult, RuntimeService
from app.tests.eval.eval_cases import (
    EvalCase,
    EvalCaseValidationError,
    load_eval_cases,
    parse_eval_cases_text,
)
from app.tests.eval.eval_report import EvalCaseResult, render_eval_report, write_eval_report


EVAL_CASES = load_eval_cases()
EVAL_RESULTS: list[EvalCaseResult] = []
TERMINAL_EVENTS = {
    "succeeded": "task.succeeded",
    "partial_failed": "task.partial_failed",
    "failed": "task.failed",
    "cancelled": "task.cancelled",
}
TERMINAL_STATUSES = set(TERMINAL_EVENTS)
GATE_TYPES = {
    "requirements_gate",
    "code_gate",
    "test_gate",
    "formal_gate",
    "regression_gate",
    "final_gate",
}


@pytest.fixture(scope="session", autouse=True)
def eval_report_writer() -> Iterator[None]:
    EVAL_RESULTS.clear()
    yield
    if EVAL_RESULTS:
        report_path = Path(os.environ.get("ROUTER_EVAL_REPORT_PATH", "eval_report.md"))
        write_eval_report(EVAL_RESULTS, report_path)


@pytest.fixture()
def eval_context(tmp_path: Path) -> Iterator[tuple[Settings, sessionmaker[Session]]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'router-eval.db'}"
    engine = get_engine_for_url(database_url)
    Base.metadata.create_all(engine)
    factory = get_session_factory_for_url(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
    )
    try:
        yield settings, factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_engine_for_url.cache_clear()
        get_session_factory_for_url.cache_clear()


@pytest.fixture(autouse=True)
def scheduled_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, str | None]]:
    scheduled: list[tuple[str, str, str | None]] = []

    def fake_start(task_id: str, settings: Settings | None = None) -> None:
        scheduled.append(
            ("start", task_id, settings.database_url if settings is not None else None)
        )

    def fake_resume(task_id: str, settings: Settings | None = None) -> None:
        scheduled.append(
            ("resume", task_id, settings.database_url if settings is not None else None)
        )

    monkeypatch.setattr(tasks_api, "run_runtime_start_task", fake_start)
    monkeypatch.setattr(tasks_api, "run_runtime_resume_task", fake_resume)
    return scheduled


class EvalScriptedRunner:
    def __init__(self, case: EvalCase) -> None:
        self.case = case
        self.calls: list[str] = []
        self.tool_results: list[AgentToolResult] = []
        self.failures: list[str] = []
        self.uses_tool_loop_side_effects = False

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
        if _worker_rows(context.session, task_id):
            self.failures.append("worker jobs existed before task was prepared")
        task = TaskRepository(context.session).get_task(task_id)
        if task.status != "created" or task.phase != "intake":
            self.failures.append(
                f"unexpected initial task state: status={task.status}, phase={task.phase}"
            )
        if TaskStatus.WAITING_USER.value in self.case.expected.final_status:
            self.uses_tool_loop_side_effects = True
            result = tools.request_clarification(
                task_id,
                questions=[
                    {
                        "question": "Which PLC platform and I/O names should be used?",
                        "reason": "The worker needs concrete target details.",
                        "required": True,
                    }
                ],
                rationale_summary=f"Eval case {self.case.id} needs clarification.",
            )
            self._record_tool_expectation("request_clarification", result)
            task = TaskRepository(context.session).get_task(task_id)
            return episode_output_from_task(
                task,
                main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
                summary=f"Deterministic eval case {self.case.id} paused.",
            )

        plan_result = tools.update_plan(
            task_id,
            summary=f"Deterministic eval case {self.case.id} prepared task.",
            plan=[{"order": 1, "action": "run scripted eval sequence"}],
            **self.case.plan_config(),
        )
        if plan_result.status != "applied":
            self.failures.append(
                f"update_plan expected applied but got {plan_result.status}"
            )
        for action in self.case.scripted_sequence:
            if action == "finalizing":
                self._emit_finalizing(context, task_id)
                continue

            result = _run_tool(tools, task_id, action)
            self.tool_results.append(result)
            self._record_tool_expectation(action, result)

        task = TaskRepository(context.session).get_task(task_id)
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary=f"Deterministic eval case {self.case.id} completed.",
            decisions=[
                MainAgentDecision(
                    decision_type="eval_scripted_sequence",
                    summary="Ran deterministic backend eval tool sequence.",
                    action="finish",
                    artifact_refs=_output_artifact_refs(self.tool_results),
                    details={
                        "case_id": self.case.id,
                        "tools": [result.tool for result in self.tool_results],
                        "statuses": [str(result.status) for result in self.tool_results],
                    },
                )
            ],
            artifact_refs=_output_artifact_refs(self.tool_results),
        )
        final_status = self.case.runner_final_status()
        if final_status is not None:
            output = output.model_copy(
                update={
                    "final_task_status": final_status,
                    "phase": "completed"
                    if final_status in TERMINAL_STATUSES
                    else task.phase,
                    "next_recommended_action": "none",
                }
            )
        return output.model_copy(
            update={
                "plan": [
                    MainAgentPlanStep(
                        order=index,
                        action=result.tool,
                        status=str(result.status),
                        tool_name=result.tool,
                        worker_type=result.worker_type,
                    )
                    for index, result in enumerate(self.tool_results, start=1)
                ],
                "metadata": {
                    "case_id": self.case.id,
                    "tool_count": len(self.tool_results),
                    "tool_names": [result.tool for result in self.tool_results],
                },
            }
        )

    def _record_tool_expectation(self, action: str, result: AgentToolResult) -> None:
        expected_rejection = self.case.expected.expected_rejections.get(action)
        if expected_rejection is not None:
            if result.status != "rejected":
                self.failures.append(
                    f"{action} expected rejected but got {result.status}"
                )
            elif result.violation is None or result.violation.code != expected_rejection:
                self.failures.append(
                    f"{action} expected rejection {expected_rejection!r} but got "
                    f"{result.violation.code if result.violation else None!r}"
                )
            return

        expected_status = self.case.expected.expected_tool_statuses.get(
            action,
            "applied",
        )
        if result.status != expected_status:
            self.failures.append(
                f"{action} expected tool status {expected_status!r} but got "
                f"{result.status!r}"
            )

        expected_execution = self.case.expected.expected_execution_statuses.get(action)
        if expected_execution is not None and result.execution_status != expected_execution:
            self.failures.append(
                f"{action} expected execution_status {expected_execution!r} but got "
                f"{result.execution_status!r}"
            )

    def _emit_finalizing(self, context: AgentToolContext, task_id: str) -> None:
        task = TaskRepository(context.session).get_task(task_id)
        EventService(context.session).append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message="Backend eval runner is running Quality Gate before finish.",
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id, "case_id": self.case.id},
                created_at=utc_now(),
            )
        )
        if context.checkpoint is not None:
            context.checkpoint()


@dataclass(frozen=True)
class AuditSnapshot:
    task: TaskState
    worker_jobs: list[WorkerJobRow]
    artifacts: list[Any]
    events: list[Any]
    gate_results: list[Any]
    final_report: dict[str, Any] | None


@pytest.mark.parametrize("case", EVAL_CASES, ids=[case.id for case in EVAL_CASES])
def test_backend_eval_case(
    case: EvalCase,
    eval_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = eval_context
    runner = EvalScriptedRunner(case)
    task_id: str | None = None
    audit: AuditSnapshot | None = None
    invariant_results: dict[str, str] = {}

    try:
        task_id = create_task_via_api(settings, scheduled_runtime, case=case)
        assert_created_task_audit(session_factory, task_id, case)
        result = run_runtime(settings, session_factory, task_id, case, runner)
        audit = load_audit(settings, session_factory, task_id)
        if runner.failures:
            raise_eval_failure(case, audit, "; ".join(runner.failures))
        invariant_results = assert_eval_expectations(case, audit, result)
    except Exception as exc:
        if task_id is not None and audit is None:
            audit = _try_load_audit(settings, session_factory, task_id)
        EVAL_RESULTS.append(
            result_record(
                case,
                audit,
                passed=False,
                invariant_results=invariant_results,
                failure_reason=str(exc),
            )
        )
        if isinstance(exc, EvalAssertionFailure):
            raise
        try:
            raise_eval_failure(case, audit, str(exc))
        except EvalAssertionFailure as wrapped:
            raise wrapped from exc

    EVAL_RESULTS.append(
        result_record(
            case,
            audit,
            passed=True,
            invariant_results=invariant_results,
        )
    )


def test_default_eval_corpus_loads_representative_cases() -> None:
    cases = load_eval_cases()
    assert len(cases) >= 15
    assert {case.id for case in cases} >= {
        "qa_st_explain",
        "motor_estop",
        "test_failure_repair",
        "formal_counterexample_repair",
        "repair_budget_exhausted",
        "worker_timeout_visible",
    }


def test_eval_case_loader_rejects_duplicate_ids() -> None:
    payload = {"cases": [_minimal_case("duplicate"), _minimal_case("duplicate")]}
    with pytest.raises(EvalCaseValidationError, match="duplicate eval case ids"):
        parse_eval_cases_text(json.dumps(payload), validate_corpus=False)


def test_eval_case_loader_rejects_invalid_enum_value() -> None:
    invalid = _minimal_case("invalid_enum")
    invalid["expected"]["required_workers"] = ["plc-missing"]
    with pytest.raises(EvalCaseValidationError, match="required_workers"):
        parse_eval_cases_text(json.dumps({"cases": [invalid]}), validate_corpus=False)


def test_eval_case_loader_rejects_missing_required_field() -> None:
    invalid = _minimal_case("missing_message")
    del invalid["message"]
    with pytest.raises(EvalCaseValidationError, match="message"):
        parse_eval_cases_text(json.dumps({"cases": [invalid]}), validate_corpus=False)


def test_eval_report_writer_includes_cases_and_failure_diagnostics(tmp_path: Path) -> None:
    report_path = tmp_path / "eval_report.md"
    write_eval_report(
        [
            EvalCaseResult(
                case_id="passing_case",
                passed=True,
                task_id="task-pass",
                expected_statuses=["succeeded"],
                actual_status="succeeded",
                worker_sequence=["plc-dev", "plc-test"],
                artifact_types=["raw_user_request", "final_report"],
                invariant_results={"no_success_without_quality_gate": "passed"},
            ),
            EvalCaseResult(
                case_id="failing_case",
                passed=False,
                task_id="task-fail",
                expected_statuses=["succeeded"],
                actual_status="running",
                worker_sequence=["plc-dev"],
                artifact_types=["raw_user_request"],
                invariant_results={"no_false_success_on_worker_error": "failed"},
                failure_reason="case failed with bounded diagnostics",
            ),
        ],
        report_path,
    )

    content = report_path.read_text(encoding="utf-8")
    assert "passing_case" in content
    assert "failing_case" in content
    assert "case failed with bounded diagnostics" in content
    assert "FUNCTION_BLOCK" not in content


@pytest.mark.skipif(
    os.environ.get("ROUTER_LIVE_EVAL") != "1",
    reason="live provider eval is opt-in via ROUTER_LIVE_EVAL=1",
)
@pytest.mark.parametrize("case", EVAL_CASES, ids=[case.id for case in EVAL_CASES])
def test_live_provider_eval_scaffold(case: EvalCase) -> None:
    has_main_agent_key = os.environ.get("MAIN_AGENT_API_KEY") or os.environ.get(
        "OPENAI_API_KEY"
    )
    if not has_main_agent_key or not os.environ.get("MAIN_AGENT_MODEL"):
        pytest.skip(
            "live provider eval requires MAIN_AGENT_API_KEY or OPENAI_API_KEY, "
            "plus MAIN_AGENT_MODEL"
        )
    pytest.skip(
        "live provider eval reuses the fixed corpus but is deferred until the "
        "provider runner implementation is selected"
    )


def create_task_via_api(
    settings: Settings,
    scheduled: list[tuple[str, str, str | None]],
    *,
    case: EvalCase,
) -> str:
    payload = {
        "message": case.message,
        "project_context": {
            "target_plc_language": "ST",
            "target_platform": "Codesys",
        }
        | case.project_context,
    }
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/tasks", json=payload)

    assert response.status_code == 201
    body = response.json()
    task_id = body["task_id"]
    assert body["events_url"] == f"/api/tasks/{task_id}/events"
    assert scheduled == [("start", task_id, settings.database_url)]
    return task_id


def run_runtime(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
    case: EvalCase,
    runner: EvalScriptedRunner,
) -> RuntimeRunResult:
    return RuntimeService(
        settings=settings,
        session_factory=session_factory,
        artifact_root=settings.artifact_root,
        mock_scenario=case.mock_scenario,
        runner=runner,
    ).start_task(task_id)


def assert_created_task_audit(
    session_factory: sessionmaker[Session],
    task_id: str,
    case: EvalCase,
) -> None:
    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        artifacts = ArtifactRepository(session).list_task_artifacts(task_id)
        events = EventService(session).list_visible_events(task_id)
    if task.status != "created" or task.phase != "intake":
        raise_eval_failure(case, None, "created task did not start in intake")
    if [artifact.type for artifact in artifacts] != ["raw_user_request"]:
        raise_eval_failure(case, None, "raw user request artifact was not created")
    if [event.type for event in events] != ["task.created"]:
        raise_eval_failure(case, None, "task.created event was not emitted")


def load_audit(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
) -> AuditSnapshot:
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
        return AuditSnapshot(
            task=task,
            worker_jobs=_worker_rows(session, task_id),
            artifacts=artifacts,
            events=EventService(session).list_visible_events(task_id),
            gate_results=GateResultRepository(session).list_results(task_id),
            final_report=final_report,
        )


def _try_load_audit(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
) -> AuditSnapshot | None:
    try:
        return load_audit(settings, session_factory, task_id)
    except Exception:
        return None


def assert_eval_expectations(
    case: EvalCase,
    audit: AuditSnapshot,
    result: RuntimeRunResult,
) -> dict[str, str]:
    expected = case.expected
    if audit.task.status not in expected.final_status:
        raise_eval_failure(
            case,
            audit,
            f"expected task status in {expected.final_status}, got {audit.task.status}",
        )

    worker_sequence = [row.worker_type for row in audit.worker_jobs]
    if expected.worker_sequence is not None and worker_sequence != expected.worker_sequence:
        raise_eval_failure(
            case,
            audit,
            f"expected worker sequence {expected.worker_sequence}, got {worker_sequence}",
        )
    for worker in expected.required_workers:
        if worker not in worker_sequence:
            raise_eval_failure(case, audit, f"missing required worker {worker!r}")
    for worker in expected.forbidden_workers:
        if worker in worker_sequence:
            raise_eval_failure(case, audit, f"forbidden worker ran: {worker!r}")

    artifact_types = [str(artifact.type) for artifact in audit.artifacts]
    for artifact_type in expected.required_artifacts:
        if artifact_type not in artifact_types:
            raise_eval_failure(
                case,
                audit,
                f"missing required artifact type {artifact_type!r}",
            )
    for artifact_type, versions in expected.artifact_versions.items():
        actual_versions = sorted(
            artifact.version
            for artifact in audit.artifacts
            if str(artifact.type) == artifact_type
        )
        if actual_versions != versions:
            raise_eval_failure(
                case,
                audit,
                f"expected {artifact_type} versions {versions}, got {actual_versions}",
            )

    assert_event_subsequence(case, audit, expected.required_event_subsequence)
    assert_monotonic_event_sequences(case, audit)
    assert_final_report(case, audit)

    invariant_results: dict[str, str] = {}
    for invariant in expected.invariants:
        try:
            INVARIANTS[invariant](case, audit)
        except Exception as exc:
            invariant_results[invariant] = "failed"
            try:
                raise_eval_failure(case, audit, str(exc), invariant=invariant)
            except EvalAssertionFailure as wrapped:
                raise wrapped from exc
        invariant_results[invariant] = "passed"

    if result.status == "error":
        raise_eval_failure(case, audit, f"runtime returned error: {result.reason}")
    return invariant_results


def assert_event_subsequence(
    case: EvalCase,
    audit: AuditSnapshot,
    expected: list[str],
) -> None:
    event_types = [event.type for event in audit.events]
    cursor = 0
    for event_type in expected:
        try:
            cursor = event_types.index(event_type, cursor) + 1
        except ValueError as exc:
            try:
                raise_eval_failure(
                    case,
                    audit,
                    f"missing event {event_type!r} after position {cursor}; "
                    f"events={event_types}",
                )
            except EvalAssertionFailure as wrapped:
                raise wrapped from exc


def assert_monotonic_event_sequences(case: EvalCase, audit: AuditSnapshot) -> None:
    seqs = [event.seq for event in audit.events]
    if seqs != sorted(seqs) or seqs != list(range(1, len(seqs) + 1)):
        raise_eval_failure(case, audit, f"event seqs are not monotonic: {seqs}")


def assert_final_report(case: EvalCase, audit: AuditSnapshot) -> None:
    if audit.task.status == "waiting_user":
        if audit.final_report is not None:
            raise_eval_failure(case, audit, "waiting_user task should not have report")
        return
    if "final_report" in case.expected.required_artifacts and audit.final_report is None:
        raise_eval_failure(case, audit, "final report artifact is missing")
    if audit.final_report is None:
        return

    report = audit.final_report
    if report["final_task_status"] != audit.task.status:
        raise_eval_failure(
            case,
            audit,
            "final report status does not match task status: "
            f"{report['final_task_status']} != {audit.task.status}",
        )
    if audit.task.current_artifacts.current_code is not None:
        code_ref = report["delivery_artifacts"]["final_plc_code"]
        if code_ref["artifact_id"] != audit.task.current_artifacts.current_code.artifact_id:
            raise_eval_failure(case, audit, "final report does not reference code")
    if audit.task.current_artifacts.latest_gate_report is not None:
        gate_ref = report["delivery_artifacts"]["gate_report"]
        if gate_ref["artifact_id"] != audit.task.current_artifacts.latest_gate_report.artifact_id:
            raise_eval_failure(case, audit, "final report does not reference gate report")

    serialized = json.dumps(report, ensure_ascii=False)
    forbidden_snippets = [
        "FUNCTION_BLOCK FB_MotorControl",
        "--- plc_code_v1.st",
        "+++ plc_code_v2.st",
        "EmergencyStop -> NOT MotorRun",
        "emergency_stop_forces_motor_off",
    ]
    embedded = [snippet for snippet in forbidden_snippets if snippet in serialized]
    if embedded:
        raise_eval_failure(
            case,
            audit,
            f"final report embeds large artifact content snippets: {embedded}",
        )


def invariant_l3_requires_formal(case: EvalCase, audit: AuditSnapshot) -> None:
    if audit.task.status != "succeeded":
        return
    worker_sequence = [row.worker_type for row in audit.worker_jobs]
    if "plc-formal" not in worker_sequence:
        raise AssertionError("successful L3/formal case skipped plc-formal")
    if audit.task.gates.formal_required is not True:
        raise AssertionError("formal_required gate was not set")
    if audit.task.gates.latest_formal_passed is not True:
        raise AssertionError("latest formal evidence did not pass")


def invariant_repair_requires_regression(case: EvalCase, audit: AuditSnapshot) -> None:
    worker_sequence = [row.worker_type for row in audit.worker_jobs]
    repair_indexes = [
        index for index, worker in enumerate(worker_sequence) if worker == "plc-repair"
    ]
    for index in repair_indexes:
        if "plc-test" not in worker_sequence[index + 1 :]:
            raise AssertionError("repair was not followed by regression plc-test")
    if audit.task.status == "succeeded" and audit.task.gates.regression_required:
        raise AssertionError("terminal success left regression_required=true")


def invariant_formal_repair_requires_formal_regression(
    case: EvalCase,
    audit: AuditSnapshot,
) -> None:
    worker_sequence = [row.worker_type for row in audit.worker_jobs]
    if "plc-repair" not in worker_sequence or "plc-formal" not in worker_sequence:
        return
    repair_index = worker_sequence.index("plc-repair")
    if "plc-formal" in worker_sequence[:repair_index]:
        if "plc-formal" not in worker_sequence[repair_index + 1 :]:
            raise AssertionError("formal repair was not followed by formal regression")
    if audit.task.status == "succeeded" and audit.task.gates.formal_regression_required:
        raise AssertionError("terminal success left formal_regression_required=true")


def invariant_no_success_without_quality_gate(case: EvalCase, audit: AuditSnapshot) -> None:
    if audit.task.status != "succeeded":
        return
    if {result.gate_type for result in audit.gate_results} != GATE_TYPES:
        raise AssertionError("Quality Gate did not persist all gate result types")
    final_gate = next(
        result for result in audit.gate_results if result.gate_type == "final_gate"
    )
    if final_gate.status != "passed":
        raise AssertionError("final gate did not pass before success")
    assert_ordered_events(audit, "gate.passed", "task.succeeded")


def invariant_final_report_before_terminal_event(
    case: EvalCase,
    audit: AuditSnapshot,
) -> None:
    if audit.task.status not in TERMINAL_EVENTS:
        return
    if audit.task.current_artifacts.final_report is None:
        raise AssertionError("terminal task has no final report artifact")
    assert_ordered_events(audit, "agent.completed", TERMINAL_EVENTS[audit.task.status])


def invariant_no_worker_for_clarification(case: EvalCase, audit: AuditSnapshot) -> None:
    if audit.task.status != "waiting_user":
        raise AssertionError("clarification case did not pause as waiting_user")
    if not audit.task.unresolved_questions:
        raise AssertionError("clarification case has no unresolved questions")
    if audit.worker_jobs:
        raise AssertionError("clarification case created worker jobs")
    worker_events = [event.type for event in audit.events if event.type.startswith("worker.")]
    if worker_events:
        raise AssertionError(f"clarification case emitted worker events: {worker_events}")


def invariant_no_fourth_repair_round(case: EvalCase, audit: AuditSnapshot) -> None:
    repair_count = [row.worker_type for row in audit.worker_jobs].count("plc-repair")
    if repair_count > audit.task.runtime_limits.max_repair_rounds:
        raise AssertionError("created a repair job beyond max_repair_rounds")
    if audit.task.runtime_limits.repair_rounds > audit.task.runtime_limits.max_repair_rounds:
        raise AssertionError("repair_rounds exceeded max_repair_rounds")


def invariant_no_false_success_on_worker_error(case: EvalCase, audit: AuditSnapshot) -> None:
    if audit.task.status == "succeeded":
        raise AssertionError("worker error case was marked succeeded")
    event_types = [event.type for event in audit.events]
    if "worker.timeout" not in event_types and "worker.error" not in event_types:
        raise AssertionError("worker error case has no worker.timeout or worker.error")
    if "task.succeeded" in event_types:
        raise AssertionError("worker error case emitted task.succeeded")


INVARIANTS = {
    "l3_requires_formal": invariant_l3_requires_formal,
    "repair_requires_regression": invariant_repair_requires_regression,
    "formal_repair_requires_formal_regression": (
        invariant_formal_repair_requires_formal_regression
    ),
    "no_success_without_quality_gate": invariant_no_success_without_quality_gate,
    "final_report_before_terminal_event": invariant_final_report_before_terminal_event,
    "no_worker_for_clarification": invariant_no_worker_for_clarification,
    "no_fourth_repair_round": invariant_no_fourth_repair_round,
    "no_false_success_on_worker_error": invariant_no_false_success_on_worker_error,
}


class EvalAssertionFailure(AssertionError):
    pass


def raise_eval_failure(
    case: EvalCase,
    audit: AuditSnapshot | None,
    message: str,
    *,
    invariant: str | None = None,
) -> None:
    task_id = audit.task.task_id if audit is not None else "unknown"
    status = audit.task.status if audit is not None else "unknown"
    workers = (
        [row.worker_type for row in audit.worker_jobs]
        if audit is not None
        else []
    )
    invariant_part = f", invariant={invariant}" if invariant is not None else ""
    raise EvalAssertionFailure(
        f"case={case.id}, task_id={task_id}, actual_status={status}, "
        f"workers={workers}{invariant_part}: {message}"
    )


def result_record(
    case: EvalCase,
    audit: AuditSnapshot | None,
    *,
    passed: bool,
    invariant_results: dict[str, str],
    failure_reason: str | None = None,
) -> EvalCaseResult:
    return EvalCaseResult(
        case_id=case.id,
        passed=passed,
        task_id=audit.task.task_id if audit is not None else None,
        expected_statuses=[str(status) for status in case.expected.final_status],
        actual_status=audit.task.status if audit is not None else None,
        worker_sequence=(
            [row.worker_type for row in audit.worker_jobs]
            if audit is not None
            else []
        ),
        artifact_types=(
            [str(artifact.type) for artifact in audit.artifacts]
            if audit is not None
            else []
        ),
        invariant_results=dict(invariant_results),
        failure_reason=failure_reason,
    )


def assert_ordered_events(audit: AuditSnapshot, before: str, after: str) -> None:
    event_types = [event.type for event in audit.events]
    if before not in event_types:
        raise AssertionError(f"missing event {before!r}")
    if after not in event_types:
        raise AssertionError(f"missing event {after!r}")
    if event_types.index(before) >= event_types.index(after):
        raise AssertionError(f"event {before!r} does not precede {after!r}")


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
    if action in {"repair", "repair_limit_rejected"}:
        return tools.call_plc_repair(task_id)
    if action == "gate":
        return tools.run_quality_gate(task_id)
    raise AssertionError(f"unknown scripted action: {action}")


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


def _worker_rows(session: Session, task_id: str) -> list[WorkerJobRow]:
    return list(
        session.execute(
            select(WorkerJobRow)
            .where(WorkerJobRow.task_id == task_id)
            .order_by(WorkerJobRow.created_at, WorkerJobRow.id)
        ).scalars()
    )


def _minimal_case(case_id: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "title": "Minimal eval case",
        "message": "Create motor control logic.",
        "scripted_sequence": ["dev", "test", "finalizing", "gate"],
        "expected": {
            "final_status": ["succeeded"],
            "required_workers": ["plc-dev", "plc-test"],
            "required_artifacts": ["raw_user_request", "final_report"],
        },
    }
