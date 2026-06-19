"""Local development launcher for the Router stack.

Run from the repository root:

    uv run main.py
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_HOST = "127.0.0.1"
DEFAULT_FRONTEND_PORT = 5173
DEFAULT_MCP_URL = "http://localhost:9000/mcp"


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    command: list[str]
    cwd: Path
    port: int | None = None
    service: str | None = None


@dataclass
class ManagedProcess:
    spec: ProcessSpec
    process: subprocess.Popen[str] | None = None
    status: str = "pending"

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None


@dataclass(frozen=True)
class LauncherConfig:
    backend_host: str
    backend_port: int
    frontend_host: str
    frontend_port: int
    database_url: str
    artifact_root: Path
    mcp_mode: str
    plc_worker_mcp_url: str
    plc_worker_modes: tuple[str | None, str | None, str | None, str | None]
    manage_postgres: bool
    run_migrations: bool
    start_backend: bool
    start_frontend: bool
    start_worker: bool
    install_frontend_deps: bool
    dry_run: bool
    stop_postgres_on_exit: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Router stack.")
    parser.add_argument("--backend-host", default=DEFAULT_BACKEND_HOST)
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--frontend-host", default=DEFAULT_FRONTEND_HOST)
    parser.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT)
    parser.add_argument("--no-postgres", action="store_true", help="Do not start Docker Compose PostgreSQL.")
    parser.add_argument("--stop-postgres-on-exit", action="store_true", help="Stop the Docker Compose PostgreSQL service on shutdown.")
    parser.add_argument("--no-migrate", action="store_true", help="Skip Alembic migration execution.")
    parser.add_argument("--no-backend", action="store_true", help="Do not start the backend API process.")
    parser.add_argument("--no-frontend", action="store_true", help="Do not start the frontend dev server.")
    parser.add_argument("--with-worker", action="store_true", help="Start the local PLC worker MCP server.")
    parser.add_argument("--no-worker", action="store_true", help="Never start the local PLC worker MCP server.")
    parser.add_argument("--install-frontend-deps", action="store_true", help="Run npm install when frontend/node_modules is missing.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned process and URL summary without starting processes.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    env = merged_environment()
    args = parse_args(argv)
    config = config_from_args(args, env)

    managed: list[ManagedProcess] = []
    stop_event = threading.Event()

    def request_shutdown(signum: int, _frame: object) -> None:
        print(f"\nReceived signal {signum}; shutting down.")
        stop_event.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    try:
        prepare_runtime(config)
        specs = build_process_specs(config)
        managed = [ManagedProcess(spec=spec) for spec in specs]

        if config.dry_run:
            print_process_table(managed)
            print_access_urls(build_access_urls(config))
            print("\nDry run complete; no child processes started.")
            managed = []
            return 0

        if config.manage_postgres:
            start_postgres(config)
        else:
            verify_database(config)
        if config.run_migrations:
            run_checked([sys.executable, "-m", "alembic", "upgrade", "head"], ROOT, env)
        ensure_frontend_dependencies(config, env)

        print_process_table(managed)
        print_access_urls(build_access_urls(config))

        for item in managed:
            start_process(item, env)

        wait_for_ready(config)
        print("\nRouter development stack is ready.")
        print_process_table(managed)
        print_access_urls(build_access_urls(config))

        while not stop_event.is_set():
            for item in managed:
                if item.process is not None and item.process.poll() is not None:
                    item.status = f"exited {item.process.returncode}"
                    print(f"\nProcess {item.spec.name} exited with {item.process.returncode}.")
                    stop_event.set()
                    break
            time.sleep(0.5)
        return 0
    except Exception as exc:
        print(f"\nStartup failed: {exc}", file=sys.stderr)
        if managed:
            print_process_table(managed)
        return 1
    finally:
        shutdown_processes(managed)
        if config.stop_postgres_on_exit and config.manage_postgres:
            run_unchecked(["docker", "compose", "stop", "postgres"], ROOT, env)
        if managed:
            print("\nShutdown summary:")
            print_process_table(managed)


def merged_environment() -> dict[str, str]:
    env = parse_env_file(ROOT / ".env")
    env.update(os.environ)
    return env


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def config_from_args(args: argparse.Namespace, env: dict[str, str]) -> LauncherConfig:
    mcp_mode = env.get("MCP_MODE", "mock").lower()
    worker_modes = (
        normalized_optional(env.get("PLC_DEV_MODE")),
        normalized_optional(env.get("PLC_TEST_MODE")),
        normalized_optional(env.get("PLC_FORMAL_MODE")),
        normalized_optional(env.get("PLC_REPAIR_MODE")),
    )
    start_worker = should_start_worker(
        mcp_mode=mcp_mode,
        worker_modes=worker_modes,
        explicit_with_worker=args.with_worker,
        explicit_no_worker=args.no_worker,
    )
    artifact_root = Path(env.get("ARTIFACT_ROOT", "./data/artifacts"))
    if not artifact_root.is_absolute():
        artifact_root = ROOT / artifact_root

    return LauncherConfig(
        backend_host=args.backend_host,
        backend_port=args.backend_port,
        frontend_host=args.frontend_host,
        frontend_port=args.frontend_port,
        database_url=env.get(
            "DATABASE_URL",
            "postgresql+psycopg://router:router@localhost:5432/router",
        ),
        artifact_root=artifact_root,
        mcp_mode=mcp_mode,
        plc_worker_mcp_url=env.get("PLC_WORKER_MCP_URL", DEFAULT_MCP_URL),
        plc_worker_modes=worker_modes,
        manage_postgres=not args.no_postgres,
        run_migrations=not args.no_migrate,
        start_backend=not args.no_backend,
        start_frontend=not args.no_frontend,
        start_worker=start_worker,
        install_frontend_deps=args.install_frontend_deps,
        dry_run=args.dry_run,
        stop_postgres_on_exit=args.stop_postgres_on_exit,
    )


def normalized_optional(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized or None


def should_start_worker(
    *,
    mcp_mode: str,
    worker_modes: Iterable[str | None],
    explicit_with_worker: bool,
    explicit_no_worker: bool,
) -> bool:
    if explicit_no_worker:
        return False
    if explicit_with_worker:
        return True
    if mcp_mode == "real":
        return True
    return mcp_mode == "hybrid" and any(mode == "real" for mode in worker_modes)


def prepare_runtime(config: LauncherConfig) -> None:
    config.artifact_root.mkdir(parents=True, exist_ok=True)


def start_postgres(config: LauncherConfig) -> None:
    if shutil.which("docker") is None:
        raise RuntimeError(
            "docker is required for managed PostgreSQL; "
            "use --no-postgres to use an existing database"
        )
    print("Starting PostgreSQL through Docker Compose...")
    run_checked(["docker", "compose", "up", "-d", "postgres"], ROOT, os.environ.copy())
    verify_database(config)


def verify_database(config: LauncherConfig) -> None:
    parsed = urlparse(config.database_url)
    if parsed.scheme.startswith("sqlite"):
        db_path = Path(parsed.path)
        if db_path.parent:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        return
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"database is not reachable at {host}:{port}")


def ensure_frontend_dependencies(config: LauncherConfig, env: dict[str, str]) -> None:
    if not config.start_frontend:
        return
    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.exists():
        return
    if config.install_frontend_deps:
        run_checked(["npm", "install"], FRONTEND_DIR, env)
        return
    raise RuntimeError(
        "frontend dependencies are missing. Run `cd frontend && npm install` "
        "or restart with `uv run main.py --install-frontend-deps`."
    )


def build_process_specs(config: LauncherConfig) -> list[ProcessSpec]:
    specs: list[ProcessSpec] = []
    if config.start_worker:
        specs.append(
            ProcessSpec(
                name="plc-worker",
                command=[sys.executable, "scripts/start_plc_worker_mcp_server.py"],
                cwd=ROOT,
                port=urlparse(config.plc_worker_mcp_url).port or 9000,
                service="mcp",
            )
        )
    if config.start_backend:
        specs.append(
            ProcessSpec(
                name="backend",
                command=[
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--app-dir",
                    "backend",
                    "--host",
                    config.backend_host,
                    "--port",
                    str(config.backend_port),
                ],
                cwd=ROOT,
                port=config.backend_port,
                service="api",
            )
        )
    if config.start_frontend:
        specs.append(
            ProcessSpec(
                name="frontend",
                command=[
                    "npm",
                    "run",
                    "dev",
                    "--",
                    "--host",
                    config.frontend_host,
                    "--port",
                    str(config.frontend_port),
                ],
                cwd=FRONTEND_DIR,
                port=config.frontend_port,
                service="vite",
            )
        )
    return specs


def start_process(item: ManagedProcess, env: dict[str, str]) -> None:
    item.process = subprocess.Popen(
        item.spec.command,
        cwd=item.spec.cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    item.status = "running"
    thread = threading.Thread(target=pipe_logs, args=(item,), daemon=True)
    thread.start()


def pipe_logs(item: ManagedProcess) -> None:
    if item.process is None or item.process.stdout is None:
        return
    for line in item.process.stdout:
        print(f"[{item.spec.name}] {line}", end="")


def wait_for_ready(config: LauncherConfig) -> None:
    if config.start_backend:
        wait_http(f"http://{config.backend_host}:{config.backend_port}/api/health")
    if config.start_frontend:
        wait_http(f"http://{config.frontend_host}:{config.frontend_port}/")


def wait_http(url: str, timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - local dev URL
                if 200 <= response.status < 500:
                    return
        except URLError:
            time.sleep(0.5)
    raise RuntimeError(f"service did not become ready: {url}")


def build_access_urls(config: LauncherConfig) -> dict[str, str]:
    backend = f"http://{config.backend_host}:{config.backend_port}"
    frontend = f"http://{config.frontend_host}:{config.frontend_port}"
    urls = {
        "Frontend": frontend,
        "Backend": backend,
        "Health": f"{backend}/api/health",
        "OpenAPI": f"{backend}/docs",
        "Task API": f"{backend}/api/tasks",
        "Task SSE": f"{backend}/api/tasks/<task_id>/events",
    }
    if config.start_worker:
        urls["PLC Worker MCP"] = config.plc_worker_mcp_url
    return urls


def print_access_urls(urls: dict[str, str]) -> None:
    print("\nAccess URLs")
    for label, url in urls.items():
        print(f"  {label:<15} {url}")


def print_process_table(processes: list[ManagedProcess]) -> None:
    print("\nProcesses")
    print(f"  {'Name':<12} {'PID':<8} {'Port/Service':<14} {'Status':<12} Command")
    for item in processes:
        print(
            f"  {item.spec.name:<12} {str(item.pid or '-'): <8} "
            f"{str(item.spec.port or item.spec.service or '-'): <14} "
            f"{item.status:<12} {shell_join(item.spec.command)}"
        )


def shutdown_processes(processes: list[ManagedProcess]) -> None:
    for item in processes:
        if item.process is not None and item.process.poll() is None:
            item.process.terminate()
    deadline = time.monotonic() + 8
    for item in processes:
        process = item.process
        if process is None:
            continue
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            process.kill()
        item.status = f"exited {process.poll()}"


def run_checked(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print(f"$ {shell_join(command)}")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def run_unchecked(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    try:
        subprocess.run(command, cwd=cwd, env=env, check=False)
    except OSError:
        pass


def shell_join(command: list[str]) -> str:
    return " ".join(command)


if __name__ == "__main__":
    raise SystemExit(main())
