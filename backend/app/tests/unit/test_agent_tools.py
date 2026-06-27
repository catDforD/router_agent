import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.tools import (
    AgentToolContext,
    AgentToolService,
    ParallelWorkerRequest,
    get_main_agent_tool_specs,
    get_main_agent_tools,
)
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    CurrentArtifacts,
    DifficultyProfile,
    DifficultySignals,
    Failure,
    FailureReproduction,
    GateState,
    TaskPhase,
    TaskState,
    TaskStatus,
    WorkerType,
)
from app.mcp.mock_worker import run_mock_worker
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService
from app.services.task_service import TaskService
from app.workers.worker_input_builder import (
    WorkerInputBuildError,
    build_worker_input,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def service(db_session: Session, tmp_path: Path) -> AgentToolService:
    return AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
        )
    )


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def classified_task(
    db_session: Session,
    *,
    task_id: str = "task-agent-tools",
    qa: bool = False,
) -> TaskState:
    base = TaskState.model_validate(load_fixture("task_state.valid.json"))
    difficulty = quiet_difficulty() if qa else base.difficulty
    gates = quiet_gates() if qa else base.gates
    task = base.model_copy(
        deep=True,
        update={
            "task_id": task_id,
            "session_id": f"session-{task_id}",
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "task_type": "qa" if qa else "new_plc_development",
            "difficulty": difficulty,
            "gates": gates,
            "normalized_goal": base.raw_user_request,
            "event_seq": 0,
            "current_artifacts": CurrentArtifacts(all_artifact_ids=[]),
            "active_worker_jobs": [],
            "completed_worker_job_ids": [],
            "failures": [],
            "unresolved_questions": [],
        },
    )
    return TaskRepository(db_session).create_task(task)


def quiet_signals() -> DifficultySignals:
    return DifficultySignals(
        has_existing_code=False,
        has_io_points=False,
        has_timing_logic=False,
        has_state_machine=False,
        has_safety_constraints=False,
        has_emergency_stop=False,
        has_interlock=False,
        has_fault_latching=False,
        has_mode_switching=False,
        multi_module=False,
        requirement_incomplete=False,
    )


def quiet_difficulty() -> DifficultyProfile:
    return DifficultyProfile(
        level="L1",
        score=0.1,
        confidence=0.9,
        reasons=["QA task for agent tool test."],
        signals=quiet_signals(),
        requires_test=False,
        requires_formal=False,
        requires_repair_loop=False,
        need_clarification=False,
    )


def quiet_gates(**updates: Any) -> GateState:
    values: dict[str, Any] = {
        "test_required": False,
        "formal_required": False,
        "regression_required": False,
        "formal_regression_required": False,
        "latest_test_passed": None,
        "latest_formal_passed": None,
        "has_blocking_failure": False,
        "can_finish_as_success": False,
    }
    values.update(updates)
    return GateState(**values)


def store(db_session: Session, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(session=db_session, artifact_root=tmp_path / "artifacts")


def create_raw_artifact(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> ArtifactRef:
    artifact = store(db_session, tmp_path).write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.RAW_USER_REQUEST,
            version=1,
            name="raw_user_request.json",
            content={"message": task.raw_user_request},
            summary="Raw request for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="application/json",
        )
    ).artifact
    return store(db_session, tmp_path).get_artifact_ref(artifact.artifact_id)


def create_requirements_and_code(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> tuple[ArtifactRef, ArtifactRef]:
    artifact_store = store(db_session, tmp_path)
    raw = create_raw_artifact(db_session, tmp_path, task)
    requirements = artifact_store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.REQUIREMENTS_IR,
            version=1,
            name="requirements_ir_v1.json",
            content={"goal": task.raw_user_request},
            summary="Requirements for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            parent_artifact_ids=(raw.artifact_id,),
            mime_type="application/json",
        )
    ).artifact
    code = artifact_store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.PLC_CODE,
            version=1,
            name="plc_code_v1.st",
            content="FUNCTION_BLOCK FB_MotorControl\nMotorRun := StartCmd;\nEND_FUNCTION_BLOCK\n",
            summary="PLC code v1 for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            parent_artifact_ids=(requirements.artifact_id,),
            metadata={"code_metadata": {"code_version": 1, "is_current": True}},
            mime_type="text/plain",
        )
    ).artifact
    return (
        artifact_store.get_artifact_ref(requirements.artifact_id),
        artifact_store.get_artifact_ref(code.artifact_id),
    )


