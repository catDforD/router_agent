from app.agents.tools import AgentToolContext, AgentToolService
from app.models.router_schema import ArtifactType, WorkerType
from app.repositories.artifact_repo import ArtifactRepository

from real_mcp_helpers import (
    FakeMcpClient,
    db_session,
    dispatch_and_apply,
    draft_repair,
    draft_test_failed,
    real_adapter,
    task,
    worker_input,
    write_artifact,
)


def test_plc_repair_without_failure_is_guard_rejected(
    db_session,
    tmp_path,
    task,
) -> None:
    write_artifact(
        db_session,
        tmp_path,
        task.task_id,
        ArtifactType.PLC_CODE,
        "PROGRAM Main\nEND_PROGRAM",
        name="plc_code_v1.st",
    )
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            mcp_mode="real",
        )
    )

    result = service.call_plc_repair(task.task_id)

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "no_open_blocking_failure"


def test_llm_backed_plc_repair_updates_code_and_regression_flags(
    db_session,
    tmp_path,
    task,
) -> None:
    requirements = write_artifact(
        db_session,
        tmp_path,
        task.task_id,
        ArtifactType.REQUIREMENTS_IR,
        {"requirements": []},
        name="requirements.json",
    )
    code = write_artifact(
        db_session,
        tmp_path,
        task.task_id,
        ArtifactType.PLC_CODE,
        "PROGRAM Main\nEND_PROGRAM",
        name="plc_code_v1.st",
    )
    test_payload = worker_input(task, WorkerType.PLC_TEST, [requirements, code])
    test_client = FakeMcpClient({"plc_test.run": draft_test_failed()})
    failed_task = dispatch_and_apply(
        real_adapter(db_session, tmp_path, test_client),
        db_session,
        test_payload,
    )

    repair_artifacts = [
        artifact
        for artifact in (
            failed_task.current_artifacts.current_code,
            failed_task.current_artifacts.latest_test_report,
            failed_task.current_artifacts.latest_failing_trace,
        )
        if artifact is not None
    ]
    repair_payload = worker_input(failed_task, WorkerType.PLC_REPAIR, repair_artifacts)
    repair_client = FakeMcpClient(
        {"plc_repair.run": draft_repair(failed_task.current_artifacts.current_code.artifact_id)}
    )

    repaired = dispatch_and_apply(
        real_adapter(db_session, tmp_path, repair_client),
        db_session,
        repair_payload,
    )

    assert repaired.current_artifacts.current_code is not None
    assert repaired.current_artifacts.current_code.version == 2
    assert repaired.current_artifacts.latest_patch is not None
    assert repaired.runtime_limits.repair_rounds == 1
    assert repaired.gates.regression_required is True

    patch = ArtifactRepository(db_session).get_artifact(
        repaired.current_artifacts.latest_patch.artifact_id
    )
    assert patch.metadata.patch_metadata is not None
    assert patch.metadata.patch_metadata.from_code_artifact_id == code.artifact_id
