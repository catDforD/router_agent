import logging

from fastapi.testclient import TestClient

from app.core.config import Settings
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
