"""Read live L-GPC resources and pinned inputs without starting SimulationApp."""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "usd", "startup-preflight", "cook-script", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--experience", type=Path)
    parser.add_argument("--minimum-startup-commit-gib", type=float, default=1.,
                        help="Additional startup headroom; never reduces the 1 GiB live reserve")
    args = parser.parse_args()
    if (not math.isfinite(args.minimum_startup_commit_gib)
            or not 1 <= args.minimum_startup_commit_gib <= 128):
        raise ValueError("Startup commit headroom must be between 1 and 128 GiB")
    if platform.system() != "Windows" or platform.node().casefold() != "l-gpc":
        raise ValueError("This native resource probe requires Windows L-GPC")
    if args.out.exists() or sha(args.usd) != args.usd_sha256:
        raise ValueError("Require fresh output and exact candidate")
    manifest = json.loads(args.manifest.read_text())
    startup = json.loads(args.startup_preflight.read_text())
    if (Path(manifest["manifest"]).resolve() != args.manifest.resolve()
            or Path(startup["manifest"]).resolve() != args.manifest.resolve()
            or not startup["ready"]):
        raise ValueError("Manifest or existing native-startup evidence mismatch")
    spec = importlib.util.spec_from_file_location("cook_resources", args.cook_script)
    resources = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resources)
    memory = resources.available_memory()
    command = ("Get-CimInstance Win32_Process | "
               "Where-Object {$_.Name -match 'kit|python|blender'} | "
               "Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Compress")
    raw = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", command],
                                  text=True, timeout=15).strip()
    processes = json.loads(raw) if raw else []
    if isinstance(processes, dict):
        processes = [processes]
    others = [p for p in processes if p["ProcessId"] != os.getpid()]
    ready = (not others and min(memory.values()) >= 2**30
             and memory["available_commit_bytes"] >= args.minimum_startup_commit_gib*2**30)
    report = {
        "schema_version": 1, "kind": "read_only_target_resource_preflight",
        "observed_epoch": time.time(), "hostname": platform.node(),
        "host_os": platform.system(), "manifest": str(args.manifest.resolve()),
        "candidate_sha256": args.usd_sha256, "memory": memory,
        "minimum_free_memory_gib": 1, "conflicting_runtime_processes": others,
        "minimum_startup_commit_gib": args.minimum_startup_commit_gib,
        "probe_process_id": os.getpid(), "native_interpreter": sys.executable,
        "native_interpreter_sha256": sha(Path(sys.executable)),
        "startup_preflight_sha256": sha(args.startup_preflight),
        "cook_script_sha256": sha(args.cook_script), "script_sha256": sha(Path(__file__)),
        "experience_sha256": sha(args.experience) if args.experience else None,
        "new_simulation_app_started": False, "ready": ready,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report))
    if not ready:
        raise RuntimeError("Fresh resource or process-ownership check failed")


if __name__ == "__main__":
    main()
