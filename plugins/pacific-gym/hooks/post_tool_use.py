#!/usr/bin/env python3
"""Compare explicitly recorded candidate renders with pinned workspace references."""

import json
import os
import re
import sys
from pathlib import Path


def emit(context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": context
    }}))


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        tool_input = event.get("tool_input") or {}
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if event.get("tool_name") != "Bash" or "candidate-add" not in command:
            return 0
        output = event.get("tool_response", "")
        if not isinstance(output, str):
            output = json.dumps(output)
        match = re.search(r"PACIFIC_GYM_CANDIDATE=([0-9a-f-]{36})", output)
        if not match:
            return 0
        workspace = Path(event.get("cwd") or os.getcwd()).resolve()
        root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
        sys.path.insert(0, str(root))
        from pacific_gym import run
        from pacific_gym.compare import compare_general

        active = run.for_workspace(workspace)
        if not active:
            return 0
        manifest, data = active
        candidate_id = match.group(1)
        candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
        if candidate is None:
            return 0
        references = [item for item in data["inputs"] if item["role"] == "reference_frame"]
        if not references:
            emit(f"Pacific Gym candidate {candidate_id} is recorded, but this run has no pinned PNG reference frame; add one before comparison.")
            return 0
        model = os.environ.get("PACIFIC_GYM_VISION_MODEL", "hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M")
        host = os.environ.get("PACIFIC_GYM_OLLAMA_HOST", "http://127.0.0.1:11434")
        summaries = []
        for index, reference_item in enumerate(references):
            reference = Path(reference_item["path"])
            if not reference.is_file() or run._input("reference_frame", reference)["sha256"] != reference_item["sha256"]:
                emit(f"Pacific Gym pinned reference frame changed since run start: {reference}. Comparison skipped; restore the exact pinned bytes.")
                return 0
            pair_out = manifest.parent / "comparisons" / f"{candidate_id}-{index}.png"
            receipt = compare_general(reference, Path(candidate["path"]), model, host, pair_out)
            run.add_feedback(manifest, candidate_id, receipt)
            comparison = receipt["comparison"]
            summaries.append(f"{comparison['confidence']}: {comparison['evidence']} Next: {comparison['next_action']}")
        emit("Pacific Gym reference comparison recorded. Treat model feedback as advisory: "
             "verify each suggested edit against the cited evidence before applying it. "
             "Still images do not establish rig validity, physics, or walking. " + " | ".join(summaries))
        return 0
    except Exception as exc:
        emit(f"Pacific Gym candidate comparison could not run: {type(exc).__name__}: {exc}. Keep the candidate; do not treat comparison failure as acceptance.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
