#!/usr/bin/env python3
"""Start or restore run-bound cultural and industry reference research."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        event = {}
    root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(root))
    try:
        from pacific_gym.nimble_research import configured_key
        from pacific_gym.run import for_workspace, load, save
        from pacific_gym.trace import redact
        workspace = Path(event.get("cwd") or os.getcwd()).resolve()
        active = for_workspace(workspace)
        if not active:
            return 0
        manifest, data = active
        if data.get("state") != "active":
            return 0
        state = data.setdefault("reference_research", {"status": "waiting_for_credentials"})
        status = state.get("status", "waiting_for_credentials")
        key = configured_key(workspace)
        if status in {"waiting_for_credentials", "failed"} and key:
            try:
                from pacific_gym.nimble_research import start_run_research
                start_run_research(
                    manifest, key,
                    os.environ.get("PACIFIC_GYM_VISION_MODEL", "hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M"),
                    os.environ.get("PACIFIC_GYM_OLLAMA_HOST", "http://127.0.0.1:11434"),
                )
                data = load(manifest)
                state = data["reference_research"]
                status = state["status"]
            except Exception as error:
                data = load(manifest)
                data["reference_research"].update(status="failed", error=redact(str(error), [key])[:500])
                save(data)
                status = "failed"
        context = (
            "Pacific Gym cultural/industrial reference intake is a separate skill: "
            "use `cultural-industrial-references` for this run. Research status: " + status + ". "
        )
        if status in {"waiting_for_credentials", "failed", "waiting_for_reference_image"}:
            context += (
                f"Start or diagnose it with `python3 -m pacific_gym run-research-start --manifest {manifest} "
                "--clipboard-key` when appropriate. The skill explains the local-caption and credential boundary. "
            )
        elif status == "running":
            context += (
                f"Poll with `python3 -m pacific_gym run-research-poll --manifest {manifest}` "
                "at FLUX, Blender, and Isaac Sim planning checkpoints. Add `--clipboard-key` only if "
                "the key is unavailable in the environment/workspace .env and remains on the clipboard. "
            )
        elif status == "complete":
            context += f"Read the tagged research receipt at {state.get('receipt')}. "
        context += (
            "Route FLUX-tagged artist/motion leads to visual prompts, Blender-tagged leads to rig authoring, "
            "and Isaac Sim-tagged engineering leads to simulation planning. Treat all as sourced leads; "
            "they do not prove this asset's rig, gait, or physics."
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": context
        }}))
    except (ImportError, ValueError, OSError, KeyError, json.JSONDecodeError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
