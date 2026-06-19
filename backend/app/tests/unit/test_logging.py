import logging

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import log_with_context
from app.main import create_app
import app.core.logging as logging_config


def test_startup_logging_omits_secret_values(caplog) -> None:
    secret = "sk-router-test-secret"
    settings = Settings(
        app_env="test",
        database_url="postgresql+psycopg://router:secret-password@localhost:5432/router",
        openai_api_key=secret,
    )
    logging_config._CONFIGURED = False
    caplog.set_level(logging.INFO, logger="app")

    with TestClient(create_app(settings)):
        pass

    assert "router-backend" in caplog.text
    assert "test environment" in caplog.text
    assert secret not in caplog.text
    assert "secret-password" not in caplog.text


def test_contextual_logging_includes_trace_context_and_omits_sensitive_content(
    caplog,
) -> None:
    logger = logging.getLogger("app.test")
    caplog.set_level(logging.INFO, logger="app.test")

    log_with_context(
        logger,
        logging.INFO,
        "Worker dispatch started",
        task_id="task-log-001",
        openai_trace_id="trace-log-001",
        main_agent_run_id="main-agent-run-log-001",
        worker_job_id="worker-job-log-001",
        mcp_request_id="mcp-request-log-001",
        api_key="sk-secret-value",
        database_url="postgresql://router:db-secret@localhost/router",
        plc_code="PROGRAM secret_code END_PROGRAM",
        report_body="full report body",
        artifact_content="artifact body",
    )

    assert "task-log-001" in caplog.text
    assert "trace-log-001" in caplog.text
    assert "main-agent-run-log-001" in caplog.text
    assert "worker-job-log-001" in caplog.text
    assert "mcp-request-log-001" in caplog.text
    assert "sk-secret-value" not in caplog.text
    assert "db-secret" not in caplog.text
    assert "secret_code" not in caplog.text
    assert "full report body" not in caplog.text
    assert "artifact body" not in caplog.text
