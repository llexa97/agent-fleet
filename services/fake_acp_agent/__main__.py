from __future__ import annotations

import asyncio
import sys

from acp import run_agent

from . import __version__
from .agent import FakeAcpAgent


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"agent-fleet-fake-acp {__version__}")
        return 0
    asyncio.run(run_agent(FakeAcpAgent()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
