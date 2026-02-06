#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.orchestrator import Orchestrator, OrchestratorConfig


def main():
    parser = argparse.ArgumentParser(description="Run a single orchestrator tick")
    parser.add_argument("base_dir", help="Path to .tiangong directory")
    parser.add_argument("--heartbeat-timeout", type=int, default=180)
    args = parser.parse_args()

    orch = Orchestrator(OrchestratorConfig(base_dir=Path(args.base_dir), heartbeat_timeout_sec=args.heartbeat_timeout))
    result = orch.tick()
    print(f"OK: updated status for {result.status['project']['name']}")


if __name__ == "__main__":
    main()