def create_failed_test_report(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
    code: ArtifactRef,
) -> ArtifactRef:
    artifact_store = store(db_session, tmp_path)
    report = artifact_store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.TEST_REPORT,
            version=1,
            name="test_report_failed.json",
            content={"status": "failed", "failed": 1},
            summary="Failed test report for agent tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            parent_artifact_ids=(code.artifact_id,),
            metadata={"test_metadata": {"status": "failed", "total": 1, "failed": 1}},
            mime_type="application/json",
        )
    ).artifact
    return artifact_store.get_artifact_ref(report.artifact_id)


def create_text_artifact(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
    *,
    content: str = "0123456789abcdef",
) -> ArtifactRef:
    artifact = store(db_session, tmp_path).write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.MISC,
            version=1,
            name="notes.txt",
            content=content,
            summary="Text artifact for read tool test.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="text/plain",
        )
    ).artifact
    return store(db_session, tmp_path).get_artifact_ref(artifact.artifact_id)


def blocking_failure(task: TaskState, evidence: ArtifactRef) -> Failure:
    return Failure(
        failure_id="failure-test-001",
        source="test",
        severity="blocking",
        title="Blocking test failure",
        description="The current PLC code failed a blocking test.",
        reproduction=FailureReproduction(input_trace_artifact_id=evidence.artifact_id),
        evidence_artifact_ids=[evidence.artifact_id],
        status="open",
        created_by_worker_job_id="worker-job-test-failed",
        created_at=task.created_at,
    )


def state_failure(
    task: TaskState,
    *,
    failure_id: str = "failure-test-001",
    status: str = "open",
) -> Failure:
    return Failure(
        failure_id=failure_id,
        source="test",
        severity="blocking",
        title="Blocking test failure",
        description="The current PLC code failed a blocking test.",
        reproduction=FailureReproduction(input_trace_path="reports/failure.json"),
        evidence_paths=["reports/failure.json"],
        status=status,
        created_by_worker_job_id="worker-job-test-failed",
        created_at=task.created_at,
    )


def worker_job_rows(db_session: Session) -> list[WorkerJobRow]:
    return list(db_session.execute(select(WorkerJobRow)).scalars())


def test_sdk_tool_list_exposes_expected_names() -> None:
    tools = get_main_agent_tools()

    assert [tool.name for tool in tools] == [
        "list_files",
        "read_file",
        "glob",
        "grep",
        "write_file",
        "apply_patch",
        "exec_command",
        "git_status",
        "read_artifact",
        "write_artifact",
        "register_workspace_file",
        "plc_dev",
        "plc_test",
        "plc_formal",
        "plc_repair",
        "run_quality_gate",
    ]
    assert [spec["function"]["name"] for spec in get_main_agent_tool_specs()] == [
        tool.name for tool in tools
    ]


def test_finish_task_is_not_exposed_to_main_agent_model() -> None:
    assert "finish_task" not in [
        spec["function"]["name"] for spec in get_main_agent_tool_specs()
    ]


def test_read_file_tool_spec_accepts_optional_mode() -> None:
    read_file_spec = next(
        spec
        for spec in get_main_agent_tool_specs()
        if spec["function"]["name"] == "read_file"
    )
    parameters = read_file_spec["function"]["parameters"]

    assert parameters["properties"]["mode"]["enum"] == ["auto", "summary", "full"]
    assert "mode" not in parameters["required"]


def test_file_tools_read_write_and_reject_foreign_paths(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
        )
    )

    written = service.write_file(
        task.task_id,
        path="src/main.txt",
        content="hello router\n",
        create_dirs=True,
    )
    listed = service.list_files(task.task_id, path=".", recursive=True)
    read = service.read_file(task.task_id, path="src/main.txt")
    rejected = service.read_file(task.task_id, path="../outside.txt")

    assert written.status == "applied"
    assert (workspace / "src/main.txt").read_text(encoding="utf-8") == "hello router\n"
    assert listed.status == "applied"
    assert any(entry["path"] == "src/main.txt" for entry in listed.details["entries"])
    assert read.details["content"] == "hello router\n"
    assert rejected.status == "rejected"
    assert rejected.violation is not None
    assert rejected.violation.code == "workspace_path_rejected"


