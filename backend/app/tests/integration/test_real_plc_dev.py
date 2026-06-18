from app.models.router_schema import ArtifactType, WorkerType

from real_mcp_helpers import (
    FakeMcpClient,
    db_session,
    dispatch_and_apply,
    draft_dev,
    real_adapter,
    task,
    worker_input,
    write_artifact,
)


def test_llm_backed_plc_dev_produces_code_and_io_contract(
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
    payload = worker_input(task, WorkerType.PLC_DEV, [raw])
    client = FakeMcpClient({"plc_dev.run": draft_dev()})

    updated = dispatch_and_apply(
        real_adapter(db_session, tmp_path, client),
        db_session,
        payload,
    )

    assert updated.current_artifacts.current_code is not None
    assert updated.current_artifacts.current_code.type == "plc_code"
    assert updated.current_artifacts.current_io_contract is not None
    assert updated.current_artifacts.current_io_contract.type == "io_contract"
    assert client.calls[0][1].worker_input.worker_type == "plc-dev"
