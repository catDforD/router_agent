from types import SimpleNamespace

import httpx

from app.api import subagents
from app.core.config import Settings
from app.main import create_app


def test_subagent_status_reports_configured_remote_workers(
    monkeypatch,
) -> None:
    settings = Settings(
        app_env="test",
        mcp_mode="subagent",
        subagent_api_base_url="http://subagent.example",
        plc_dev_mode="subagent",
        plc_test_mode="subagent",
        plc_formal_mode="subagent",
        plc_repair_mode="subagent",
    )

    monkeypatch.setattr(
        subagents,
        "probe_remote_subagent",
        lambda _settings: {
            "status": "online",
            "online": True,
            "latency_ms": 12,
            "status_code": 405,
            "error": None,
            "scope": "transport_reachability",
            "checked_at": "2026-06-26T00:00:00+00:00",
        },
    )

    payload = subagents.subagent_status(fake_request(settings))

    assert payload["mode"] == "subagent"
    assert payload["base_url"] == "http://subagent.example"
    assert payload["probe"]["status"] == "online"
    assert payload["probe"]["scope"] == "transport_reachability"
    assert [worker["worker_type"] for worker in payload["workers"]] == [
        "plc-dev",
        "plc-test",
        "plc-formal",
        "plc-repair",
    ]
    assert {worker["route"] for worker in payload["workers"]} == {"subagent"}
    assert {worker["online"] for worker in payload["workers"]} == {True}
    assert {worker["probe_scope"] for worker in payload["workers"]} == {
        "transport_reachability"
    }


def test_subagent_status_keeps_non_subagent_routes_disabled(
    monkeypatch,
) -> None:
    settings = Settings(
        app_env="test",
        mcp_mode="hybrid",
        plc_dev_mode="subagent",
        plc_test_mode="mock",
        plc_formal_mode="real",
        plc_repair_mode="mock",
    )

    monkeypatch.setattr(
        subagents,
        "probe_remote_subagent",
        lambda _settings: {
            "status": "timeout",
            "online": False,
            "latency_ms": 2001,
            "status_code": None,
            "error": "Remote subagent probe timed out",
            "scope": "transport_reachability",
            "checked_at": "2026-06-26T00:00:00+00:00",
        },
    )

    workers = {
        worker["worker_type"]: worker
        for worker in subagents.subagent_status(fake_request(settings))["workers"]
    }
    assert workers["plc-dev"]["status"] == "timeout"
    assert workers["plc-dev"]["online"] is False
    assert workers["plc-dev"]["probe_scope"] == "transport_reachability"
    assert workers["plc-test"]["status"] == "mock"
    assert workers["plc-test"]["online"] is None
    assert workers["plc-test"]["probe_scope"] is None
    assert workers["plc-formal"]["status"] == "real"
    assert workers["plc-formal"]["online"] is None


def test_probe_remote_subagent_marks_timeout_offline(monkeypatch) -> None:
    def request(*args, **kwargs) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    settings = Settings(
        mcp_mode="subagent",
        subagent_api_base_url="http://subagent.example",
    )

    monkeypatch.setattr(subagents.httpx, "request", request)

    probe = subagents.probe_remote_subagent(settings)

    assert probe["status"] == "timeout"
    assert probe["online"] is False
    assert probe["status_code"] is None
    assert probe["scope"] == "transport_reachability"


def test_subagent_status_route_is_registered() -> None:
    settings = Settings(app_env="test")
    app = create_app(settings)

    paths = {
        getattr(subroute, "path_format", None)
        for route in app.routes
        for subroute in getattr(getattr(route, "original_router", None), "routes", [])
    }

    assert "/api/subagents/status" in paths


def fake_request(settings: Settings) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
    )
