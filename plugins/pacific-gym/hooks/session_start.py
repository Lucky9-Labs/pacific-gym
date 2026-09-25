#!/usr/bin/env python3
"""Check the install and record a minimal local SessionStart receipt."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
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
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
