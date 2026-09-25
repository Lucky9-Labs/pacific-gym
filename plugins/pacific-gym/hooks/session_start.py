#!/usr/bin/env python3
"""Read-only install check for the Pacific Gym hook."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
    manifest = root / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        print("Pacific Gym manifest missing", file=sys.stderr)
        return 1
    name = json.loads(manifest.read_text())["name"]
    print(json.dumps({"plugin": name, "hook": "SessionStart", "status": "ready"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