def test_read_file_missing_path_returns_rejected_without_exception(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
        )
    )

    result = service.read_file(task.task_id, path="missing.txt")

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "file_not_found"


def test_read_file_report_auto_mode_returns_summary_not_full_body(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    report_path = workspace / ".router" / "reports" / "test_report.json"
    report_path.parent.mkdir(parents=True)
    long_error = "compile failed: " + ("x" * 2_000)
    report_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "summary": "Blocking validation failure.",
                "failed_details": [{"message": long_error}],
                "metrics": {"cases": 12, "failed": 1},
            }
        ),
        encoding="utf-8",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
        )
    )

    result = service.read_file(
        task.task_id,
        path=".router/reports/test_report.json",
    )

    assert result.status == "applied"
    assert result.read_paths == [".router/reports/test_report.json"]
    assert result.report_paths == [".router/reports/test_report.json"]
    assert result.details["format"] == "json"
    assert result.details["status"] == "failed"
    assert result.details["summary"] == "Blocking validation failure."
    assert result.details["failed_details"][0]["message"].endswith("...")
    assert len(result.details["failed_details"][0]["message"]) == 500
    assert result.details["content_omitted"] is True
    assert result.details["preview_chars"] <= 1_200
    assert result.details["refetch_hint"]
    assert "content" not in result.details


def test_read_file_full_mode_can_return_bounded_report_content(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    report_path = workspace / ".router" / "reports" / "gate_report.txt"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("gate report\n" + ("x" * 200), encoding="utf-8")
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
        )
    )

    result = service.read_file(
        task.task_id,
        path=".router/reports/gate_report.txt",
        mode="full",
        max_chars=40,
    )

    assert result.status == "applied"
    assert result.report_paths == [".router/reports/gate_report.txt"]
    assert result.details["mode"] == "full"
    assert result.details["content"].startswith("gate report\n")
    assert len(result.details["content"]) == 40
    assert result.details["content_truncated"] is True
    assert result.details["size_chars"] == 212


def test_workspace_tools_return_results_for_invalid_paths(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("hello\n", encoding="utf-8")
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
        )
    )

    listed_file = service.list_files(task.task_id, path="file.txt")
    missing_list = service.list_files(task.task_id, path="missing")
    bad_exec_cwd = service.exec_command(
        task.task_id,
        command="pwd",
        cwd="file.txt",
    )
    missing_git_cwd = service.git_status(task.task_id, cwd="missing")
    missing_patch_cwd = service.apply_patch(
        task.task_id,
        patch="",
        cwd="missing",
    )

    assert listed_file.status == "applied"
    assert listed_file.details["entries"] == [
        {"path": "file.txt", "type": "file", "size_bytes": 6}
    ]
    assert missing_list.status == "rejected"
    assert missing_list.violation is not None
    assert missing_list.violation.code == "path_not_found"
    assert bad_exec_cwd.status == "rejected"
    assert bad_exec_cwd.violation is not None
    assert bad_exec_cwd.violation.code == "cwd_not_directory"
    assert missing_git_cwd.status == "rejected"
    assert missing_git_cwd.violation is not None
    assert missing_git_cwd.violation.code == "path_not_found"
    assert missing_patch_cwd.status == "rejected"
    assert missing_patch_cwd.violation is not None
    assert missing_patch_cwd.violation.code == "path_not_found"


def test_exec_command_records_output_and_failure(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
            tool_output_max_chars=5,
        )
    )

    ok = service.exec_command(task.task_id, command="printf abcdef")
    failed = service.exec_command(task.task_id, command="printf nope >&2; exit 7")

    assert ok.status == "applied"
    assert ok.details["exit_code"] == 0
    assert ok.details["stdout"] == "ab..."
    assert ok.details["stdout_truncated"] is True
    assert ok.details["output_path"] is not None
    assert (
        workspace / ok.details["output_path"]
    ).read_text(encoding="utf-8").startswith("$ printf abcdef")
    assert ok.artifact_refs
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.details["exit_code"] == 7


