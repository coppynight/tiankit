#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.reducer import reduce_events
from core.state_manager import atomic_write_json, StateManager


def main():
    parser = argparse.ArgumentParser(description="Replay events and generate status.json + derived outputs")
    parser.add_argument("base_dir", help="Path to .tiangong directory")
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    result = reduce_events(base_dir)

    if result.corrupted_lines:
        team_path = base_dir / "team.json"
        project_name = "unknown"
        if team_path.exists():
            try:
                team = json.loads(team_path.read_text(encoding="utf-8"))
                project_name = team.get("project") or project_name
            except Exception:
                pass

        sm = StateManager(base_dir)
        for c in result.corrupted_lines:
            corrupted_event, recovery_event = sm.build_corrupted_event_payload(
                line_offset=c["line"],
                raw_line=c["raw"],
                reason=c["reason"],
                project=project_name,
            )
            sm.append_event(corrupted_event)
            sm.append_event(recovery_event)

        # Re-run after emitting recovery events
        result = reduce_events(base_dir)

    status_path = base_dir / "status.json"
    atomic_write_json(status_path, result.status)

    if result.corrupted_lines:
        print(f"WARN: {len(result.corrupted_lines)} corrupted lines detected (recovery events emitted).")
    print(f"OK: wrote {status_path}")


if __name__ == "__main__":
    main()
