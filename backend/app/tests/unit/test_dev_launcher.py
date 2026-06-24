from pathlib import Path
import importlib.util
import socket
import sys

import pytest


ROOT = Path(__file__).resolve().parents[4]
LAUNCHER_PATH = ROOT / "main.py"


spec = importlib.util.spec_from_file_location("router_dev_launcher", LAUNCHER_PATH)
assert spec is not None
launcher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["router_dev_launcher"] = launcher
spec.loader.exec_module(launcher)


def test_parse_env_file_reads_simple_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\n"
        "DATABASE_URL='postgresql+psycopg://router:router@localhost:5432/router'\n"
        "# ignored\n",
        encoding="utf-8",
    )

    values = launcher.parse_env_file(env_file)

    assert values["APP_ENV"] == "local"
    assert values["DATABASE_URL"] == "postgresql+psycopg://router:router@localhost:5432/router"


def test_should_start_worker_respects_modes_and_flags() -> None:
    assert launcher.should_start_worker(
        mcp_mode="mock",
        worker_modes=(None, None, None, None),
        explicit_with_worker=False,
        explicit_no_worker=False,
    ) is False
    assert launcher.should_start_worker(
        mcp_mode="real",
        worker_modes=(None, None, None, None),
        explicit_with_worker=False,
        explicit_no_worker=False,
    ) is True
    assert launcher.should_start_worker(
        mcp_mode="hybrid",
        worker_modes=("mock", "real", None, None),
        explicit_with_worker=False,
        explicit_no_worker=False,
    ) is True
    assert launcher.should_start_worker(
        mcp_mode="real",
        worker_modes=(None, None, None, None),
        explicit_with_worker=False,
        explicit_no_worker=True,
    ) is False


def test_launcher_uses_local_database_by_default() -> None:
    args = launcher.parse_args([])

    config = launcher.config_from_args(args, {})

    assert config.manage_postgres is False


def test_launcher_can_manage_postgres_when_requested() -> None:
    args = launcher.parse_args(["--with-postgres", "--stop-postgres-on-exit"])

    config = launcher.config_from_args(args, {})

    assert config.manage_postgres is True
    assert config.stop_postgres_on_exit is True


def test_build_access_urls_includes_core_endpoints() -> None:
    config = launcher.LauncherConfig(
        backend_host="127.0.0.1",
        backend_port=8000,
        frontend_host="127.0.0.1",
        frontend_port=5173,
        database_url="sqlite+pysqlite:///tmp/router.db",
        artifact_root=Path("data/artifacts"),
        mcp_mode="mock",
        plc_worker_mcp_url="http://localhost:9000/mcp",
        plc_worker_modes=(None, None, None, None),
        manage_postgres=False,
        run_migrations=False,
        start_backend=True,
        start_frontend=True,
        start_worker=False,
        install_frontend_deps=False,
        dry_run=True,
        stop_postgres_on_exit=False,
    )

    urls = launcher.build_access_urls(config)

    assert urls["Frontend"] == "http://127.0.0.1:5173"
    assert urls["Health"] == "http://127.0.0.1:8000/api/health"
    assert urls["OpenAPI"] == "http://127.0.0.1:8000/docs"
    assert urls["Task SSE"] == "http://127.0.0.1:8000/api/tasks/<task_id>/events"
    assert "PLC Worker MCP" not in urls


def test_build_process_specs_respects_disabled_services() -> None:
    config = launcher.LauncherConfig(
        backend_host="127.0.0.1",
        backend_port=8000,
        frontend_host="127.0.0.1",
        frontend_port=5173,
        database_url="sqlite+pysqlite:///tmp/router.db",
        artifact_root=Path("data/artifacts"),
        mcp_mode="real",
        plc_worker_mcp_url="http://localhost:9000/mcp",
        plc_worker_modes=(None, None, None, None),
        manage_postgres=False,
        run_migrations=False,
        start_backend=False,
        start_frontend=True,
        start_worker=True,
        install_frontend_deps=False,
        dry_run=True,
        stop_postgres_on_exit=False,
    )

    specs = launcher.build_process_specs(config)

    assert [item.name for item in specs] == ["plc-worker", "frontend"]
    assert specs[0].port == 9000
    assert specs[1].port == 5173


def test_managed_port_check_rejects_existing_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        specs = [
            launcher.ProcessSpec(
                name="frontend",
                command=["npm", "run", "dev"],
                cwd=ROOT / "frontend",
                host="127.0.0.1",
                port=port,
                service="vite",
            )
        ]

        with pytest.raises(RuntimeError, match="frontend.*--no-frontend"):
            launcher.ensure_managed_ports_available(specs)
