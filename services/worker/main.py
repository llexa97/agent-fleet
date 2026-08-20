"""Entrypoint systemd du worker Agent Fleet."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path
from typing import Any

from .config import load_worker_config, redacted_config
from .gateway import WorkerGateway
from .journal import WorkerJournal
from .mcp_relay import LocalMcpRelayServer, SessionIdentity
from .runtime import WorkerRuntime
from .workspaces import WorkspaceResolver


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Worker sortant Agent Fleet")
    parser.add_argument("--config", type=Path, required=True, help="configuration YAML locale")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--print-inventory", action="store_true")
    return parser


async def run(config_path: Path, *, print_inventory: bool = False) -> None:
    config = load_worker_config(config_path)
    # Résoudre avant toute connexion permet d'échouer fermement si un montage LXC
    # attendu manque ou pointe ailleurs après résolution des liens symboliques.
    resolver = WorkspaceResolver(config.workspaces)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    async with WorkerJournal(config.journal_path) as journal:
        runtime_holder: dict[str, WorkerRuntime] = {}
        gateway_holder: dict[str, WorkerGateway] = {}

        async def relay_handler(
            identity: SessionIdentity,
            tool_name: str,
            arguments: dict[str, Any],
            call_id: Any,
        ) -> Any:
            return await runtime_holder["runtime"].relay_tool_call(
                identity, tool_name, arguments, call_id
            )

        relay = LocalMcpRelayServer(config.relay_socket_path, relay_handler)
        await relay.start()

        async def emit(envelope: Any) -> None:
            await gateway_holder["gateway"].emit(envelope)

        runtime = WorkerRuntime(config, journal, resolver, relay, emit)
        gateway = WorkerGateway(config, journal, runtime.handle, runtime.inventory)
        runtime_holder["runtime"] = runtime
        gateway_holder["gateway"] = gateway
        try:
            if print_inventory:
                print((await runtime.inventory()).model_dump_json(indent=2))
                return
            await gateway.run(stop)
        finally:
            await runtime.shutdown()
            await relay.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_worker_config(args.config)
    if args.check_config:
        print(json.dumps(redacted_config(config), indent=2, ensure_ascii=False))
        return 0
    asyncio.run(run(args.config, print_inventory=args.print_inventory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
