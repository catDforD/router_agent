from app.models.router_schema import ArtifactType, WorkerType

from real_mcp_helpers import (
    FakeMcpClient,
    db_session,
    draft_dev,
    real_adapter,
    task,
    worker_input,
    write_artifact,
)


def test_hybrid_mode_can_mix_real_and_mock_workers(
    db_session,
    tmp_path,
    task,
) -> None:
    raw = write_artifact(
        db_session,
        tmp_path,
        task.task_id,
        ArtifactType.RAW_USER_REQUEST,
        task.raw_user_request,
    )
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
    client = FakeMcpClient({"plc_dev.run": draft_dev()})
    adapter = real_adapter(
        db_session,
        tmp_path,
        client,
        mcp_mode="hybrid",
        plc_dev_mode="real",
        plc_test_mode="mock",
    )

    dev_result = adapter.call_worker(worker_input(task, WorkerType.PLC_DEV, [raw]))
    test_result = adapter.call_worker(
        worker_input(task, WorkerType.PLC_TEST, [requirements, code])
    )

    assert len(client.calls) == 1
    assert client.calls[0][0] == "plc_dev.run"
    assert {artifact.type for artifact in dev_result.produced_artifacts} == {
        "requirements_ir",
        "plc_code",
        "io_contract",
    }
    assert {artifact.type for artifact in test_result.produced_artifacts} == {"test_report"}