def test_write_artifact_maps_code_alias_to_current_plc_code_and_rejects_unknown_type(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)

    written = service.write_artifact(
        task.task_id,
        name="motor.st",
        content="FUNCTION_BLOCK FB_Motor\nEND_FUNCTION_BLOCK\n",
        summary="PLC source from tool output.",
        artifact_type="code",
        mime_type="text/plain",
    )
    rejected = service.write_artifact(
        task.task_id,
        name="unknown.txt",
        content="x",
        summary="Unknown artifact type should be rejected.",
        artifact_type="not_a_contract_type",
    )
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert written.status == "applied"
    assert written.artifact_refs[0].type == "plc_code"
    assert updated.current_artifacts.current_code is not None
    assert (
        updated.current_artifacts.current_code.artifact_id
        == written.artifact_refs[0].artifact_id
    )
    assert rejected.status == "rejected"
    assert rejected.violation is not None
    assert rejected.violation.code == "invalid_artifact_type"


def test_register_workspace_file_registers_existing_plc_code_with_metadata(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "src" / "motor.st"
    source.parent.mkdir()
    source.write_text("FUNCTION_BLOCK FB_Motor\nEND_FUNCTION_BLOCK\n", encoding="utf-8")
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_read_only",
        )
    )

    result = service.register_workspace_file(
        task.task_id,
        path="src/motor.st",
        artifact_type="code",
        summary="Existing PLC source in the session workspace.",
        file_role="current_plc_code",
        mime_type="text/plain",
    )
    updated = TaskRepository(db_session).get_task(task.task_id)
    artifact = ArtifactRepository(db_session).get_artifact(
        result.artifact_refs[0].artifact_id
    )

    assert result.status == "applied"
    assert result.details["path"] == "src/motor.st"
    assert result.artifact_refs[0].type == "plc_code"
    assert updated.current_artifacts.current_code is not None
    assert (
        updated.current_artifacts.current_code.artifact_id
        == result.artifact_refs[0].artifact_id
    )
    assert artifact.metadata.workspace_path == "src/motor.st"
    assert artifact.metadata.file_role == "current_plc_code"
    assert artifact.metadata.source_task_id == task.task_id


