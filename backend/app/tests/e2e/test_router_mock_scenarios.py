from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.main_agent import (
    build_main_agent_event,
    episode_output_from_task,
)
from app.agents.output_schema import (
    IntakeClassificationOutput,
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
from app.mcp.mock_worker import (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
)
from app.models.db_models import ArtifactRow, Base, GateResultRow, WorkerJobRow
from app.models.router_schema import EventType, TaskState
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactStore
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeRunResult, RuntimeService


TERMINAL_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}
GATE_TYPES = {
    "requirements_gate",
    "code_gate",
    "test_gate",
    "formal_gate",
    "regression_gate",
    "final_gate",
}


@pytest.fixture()
def e2e_context(tmp_path: Path) -> Iterator[tuple[Settings, sessionmaker[Session]]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'router-e2e.db'}"
    engine = get_engine_for_url(database_url)
    Base.metadata.create_all(engine)
    factory = get_session_factory_for_url(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
        mock_scenario=SCENARIO_DEV_TEST_PASS,
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


class ScriptedE2ERunner:
    def __init__(
        self,
        *,
        classification: IntakeClassificationOutput,
        sequence: list[str],
        final_task_status: str | None,
        expected_rejections: dict[str, str] | None = None,
    ) -> None:
        self.classification = classification
        self.sequence = sequence
        self.final_task_status = final_task_status
        self.expected_rejections = expected_rejections or {}
        self.calls: list[str] = []
        self.tool_results: list[AgentToolResult] = []

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        self.calls.append("intake")
        assert _worker_rows(context.session, run_config.group_id) == []
        task = TaskRepository(context.session).get_task(run_config.group_id)
        assert task.status == "created"
        assert task.phase == "intake"
        return self.classification

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
        for action in self.sequence:
            if action == "finalizing":
                self._emit_finalizing(context, task_id)
                continue

            result = _run_tool(tools, task_id, action)
            self.tool_results.append(result)
            expected_rejection = self.expected_rejections.get(action)
            if expected_rejection is None:
                assert result.status == "applied", result.model_dump(mode="json")
            else:
                assert result.status == "rejected", result.model_dump(mode="json")
                assert result.violation is not None
                assert result.violation.code == expected_rejection

        task = TaskRepository(context.session).get_task(task_id)
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Scripted mock E2E Router scenario completed.",
            decisions=[
                MainAgentDecision(
                    decision_type="scripted_e2e_sequence",
                    summary="Ran deterministic Router E2E tool sequence.",
                    action="finish",
                    artifact_refs=_output_artifact_refs(self.tool_results),
                    details={
                        "tools": [result.tool for result in self.tool_results],
                        "statuses": [str(result.status) for result in self.tool_results],
                    },
                )
            ],
            artifact_refs=_output_artifact_refs(self.tool_results),
        )
        if self.final_task_status is not None:
            output = output.model_copy(
                update={
                    "final_task_status": self.final_task_status,
                    "phase": (
                        "completed"
                        if self.final_task_status in TERMINAL_STATUSES
                        else task.phase
                    ),
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
                    "tool_count": len(self.tool_results),
                    "tool_names": [result.tool for result in self.tool_results],
                },
            }
        )

    def _emit_finalizing(self, context: AgentToolContext, task_id: str) -> None:
        task = TaskRepository(context.session).get_task(task_id)
        EventService(context.session).append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message="Scripted E2E runner is running Quality Gate before finish.",
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id},
                created_at=utc_now(),
            )
        )
        if context.checkpoint is not None:
            context.checkpoint()


