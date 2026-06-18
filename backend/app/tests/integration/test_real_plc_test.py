from app.models.router_schema import ArtifactType, WorkerType

from real_mcp_helpers import (
    FakeMcpClient,
    db_session,
    dispatch_and_apply,
    draft_test_failed,
    draft_test_passed,
    real_adapter,
    task,
    worker_input,
    write_artifact,
)


def test_llm_backed_plc_test_pass_records_report(
    db_session,
    tmp_path,
    task,
) -> None:
    requirements, code = requirements_and_code(db_session, tmp_path, task.task_id)
    payload = worker_input(task, WorkerType.PLC_TEST, [requirements, code])
    client = FakeMcpClient({"plc_test.run": draft_test_passed()})

    updated = dispatch_and_apply(
        real_adapter(db_session, tmp_path, client),
        db_session,
        payload,
    )

    assert updated.current_artifacts.latest_test_report is not None
    assert updated.current_artifacts.latest_test_report.type == "test_report"
    assert updated.gates.latest_test_passed is True


def test_llm_backed_plc_test_failure_records_evidence_and_gate(
    db_session,
    tmp_path,
    task,
) -> None:
    requirements, code = requirements_and_code(db_session, tmp_path, task.task_id)
    payload = worker_input(task, WorkerType.PLC_TEST, [requirements, code])
    client = FakeMcpClient({"plc_test.run": draft_test_failed()})

    updated = dispatch_and_apply(
        real_adapter(db_session, tmp_path, client),
        db_session,
        payload,
    )

    assert updated.current_artifacts.latest_test_report is not None
    assert updated.current_artifacts.latest_failing_trace is not None
    assert updated.gates.has_blocking_failure is True
    assert len(updated.failures) == 1
    assert updated.failures[0].evidence_artifact_ids


def requirements_and_code(db_session, tmp_path, task_id: str):
    requirements = write_artifact(
        db_session,
        tmp_path,
        task_id,
        ArtifactType.REQUIREMENTS_IR,
        {"requirements": []},
        name="requirements.json",
    )
    code = write_artifact(
        db_session,
        tmp_path,
        task_id,
        ArtifactType.PLC_CODE,
        "PROGRAM Main\nEND_PROGRAM",
        name="plc_code_v1.st",
    )
    return requirements, code