def test_apply_patch_modifies_workspace_file(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, qa=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess_run = __import__("subprocess").run
    subprocess_run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    target = workspace / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    patch = """diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=workspace,
            execution_mode="local_full_access",
        )
    )

    result = service.apply_patch(task.task_id, patch=patch)

    assert result.status == "applied"
    assert target.read_text(encoding="utf-8") == "new\n"
    assert result.artifact_refs


def test_update_plan_normalizes_provider_task_type_alias(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)

    result = service.update_plan(
        task.task_id,
        summary="Provider supplied a non-contract task type alias.",
        task_type="L0_development",
    )
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "applied"
    assert updated.task_type == "new_plc_development"


def test_worker_input_builder_selects_validator_inputs(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    current = TaskRepository(db_session).get_task(task.task_id)

    payload = build_worker_input(current, WorkerType.PLC_TEST)

    assert payload.worker_type == "plc-test"
    assert payload.mode == "test"
    assert [artifact.artifact_id for artifact in payload.input_artifacts] == [
        requirements.artifact_id,
        code.artifact_id,
    ]
    assert payload.context.user_goal == current.normalized_goal
    assert payload.context.repair_round == 0


def test_worker_input_builder_rejects_missing_repair_evidence(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)
    current = TaskRepository(db_session).get_task(task.task_id)

    with pytest.raises(WorkerInputBuildError):
        build_worker_input(current, WorkerType.PLC_REPAIR)


def test_plc_dev_invokes_mock_worker_and_returns_compact_refs(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_raw_artifact(db_session, tmp_path, task)

    result = service.plc_dev(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "applied"
    assert result.worker_type == "plc-dev"
    assert result.artifact_refs
    assert result.artifact is None
    assert updated.current_artifacts.current_code is not None
    assert updated.active_worker_jobs == []
    assert updated.runtime_limits.active_parallel_workers == 0
    assert updated.runtime_limits.worker_calls_used == 1
    assert result.worker_job_id in updated.completed_worker_job_ids


def test_plc_dev_propagates_direct_worker_config_into_worker_job_input(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_raw_artifact(db_session, tmp_path, task)

    result = service.plc_dev(
        task.task_id,
        target_language="FBD",
        compiler_type="matiec",
        llm={
            "model": "deepseek-worker",
            "base_url": "https://deepseek.example/v1",
            "temperature": 0.2,
            "timeout_seconds": 30,
            "max_retries": 2,
        },
    )
    job = WorkerJobRepository(db_session).get_job(result.worker_job_id)

    assert result.status == "applied"
    assert job.input.worker_config is not None
    assert job.input.worker_config.target_language == "FBD"
    assert job.input.worker_config.compiler_type == "matiec"
    assert job.input.worker_config.llm is not None
    assert job.input.worker_config.llm.model == "deepseek-worker"
    assert job.input.worker_config.llm.timeout_seconds == 30


def test_plc_test_propagates_direct_worker_config_into_worker_job_input(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)

    result = service.plc_test(
        task.task_id,
        fuzz_method="llm",
        case_count=7,
        enable_fuzz_test=False,
        llm={"model": "test-worker"},
    )
    job = WorkerJobRepository(db_session).get_job(result.worker_job_id)

    assert result.status == "applied"
    assert job.input.worker_config is not None
    assert job.input.worker_config.fuzz_method == "llm"
    assert job.input.worker_config.case_count == 7
    assert job.input.worker_config.enable_fuzz_test is False
    assert job.input.worker_config.llm is not None
    assert job.input.worker_config.llm.model == "test-worker"


def test_plc_formal_propagates_direct_worker_config_into_worker_job_input(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)
    properties = {"must_hold": ["emergency_stop_forces_motor_off"]}

    result = service.plc_formal(
        task.task_id,
        compiler_type="rusty",
        properties=properties,
        natural_language_requirements="Emergency stop must force motor off.",
        llm={"temperature": 0},
    )
    job = WorkerJobRepository(db_session).get_job(result.worker_job_id)

    assert result.status == "applied"
    assert job.input.worker_config is not None
    assert job.input.worker_config.compiler_type == "rusty"
    assert job.input.worker_config.properties == properties
    assert (
        job.input.worker_config.natural_language_requirements
        == "Emergency stop must force motor off."
    )
    assert job.input.worker_config.llm is not None
    assert job.input.worker_config.llm.temperature == 0


def test_plc_repair_propagates_direct_worker_config_into_worker_job_input(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    _requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    failed_report = create_failed_test_report(db_session, tmp_path, task, code)
    current = TaskRepository(db_session).get_task(task.task_id)
    current = current.model_copy(
        update={"failures": [blocking_failure(current, failed_report)]}
    )
    TaskRepository(db_session).update_task_state(current)

    result = service.plc_repair(
        task.task_id,
        repair_source="test_failure",
        repair_targets=["test_failure"],
        repair_failure_notes="Repair the failed emergency stop case.",
        compiler_type="rusty",
        llm={"max_retries": 1},
    )
    job = WorkerJobRepository(db_session).get_job(result.worker_job_id)

    assert result.status == "applied"
    assert job.input.worker_config is not None
    assert job.input.worker_config.repair_source == "test_failure"
    assert job.input.worker_config.repair_targets == ["test_failure"]
    assert (
        job.input.worker_config.repair_failure_notes
        == "Repair the failed emergency stop case."
    )
    assert job.input.worker_config.compiler_type == "rusty"
    assert job.input.worker_config.llm is not None
    assert job.input.worker_config.llm.max_retries == 1


def test_worker_input_builder_uses_string_failure_sources_for_repair_defaults(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    _requirements, code = create_requirements_and_code(db_session, tmp_path, task)
    failed_report = create_failed_test_report(db_session, tmp_path, task, code)
    current = TaskRepository(db_session).get_task(task.task_id)
    current = current.model_copy(
        update={
            "failures": [blocking_failure(current, failed_report)],
        }
    )
    current = TaskRepository(db_session).update_task_state(current)

    worker_input = build_worker_input(current, WorkerType.PLC_REPAIR)

    assert worker_input.worker_config is not None
    assert worker_input.worker_config.repair_source == "test_failure"
    assert worker_input.worker_config.repair_targets == ["test_failure"]
    assert worker_input.worker_config.repair_failure_notes is not None
    assert "failure-test-001" in worker_input.worker_config.repair_failure_notes


def test_mock_plc_dev_uses_fbd_artifact_details_when_target_language_is_fbd(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    create_raw_artifact(db_session, tmp_path, task)
    current = TaskRepository(db_session).get_task(task.task_id)
    worker_input = build_worker_input(
        current,
        WorkerType.PLC_DEV,
        worker_config={"target_language": "FBD"},
    )

    output = run_mock_worker(worker_input)
    code_write = next(
        write for write in output.artifact_writes if write.artifact_type == ArtifactType.PLC_CODE
    )

    assert code_write.name == "plc_code_v1.fbd"
    assert code_write.mime_type == "application/xml"
    assert code_write.summary == "Mock FBD implementation."


def test_plc_dev_prepares_intake_task_for_domain_worker(
    db_session: Session,
    tmp_path: Path,
) -> None:
    created = TaskService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).create_task(
        message="Generate a conveyor start stop controller.",
        project_context={
            "target_plc_language": "ST",
            "target_platform": "Codesys",
        },
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
        )
    )

    result = service.plc_dev(
        created.task.task_id,
        objective="Generate conveyor start stop ST code.",
    )
    updated = TaskRepository(db_session).get_task(created.task.task_id)
    events = EventService(db_session).list_visible_events(created.task.task_id)

    assert result.status == "applied"
    assert result.worker_type == "plc-dev"
    assert updated.task_type == "new_plc_development"
    assert updated.phase == "planning"
    assert updated.status == "running"
    assert updated.current_artifacts.current_code is not None
    assert updated.runtime_limits.worker_calls_used == 1
    assert "task.updated" in [event.type for event in events]
    assert "worker.started" in [event.type for event in events]
    assert "worker.completed" in [event.type for event in events]


def test_worker_tool_rationale_is_observable_without_changing_worker_input(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    create_raw_artifact(db_session, tmp_path, task)
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
        openai_trace_id="trace-001",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.plc_dev(
        task.task_id,
        objective="Generate motor control code.",
        rationale_summary="No current code exists, so start with PLC development.",
    )
    events = EventService(db_session).list_visible_events(task.task_id)
    job = worker_job_rows(db_session)[0]

    assert result.status == "applied"
    assert "agent.tool_called" in [event.type for event in events]
    assert "agent.tool_result" in [event.type for event in events]
    tool_call = next(event for event in events if event.type == "agent.tool_called")
    assert tool_call.payload["tool_name"] == "plc_dev"
    assert (
        tool_call.payload["rationale_summary"]
        == "No current code exists, so start with PLC development."
    )
    assert job.input_json["objective"] == "Generate motor control code."
    assert "rationale_summary" not in job.input_json["metadata"]


def test_plc_test_without_current_code_is_rejected_without_side_effects(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)

    result = service.plc_test(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "missing_current_code"
    assert updated.runtime_limits.worker_calls_used == 0
    assert updated.active_worker_jobs == []
    assert worker_job_rows(db_session) == []


def test_plc_test_repeated_same_input_failure_is_debounced(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            mock_scenario="test_failed_then_repair_pass",
        )
    )

    first = service.plc_test(task.task_id)
    second = service.plc_test(task.task_id)
    third = service.plc_test(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert first.status == "applied"
    assert second.status == "applied"
    assert third.status == "rejected"
    assert third.violation is not None
    assert third.violation.code == "worker_retry_debounce"
    assert third.violation.details["blocked_failure_types"] == ["test"]
    assert updated.runtime_limits.worker_calls_used == 2


def test_guard_rejection_is_recorded_when_observability_is_enabled(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.plc_test(
        task.task_id,
        rationale_summary="Testing is required before final delivery.",
    )
    events = EventService(db_session).list_visible_events(task.task_id)
    tool_result = next(event for event in events if event.type == "agent.tool_result")

    assert result.status == "rejected"
    assert [event.type for event in events] == [
        "agent.turn_started",
        "agent.tool_called",
        "agent.tool_result",
    ]
    assert tool_result.payload["status"] == "rejected"
    assert tool_result.payload["details"]["violation"]["code"] == "missing_current_code"
    assert worker_job_rows(db_session) == []


def test_plc_repair_without_failure_is_rejected_without_side_effects(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    create_requirements_and_code(db_session, tmp_path, task)

    result = service.plc_repair(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "no_open_blocking_failure"
    assert updated.runtime_limits.worker_calls_used == 0
    assert updated.active_worker_jobs == []
    assert worker_job_rows(db_session) == []


def test_run_parallel_workers_rejects_invalid_batch_atomically(
    db_session: Session,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)

    result = service.run_parallel_workers(
        task.task_id,
        [
            ParallelWorkerRequest(worker_type="plc-test"),
            ParallelWorkerRequest(worker_type="plc-formal"),
        ],
    )
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "missing_current_code"
    assert updated.runtime_limits.worker_calls_used == 0
    assert updated.active_worker_jobs == []
    assert worker_job_rows(db_session) == []


def test_read_artifact_summary_and_bounded_full_modes(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session)
    artifact = create_text_artifact(db_session, tmp_path, task)

    summary = service.read_artifact(task.task_id, artifact.artifact_id, mode="summary")
    full = service.read_artifact(
        task.task_id,
        artifact.artifact_id,
        mode="full",
        max_chars=5,
    )

    assert summary.status == "applied"
    assert summary.artifact is not None
    assert summary.artifact.content is None
    assert full.artifact is not None
    assert full.artifact.content == "01234"
    assert full.artifact.content_truncated is True
    assert full.artifact.content_chars == 5


def test_read_artifact_records_observable_call_and_result(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session)
    artifact = create_text_artifact(db_session, tmp_path, task)
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.read_artifact(task.task_id, artifact.artifact_id, mode="summary")
    events = EventService(db_session).list_visible_events(task.task_id)

    assert result.status == "applied"
    assert [event.type for event in events] == [
        "agent.turn_started",
        "agent.tool_called",
        "agent.tool_result",
    ]
    assert events[1].payload["tool_name"] == "read_artifact"
    assert events[2].payload["tool_name"] == "read_artifact"
    assert events[2].payload["artifact_ids"] == [artifact.artifact_id]


def test_read_artifact_rejects_foreign_task_artifact(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    first = classified_task(db_session, task_id="task-first")
    second = classified_task(db_session, task_id="task-second")
    foreign = create_text_artifact(db_session, tmp_path, second)

    result = service.read_artifact(first.task_id, foreign.artifact_id, mode="full")

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "foreign_artifact"
    assert result.artifact is None


def test_run_quality_gate_returns_assessment_and_gate_report(
    db_session: Session,
    tmp_path: Path,
    service: AgentToolService,
) -> None:
    task = classified_task(db_session, qa=True)
    create_raw_artifact(db_session, tmp_path, task)

    result = service.run_quality_gate(task.task_id)

    assert result.status == "applied"
    assert result.details["assessment_status"] == "passed"
    assert result.details["blocking"] is False
    assert result.artifact_refs[0].type == "gate_report"
    assert result.gate_state is not None
    assert result.gate_state.can_finish_as_success is True


def test_quality_gate_passed_does_not_correlate_historical_failures(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, task_id="task-gate-passed", qa=True)
    historical_failure = state_failure(task, status="resolved")
    TaskRepository(db_session).update_task_state(
        task.model_copy(deep=True, update={"failures": [historical_failure]})
    )
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-gate-passed",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.run_quality_gate(task.task_id)
    event = [
        event
        for event in EventService(db_session).list_visible_events(task.task_id)
        if event.type == "agent.tool_result"
    ][-1]

    assert result.details["assessment_status"] == "passed"
    assert event.payload["failure_ids"] == []
    assert event.correlation.failure_ids is None


def test_quality_gate_failed_correlates_open_blocking_failures(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = classified_task(db_session, task_id="task-gate-failed", qa=True)
    failure = state_failure(task)
    TaskRepository(db_session).update_task_state(
        task.model_copy(
            deep=True,
            update={
                "failures": [failure],
                "gates": task.gates.model_copy(update={"has_blocking_failure": True}),
            },
        )
    )
    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-gate-failed",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            observability_recorder=recorder,
        )
    )

    result = service.run_quality_gate(task.task_id)
    event = [
        event
        for event in EventService(db_session).list_visible_events(task.task_id)
        if event.type == "agent.tool_result"
    ][-1]

    assert result.details["assessment_status"] == "failed"
    assert event.payload["failure_ids"] == ["failure-test-001"]
    assert event.correlation.failure_ids == ["failure-test-001"]


def test_tool_checkpoint_callback_runs_after_gate_and_report(
    db_session: Session,
    tmp_path: Path,
) -> None:
    checkpoints: list[str] = []
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            checkpoint=lambda: checkpoints.append("checkpoint"),
        )
    )
    task = classified_task(db_session, qa=True)
    create_raw_artifact(db_session, tmp_path, task)

    service.run_quality_gate(task.task_id)
    service.write_final_report(
        task.task_id,
        final_status="succeeded",
        summary="QA task passed Quality Gate.",
    )

    assert len(checkpoints) >= 2
    assert set(checkpoints) == {"checkpoint"}
