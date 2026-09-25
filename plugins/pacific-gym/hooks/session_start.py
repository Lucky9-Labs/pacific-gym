#!/usr/bin/env python3
"""Check the install and record a minimal local SessionStart receipt."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        event = {}
    root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(root))
    manifest = root / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        print("Pacific Gym manifest missing", file=sys.stderr)
        return 1
    name = json.loads(manifest.read_text())["name"]
    result = {"plugin": name, "hook": "SessionStart", "status": "ready"}
    receipt = {
        **result,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    receipt_paths = []
    data_path = os.environ.get("PLUGIN_DATA")
    if data_path:
        receipt_paths.append(Path(data_path) / "session-start.json")
    probe_path = os.environ.get("PACIFIC_GYM_SESSION_RECEIPT")
    if probe_path:
        receipt_paths.append(Path(probe_path))
    for receipt_path in receipt_paths:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    cwd = Path(event.get("cwd") or os.getcwd()).resolve()
    try:
        from pacific_gym.run import for_workspace
        state = for_workspace(cwd)
    except (ImportError, ValueError, OSError, KeyError, json.JSONDecodeError):
        state = None
    if state:
        manifest, data = state
        if data.get("state") == "active":
            context = (
                f"Pacific Gym run {data['run_id']} is active in this workspace. "
                f"Manifest: {manifest}. Pinned inputs: "
                + ", ".join(f"{item['role']}={item['path']} sha256={item['sha256']}" for item in data["inputs"])
                + ". Preserve input bytes; write derivatives under this run directory. "
                  "Acceptance requires validated USD articulation AND repeated stable forward walking "
                  "on a supported GPU host, or a specific evidence-backed blocker."
            )
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": context
            }}))
            return 0
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