def test_simple_development_mock_e2e_succeeds(
    e2e_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = e2e_context
    task_id = create_task_via_api(settings, scheduled_runtime)
    assert_created_task_audit(session_factory, task_id)
    runner = ScriptedE2ERunner(
        classification=classification(),
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    result = run_runtime(
        settings,
        session_factory,
        task_id,
        scenario=SCENARIO_DEV_TEST_PASS,
        runner=runner,
    )
    audit = load_audit(session_factory, task_id)

    assert result.status == "completed"
    assert runner.calls == ["intake", "orchestration"]
    assert audit.task.status == "succeeded"
    assert audit.task.phase == "completed"
    assert_worker_sequence(audit.worker_jobs, ["plc-dev", "plc-test"])
    assert_artifact_types_include(
        audit.artifacts,
        {
            "raw_user_request",
            "requirements_ir",
            "io_contract",
            "plc_code",
            "test_report",
            "gate_report",
            "final_report",
            "main_agent_log",
        },
    )
    assert audit.task.current_artifacts.current_code is not None
    assert audit.task.current_artifacts.latest_test_report is not None
    assert audit.task.current_artifacts.latest_gate_report is not None
    assert audit.task.current_artifacts.final_report is not None
    assert_gate_results(audit.gate_results, blocking=False)
    assert_event_subsequence(
        audit.events,
        [
            "task.created",
            "agent.started",
            "agent.decision",
            "task.updated",
            "worker.started",
            "artifact.created",
            "worker.completed",
            "worker.started",
            "artifact.created",
            "worker.completed",
            "agent.finalizing",
            "gate.started",
            "gate.passed",
            "agent.completed",
            "task.succeeded",
        ],
    )
    report = read_final_report(settings, session_factory, audit.task)
    assert_delivery_report_refs_current_artifacts(
        report,
        audit.task,
        expected_status="succeeded",
    )
    assert audit.task.current_artifacts.latest_test_report is not None
    assert report["delivery_artifacts"]["test_report"]["artifact_id"] == (
        audit.task.current_artifacts.latest_test_report.artifact_id
    )
    assert report["unresolved_items"]["blocking_failure_count"] == 0
    assert_monotonic_event_sequences(audit.events)


def test_test_failure_repair_mock_e2e_succeeds(
    e2e_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = e2e_context
    task_id = create_task_via_api(settings, scheduled_runtime)
    runner = ScriptedE2ERunner(
        classification=classification(),
        sequence=["dev", "test", "repair", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    result = run_runtime(
        settings,
        session_factory,
        task_id,
        scenario=SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
        runner=runner,
    )
    audit = load_audit(session_factory, task_id)

    assert result.status == "completed"
    assert audit.task.status == "succeeded"
    assert_worker_sequence(
        audit.worker_jobs,
        ["plc-dev", "plc-test", "plc-repair", "plc-test"],
    )
    assert audit.task.runtime_limits.repair_rounds == 1
    assert audit.task.gates.latest_test_passed is True
    assert audit.task.gates.regression_required is False
    assert audit.task.gates.has_blocking_failure is False
    assert [failure.status for failure in audit.task.failures] == ["resolved"]
    assert_artifact_versions(audit.artifacts, "plc_code", [1, 2])
    assert_artifact_versions(audit.artifacts, "test_report", [1, 2])
    assert_artifact_types_include(
        audit.artifacts,
        {"failing_trace", "patch", "repair_summary", "gate_report", "final_report"},
    )
    assert_gate_results(audit.gate_results, blocking=False)
    assert_event_subsequence(
        audit.events,
        [
            "worker.completed",
            "worker.started",
            "artifact.created",
            "worker.completed",
            "worker.started",
            "artifact.created",
            "worker.completed",
            "gate.passed",
            "task.succeeded",
        ],
    )
    report = read_final_report(settings, session_factory, audit.task)
    assert_delivery_report_refs_current_artifacts(
        report,
        audit.task,
        expected_status="succeeded",
    )
    assert audit.task.current_artifacts.latest_test_report is not None
    assert audit.task.current_artifacts.latest_patch is not None
    assert audit.task.current_artifacts.latest_repair_summary is not None
    assert report["delivery_artifacts"]["test_report"]["artifact_id"] == (
        audit.task.current_artifacts.latest_test_report.artifact_id
    )
    assert report["repair_summary"]["latest_patch"]["artifact_id"] == (
        audit.task.current_artifacts.latest_patch.artifact_id
    )
    assert report["repair_summary"]["latest_repair_summary"]["artifact_id"] == (
        audit.task.current_artifacts.latest_repair_summary.artifact_id
    )
    assert report["repair_summary"]["repair_rounds"] == 1
    assert_monotonic_event_sequences(audit.events)


def test_formal_failure_repair_mock_e2e_succeeds(
    e2e_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = e2e_context
    task_id = create_task_via_api(settings, scheduled_runtime)
    runner = ScriptedE2ERunner(
        classification=classification(
            difficulty_level="L3",
            difficulty_reasons=["Safety constraints require formal verification."],
            difficulty_signals=signals(has_safety_constraints=True),
            requires_formal=True,
        ),
        sequence=[
            "dev",
            "test",
            "formal",
            "repair",
            "test",
            "formal",
            "finalizing",
            "gate",
        ],
        final_task_status="succeeded",
    )

    result = run_runtime(
        settings,
        session_factory,
        task_id,
        scenario=SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
        runner=runner,
    )
    audit = load_audit(session_factory, task_id)

    assert result.status == "completed"
    assert audit.task.status == "succeeded"
    assert_worker_sequence(
        audit.worker_jobs,
        [
            "plc-dev",
            "plc-test",
            "plc-formal",
            "plc-repair",
            "plc-test",
            "plc-formal",
        ],
    )
    assert audit.task.difficulty.level == "L3"
    assert audit.task.gates.formal_required is True
    assert audit.task.runtime_limits.repair_rounds == 1
    assert audit.task.gates.latest_test_passed is True
    assert audit.task.gates.latest_formal_passed is True
    assert audit.task.gates.formal_regression_required is False
    assert audit.task.gates.has_blocking_failure is False
    assert [failure.status for failure in audit.task.failures] == ["resolved"]
    assert_artifact_versions(audit.artifacts, "plc_code", [1, 2])
    assert_artifact_versions(audit.artifacts, "formal_report", [1, 2])
    assert_artifact_types_include(
        audit.artifacts,
        {"counterexample", "patch", "repair_summary", "gate_report", "final_report"},
    )
    assert_gate_results(audit.gate_results, blocking=False)
    assert_event_subsequence(
        audit.events,
        [
            "worker.started",
            "worker.completed",
            "worker.started",
            "worker.completed",
            "worker.started",
            "worker.completed",
            "gate.passed",
            "task.succeeded",
        ],
    )
    report = read_final_report(settings, session_factory, audit.task)
    assert_delivery_report_refs_current_artifacts(
        report,
        audit.task,
        expected_status="succeeded",
    )
    assert audit.task.current_artifacts.latest_formal_report is not None
    assert audit.task.current_artifacts.latest_patch is not None
    assert audit.task.current_artifacts.latest_repair_summary is not None
    assert report["delivery_artifacts"]["formal_report"]["artifact_id"] == (
        audit.task.current_artifacts.latest_formal_report.artifact_id
    )
    assert report["repair_summary"]["latest_patch"]["artifact_id"] == (
        audit.task.current_artifacts.latest_patch.artifact_id
    )
    assert report["repair_summary"]["latest_repair_summary"]["artifact_id"] == (
        audit.task.current_artifacts.latest_repair_summary.artifact_id
    )
    assert report["repair_summary"]["repair_rounds"] == 1
    assert_monotonic_event_sequences(audit.events)


def test_clarification_mock_e2e_waits_for_user(
    e2e_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = e2e_context
    task_id = create_task_via_api(settings, scheduled_runtime)
    runner = ScriptedE2ERunner(
        classification=clarification_classification(),
        sequence=["dev"],
        final_task_status=None,
    )

    result = run_runtime(
        settings,
        session_factory,
        task_id,
        scenario=SCENARIO_DEV_TEST_PASS,
        runner=runner,
    )
    audit = load_audit(session_factory, task_id)

    assert result.status == "paused"
    assert runner.calls == ["intake"]
    assert audit.task.status == "waiting_user"
    assert audit.task.phase == "clarifying"
    assert audit.task.unresolved_questions
    assert audit.task.unresolved_questions[0].required is True
    assert audit.task.unresolved_questions[0].status == "open"
    assert audit.worker_jobs == []
    assert [artifact.type for artifact in audit.artifacts] == ["raw_user_request"]
    assert audit.gate_results == []
    assert_event_subsequence(
        audit.events,
        [
            "task.created",
            "agent.started",
            "agent.decision",
            "agent.clarification_requested",
            "task.waiting_user",
        ],
    )
    assert "worker.started" not in [event.type for event in audit.events]
    assert_monotonic_event_sequences(audit.events)


def test_repair_budget_exhaustion_mock_e2e_partial_failed(
    e2e_context: tuple[Settings, sessionmaker[Session]],
    scheduled_runtime: list[tuple[str, str, str | None]],
) -> None:
    settings, session_factory = e2e_context
    task_id = create_task_via_api(settings, scheduled_runtime)
    runner = ScriptedE2ERunner(
        classification=classification(requires_repair_loop=True),
        sequence=[
            "dev",
            "test",
            "repair",
            "test",
            "repair",
            "test",
            "repair",
            "test",
            "repair_limit_rejected",
            "finalizing",
            "gate",
        ],
        final_task_status="partial_failed",
        expected_rejections={"repair_limit_rejected": "repair_limit_reached"},
    )

    result = run_runtime(
        settings,
        session_factory,
        task_id,
        scenario=SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
        runner=runner,
    )
    audit = load_audit(session_factory, task_id)

    assert result.status == "completed"
    assert audit.task.status == "partial_failed"
    assert audit.task.phase == "completed"
    assert_worker_sequence(
        audit.worker_jobs,
        [
            "plc-dev",
            "plc-test",
            "plc-repair",
            "plc-test",
            "plc-repair",
            "plc-test",
            "plc-repair",
            "plc-test",
        ],
    )
    assert audit.task.runtime_limits.repair_rounds == 3
    assert audit.task.runtime_limits.repair_rounds == (
        audit.task.runtime_limits.max_repair_rounds
    )
    assert [row.worker_type for row in audit.worker_jobs].count("plc-repair") == 3
    assert audit.task.gates.has_blocking_failure is True
    assert any(failure.status == "open" for failure in audit.task.failures)
    assert_artifact_versions(audit.artifacts, "plc_code", [1, 2, 3, 4])
    assert_artifact_versions(audit.artifacts, "test_report", [1, 2, 3, 4])
    assert_gate_results(audit.gate_results, blocking=True)
    assert_event_subsequence(
        audit.events,
        [
            "worker.completed",
            "worker.started",
            "worker.completed",
            "worker.started",
            "worker.completed",
            "worker.started",
            "worker.completed",
            "gate.failed",
            "agent.completed",
            "task.partial_failed",
        ],
    )
    report = read_final_report(settings, session_factory, audit.task)
    assert_delivery_report_refs_current_artifacts(
        report,
        audit.task,
        expected_status="partial_failed",
    )
    assert audit.task.current_artifacts.latest_test_report is not None
    assert report["delivery_artifacts"]["test_report"]["artifact_id"] == (
        audit.task.current_artifacts.latest_test_report.artifact_id
    )
    assert report["repair_summary"]["repair_rounds"] == 3
    assert report["repair_summary"]["repair_budget_exhausted"] is True
    assert report["unresolved_items"]["blocking_failure_count"] >= 1
    assert report["unresolved_items"]["open_failures"]
    assert_monotonic_event_sequences(audit.events)


def create_task_via_api(
    settings: Settings,
    scheduled: list[tuple[str, str, str | None]],
    *,
    message: str = "Create conveyor motor logic with emergency stop validation.",
) -> str:
    payload = {
        "message": message,
        "project_context": {
            "target_plc_language": "ST",
            "target_platform": "Codesys",
        },
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
    *,
    scenario: str,
    runner: ScriptedE2ERunner,
) -> RuntimeRunResult:
    return RuntimeService(
        settings=settings,
        session_factory=session_factory,
        artifact_root=settings.artifact_root,
        mock_scenario=scenario,
        runner=runner,
    ).start_task(task_id)


class AuditSnapshot:
    def __init__(
        self,
        *,
        task: TaskState,
        worker_jobs: list[WorkerJobRow],
        artifacts: list[Any],
        events: list[Any],
        gate_results: list[Any],
    ) -> None:
        self.task = task
        self.worker_jobs = worker_jobs
        self.artifacts = artifacts
        self.events = events
        self.gate_results = gate_results


def load_audit(
    session_factory: sessionmaker[Session],
    task_id: str,
) -> AuditSnapshot:
    with session_factory() as session:
        return AuditSnapshot(
            task=TaskRepository(session).get_task(task_id),
            worker_jobs=_worker_rows(session, task_id),
            artifacts=ArtifactRepository(session).list_task_artifacts(task_id),
            events=EventService(session).list_visible_events(task_id),
            gate_results=GateResultRepository(session).list_results(task_id),
        )


def read_final_report(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task: TaskState,
) -> dict[str, Any]:
    assert task.current_artifacts.final_report is not None
    with session_factory() as session:
        stored = ArtifactStore(
            session=session,
            artifact_root=settings.artifact_root,
        ).read_artifact_content(task.current_artifacts.final_report.artifact_id)
    return json.loads(stored.content)


def assert_delivery_report_refs_current_artifacts(
    report: dict[str, Any],
    task: TaskState,
    *,
    expected_status: str,
) -> None:
    assert report["report_version"] == 1
    assert report["final_task_status"] == expected_status
    assert task.current_artifacts.current_code is not None
    assert task.current_artifacts.latest_gate_report is not None
    assert report["delivery_artifacts"]["final_plc_code"]["artifact_id"] == (
        task.current_artifacts.current_code.artifact_id
    )
    assert report["delivery_artifacts"]["gate_report"]["artifact_id"] == (
        task.current_artifacts.latest_gate_report.artifact_id
    )


def assert_created_task_audit(
    session_factory: sessionmaker[Session],
    task_id: str,
) -> None:
    audit = load_audit(session_factory, task_id)

    assert audit.task.status == "created"
    assert audit.task.phase == "intake"
    assert [artifact.type for artifact in audit.artifacts] == ["raw_user_request"]
    assert [event.type for event in audit.events] == ["task.created"]
    assert audit.worker_jobs == []
    assert audit.gate_results == []


def assert_worker_sequence(
    worker_jobs: list[WorkerJobRow],
    expected_worker_types: list[str],
) -> None:
    assert [row.worker_type for row in worker_jobs] == expected_worker_types
    assert all(row.status == "completed" for row in worker_jobs)
    assert all(row.input_json for row in worker_jobs)
    assert all(row.result_json for row in worker_jobs)


def assert_artifact_types_include(artifacts: list[Any], expected: set[str]) -> None:
    present = {str(artifact.type) for artifact in artifacts}
    assert expected <= present


def assert_artifact_versions(
    artifacts: list[Any],
    artifact_type: str,
    expected_versions: list[int],
) -> None:
    versions = sorted(
        artifact.version
        for artifact in artifacts
        if str(artifact.type) == artifact_type
    )
    assert versions == expected_versions


def assert_gate_results(gate_results: list[Any], *, blocking: bool) -> None:
    assert {result.gate_type for result in gate_results} == GATE_TYPES
    assert any(result.blocking for result in gate_results) is blocking
    expected_status = "failed" if blocking else "passed"
    final_result = next(
        result for result in gate_results if result.gate_type == "final_gate"
    )
    assert final_result.status == expected_status


def assert_event_subsequence(events: list[Any], expected: list[str]) -> None:
    event_types = [event.type for event in events]
    cursor = 0
    for event_type in expected:
        try:
            cursor = event_types.index(event_type, cursor) + 1
        except ValueError as exc:
            raise AssertionError(
                f"missing event {event_type!r} after position {cursor}; "
                f"events={event_types}"
            ) from exc


def assert_monotonic_event_sequences(events: list[Any]) -> None:
    seqs = [event.seq for event in events]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, len(seqs) + 1))


def classification(**updates: Any) -> IntakeClassificationOutput:
    values: dict[str, Any] = {
        "normalized_goal": "Create motor control PLC logic with validation.",
        "task_type": "new_plc_development",
        "difficulty_level": "L2",
        "difficulty_score": 0.55,
        "difficulty_confidence": 0.86,
        "difficulty_reasons": ["Development with validation."],
        "difficulty_signals": signals(has_io_points=True),
        "requires_test": True,
        "requires_formal": False,
        "requires_repair_loop": False,
        "need_clarification": False,
        "clarification_questions": [],
    }
    values.update(updates)
    return IntakeClassificationOutput.model_validate(values)


def clarification_classification() -> IntakeClassificationOutput:
    values = classification(
        difficulty_level="L1",
        difficulty_reasons=["Platform and I/O details are missing."],
        requires_test=False,
        requires_formal=False,
        need_clarification=True,
        clarification_questions=[
            {
                "question": "Which PLC platform and I/O names should be used?",
                "reason": "The worker needs concrete target details.",
                "required": True,
            }
        ],
    ).model_dump(mode="json")
    values["difficulty_signals"]["requirement_incomplete"] = True
    return IntakeClassificationOutput.model_validate(values)


def signals(**updates: bool) -> dict[str, bool]:
    values = {
        "has_existing_code": False,
        "has_io_points": False,
        "has_timing_logic": False,
        "has_state_machine": False,
        "has_safety_constraints": False,
        "has_emergency_stop": False,
        "has_interlock": False,
        "has_fault_latching": False,
        "has_mode_switching": False,
        "multi_module": False,
        "requirement_incomplete": False,
    }
    values.update(updates)
    return values


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
