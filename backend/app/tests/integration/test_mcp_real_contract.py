import pytest

from app.mcp.draft import McpDraftValidationError, validate_worker_draft_output
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


def test_real_mcp_contract_discovers_tools() -> None:
    client = FakeMcpClient()

    assert client.list_tools() == [
        "plc_dev.run",
        "plc_test.run",
        "plc_formal.run",
        "plc_repair.run",
    ]


def test_real_mcp_contract_dispatches_valid_worker_input(
    db_session,
    tmp_path,
    task,
) -> None:
    client = FakeMcpClient({"plc_dev.run": draft_dev()})
    raw = write_artifact(
        db_session,
        tmp_path,
        task.task_id,
        ArtifactType.RAW_USER_REQUEST,
        task.raw_user_request,
    )
    payload = worker_input(task, WorkerType.PLC_DEV, [raw])

    result = real_adapter(db_session, tmp_path, client).call_worker(payload)

    assert result.execution_status == "completed"
    assert client.calls[0][0] == "plc_dev.run"
    assert client.calls[0][1].worker_input.worker_job_id == payload.worker_job_id
    assert client.calls[0][1].input_artifacts[0].content is not None


def test_real_mcp_contract_rejects_invalid_draft(
    task,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_DEV,
        [
            write_artifact_placeholder(
                ArtifactType.RAW_USER_REQUEST,
            )
        ],
    )

    with pytest.raises(McpDraftValidationError):
        validate_worker_draft_output(
            FakeMcpClient({"plc_dev.run": draft_dev()}).drafts["plc_dev.run"].model_copy(
                update={"artifact_writes": []}
            ),
            payload,
        )


def write_artifact_placeholder(artifact_type: ArtifactType):
    from app.models.router_schema import ArtifactRef

    return ArtifactRef(
        artifact_id=f"artifact-{artifact_type.value}",
        type=artifact_type,
        version=1,
        uri=f"local://artifacts/{artifact_type.value}",
        summary=f"{artifact_type.value} artifact",
    )
