from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

WORKER_A_ID = "a1000000-0000-4000-8000-000000000001"
WORKER_B_ID = "b2000000-0000-4000-8000-000000000002"
WORKER_TOKENS = {
    WORKER_A_ID: "demo-worker-a-token-change-me-at-least-32-characters",
    WORKER_B_ID: "demo-worker-b-token-change-me-at-least-32-characters",
}


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for(
    predicate: Callable[[], Any],
    *,
    timeout: float,
    description: str,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (httpx.HTTPError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.2)
    suffix = f" ({last_error})" if last_error else ""
    raise AssertionError(f"Délai dépassé: {description}{suffix}")


def _worker_config(
    *,
    worker_id: str,
    port: int,
    state_dir: Path,
    workspace: Path,
) -> dict[str, Any]:
    suffix = "a" if worker_id == WORKER_A_ID else "b"
    return {
        "worker": {
            "id": worker_id,
            "hostname": f"integration-worker-{suffix}",
            "labels": ["development", "fake-acp", suffix],
            "max_sessions": 4,
            "state_dir": str(state_dir),
        },
        "control_plane": {
            "url": f"ws://127.0.0.1:{port}/api/v1/workers/connect",
            "token_env": "AGENT_FLEET_WORKER_TOKEN",
            "heartbeat_seconds": 2,
            "stale_after_seconds": 10,
            "backoff_initial_seconds": 0.05,
            "backoff_max_seconds": 0.5,
            "allow_insecure_localhost": True,
        },
        "harnesses": {
            "fake": {
                "executable": sys.executable,
                "args": ["-m", "services.worker.fake_acp"],
                "enabled": True,
                "max_instances": 4,
                "env_allowlist": [],
                "version_args": ["-m", "services.worker.fake_acp", "--version"],
                "startup_timeout_seconds": 10,
                "prompt_timeout_seconds": 30,
            }
        },
        "workspaces": [
            {
                "id": f"fleetbase-ui-{suffix}",
                "display_name": f"Workspace {suffix.upper()}",
                "root": str(workspace),
                "read_only": False,
            }
        ],
        "mcp_proxy": {
            "enabled": True,
            "executable": sys.executable,
            "args": ["-m", "services.fleet_mcp_proxy"],
            "request_timeout_seconds": 15,
        },
    }


def _tail(path: Path, lines: int = 80) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "<journal indisponible>"


@pytest.mark.integration
def test_axel_to_three_agents_across_two_real_worker_processes(tmp_path: Path) -> None:
    """Tranche verticale réelle : HTTP + WSS worker + ACP stdio + MCP + persistance."""

    repository = Path(__file__).resolve().parents[2]
    port = _free_port()
    database_path = tmp_path / "control-plane.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    base_url = f"http://127.0.0.1:{port}"
    common_env = {
        **os.environ,
        "PYTHONPATH": str(repository),
        "PYTHONUNBUFFERED": "1",
        "AGENT_FLEET_ENVIRONMENT": "test",
        "AGENT_FLEET_DATABASE_URL": database_url,
        "AGENT_FLEET_REDIS_URL": "",
        "AGENT_FLEET_EMBEDDED_DISPATCHER": "true",
        "AGENT_FLEET_TRUSTED_HOSTS": "127.0.0.1,localhost",
        "AGENT_FLEET_PUBLIC_URL": base_url,
        "AGENT_FLEET_WEB_ORIGIN": base_url,
        "AGENT_FLEET_SESSION_SECRET": "integration-session-secret-at-least-thirty-two-bytes",
        "AGENT_FLEET_BOOTSTRAP_TOKEN": "integration-bootstrap-token-long-enough",
        "AGENT_FLEET_COOKIE_SECURE": "false",
        "AGENT_FLEET_DISPATCHER_POLL_SECONDS": "0.05",
        "AGENT_FLEET_DELIVERY_LEASE_SECONDS": "15",
        "AGENT_FLEET_WORKER_OFFLINE_AFTER_SECONDS": "10",
        "AGENT_FLEET_LOG_JSON": "false",
        "AGENT_FLEET_LOG_LEVEL": "WARNING",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repository,
        env=common_env,
        check=True,
        timeout=30,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "scripts.seed_demo"],
        cwd=repository,
        env=common_env,
        check=True,
        timeout=30,
        capture_output=True,
        text=True,
    )

    server_log = tmp_path / "server.log"
    worker_logs = [tmp_path / "worker-a.log", tmp_path / "worker-b.log"]
    short_runtime = tempfile.TemporaryDirectory(prefix="af-e2e-", dir="/tmp")
    short_runtime_path = Path(short_runtime.name)
    log_handles: list[Any] = []
    processes: list[subprocess.Popen[bytes]] = []
    try:
        server_handle = server_log.open("wb")
        log_handles.append(server_handle)
        server = subprocess.Popen(  # noqa: S603 - exécutable Python courant, arguments fixes
            [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.api.agent_fleet_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=repository,
            env=common_env,
            stdout=server_handle,
            stderr=subprocess.STDOUT,
        )
        processes.append(server)
        _wait_for(
            lambda: httpx.get(f"{base_url}/api/v1/health", timeout=1).status_code == 200,
            timeout=15,
            description="démarrage du Control Plane",
        )

        for index, worker_id in enumerate((WORKER_A_ID, WORKER_B_ID)):
            suffix = "a" if index == 0 else "b"
            state_dir = short_runtime_path / f"state-{suffix}"
            workspace = short_runtime_path / f"workspace-{suffix}"
            state_dir.mkdir()
            workspace.mkdir()
            config_path = tmp_path / f"worker-{suffix}.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    _worker_config(
                        worker_id=worker_id,
                        port=port,
                        state_dir=state_dir,
                        workspace=workspace,
                    ),
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            worker_handle = worker_logs[index].open("wb")
            log_handles.append(worker_handle)
            worker_env = {
                **common_env,
                "AGENT_FLEET_WORKER_TOKEN": WORKER_TOKENS[worker_id],
            }
            processes.append(
                subprocess.Popen(  # noqa: S603 - exécutable Python courant, config temporaire
                    [
                        sys.executable,
                        "-m",
                        "services.worker.main",
                        "--config",
                        str(config_path),
                    ],
                    cwd=repository,
                    env=worker_env,
                    stdout=worker_handle,
                    stderr=subprocess.STDOUT,
                )
            )

        with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"email": "axel@example.com", "password": "agent-fleet-demo-password"},
            )
            assert login.status_code == 200, login.text
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

            workers = _wait_for(
                lambda: (
                    response.json()
                    if (response := client.get("/api/v1/workers")).status_code == 200
                    and len(response.json()) == 2
                    and all(item["status"] == "online" for item in response.json())
                    else None
                ),
                timeout=20,
                description="connexion sortante des deux workers",
            )
            assert {item["id"] for item in workers} == {WORKER_A_ID, WORKER_B_ID}

            business = next(
                item for item in client.get("/api/v1/spaces").json() if item["slug"] == "business"
            )
            channel = next(
                item
                for item in client.get(
                    "/api/v1/channels", params={"space_id": business["id"]}
                ).json()
                if item["slug"] == "client-taxi"
            )
            agents = {
                item["handle"]: item
                for item in client.get("/api/v1/agents", params={"space_id": business["id"]}).json()
            }
            post = client.post(
                f"/api/v1/channels/{channel['id']}/messages",
                headers={"Idempotency-Key": "e2e-axel-cto-authentication-001"},
                json={
                    "content": "@cto fais avancer l'authentification",
                    "mentions": [
                        {
                            "target_type": "agent",
                            "target_id": agents["cto"]["id"],
                            "handle_at_creation": "cto",
                        }
                    ],
                    "expects_response": True,
                },
            )
            assert post.status_code == 201, post.text
            trace_id = post.json()["trace_id"]

            def completed_chain() -> dict[str, Any] | None:
                traces = client.get("/api/v1/traces").json()
                trace = next((item for item in traces if item["id"] == trace_id), None)
                tasks = client.get("/api/v1/tasks").json()
                messages = client.get(
                    f"/api/v1/channels/{channel['id']}/messages", params={"limit": 200}
                ).json()
                if (
                    trace
                    and trace["status"] == "completed"
                    and len(tasks) >= 2
                    and all(item["status"] == "completed" for item in tasks)
                    and any(
                        item.get("author_handle") == "cto"
                        and "Fake ACP a traité" in item["content"]
                        for item in messages
                    )
                ):
                    return {"trace": trace, "tasks": tasks, "messages": messages}
                return None

            result = _wait_for(
                completed_chain,
                timeout=45,
                description="chaîne Axel → CTO → backend → reviewer → CTO",
            )
            assert result["trace"]["delegation_count"] >= 4
            assert {item["assigned_agent_id"] for item in result["tasks"]} >= {
                agents["backend-dev"]["id"],
                agents["code-reviewer"]["id"],
            }

        # Un nouveau navigateur récupère l'historique central après reconnexion.
        with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as reloaded:
            login = reloaded.post(
                "/api/v1/auth/login",
                json={"email": "axel@example.com", "password": "agent-fleet-demo-password"},
            )
            assert login.status_code == 200
            history = reloaded.get(
                f"/api/v1/channels/{channel['id']}/messages", params={"limit": 200}
            )
            assert history.status_code == 200
            assert len(history.json()) >= 5

        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            deliveries = database.execute(
                "SELECT status, COUNT(*) AS count FROM deliveries GROUP BY status"
            ).fetchall()
            assert {row["status"]: row["count"] for row in deliveries} == {"completed": 5}
            session_workers = {
                row["worker_id"]
                for row in database.execute("SELECT DISTINCT worker_id FROM agent_sessions")
            }
            assert session_workers == {
                WORKER_A_ID.replace("-", ""),
                WORKER_B_ID.replace("-", ""),
            }
            participants = database.execute(
                "SELECT COUNT(*) FROM trace_participants WHERE trace_id = ?",
                (trace_id.replace("-", ""),),
            ).fetchone()[0]
            assert participants == 3
    except Exception as exc:
        journals = "\n\n".join(
            [f"--- {path.name} ---\n{_tail(path)}" for path in [server_log, *worker_logs]]
        )
        pytest.fail(f"{exc}\n\n{journals}", pytrace=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in log_handles:
            handle.close()
        short_runtime.cleanup()
