#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.orchestrator import Orchestrator, OrchestratorConfig


def main():
    parser = argparse.ArgumentParser(description="Run orchestrator loop")
    parser.add_argument("base_dir", help="Path to .tiangong directory")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--heartbeat-timeout", type=int, default=180)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    orch = Orchestrator(OrchestratorConfig(base_dir=Path(args.base_dir), heartbeat_timeout_sec=args.heartbeat_timeout))

    if args.once:
        orch.tick()
        print("OK: ticked once")
        return 0

    while True:
        orch.tick()
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
