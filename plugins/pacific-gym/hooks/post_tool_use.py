#!/usr/bin/env python3
"""Run advisory comparisons after explicit candidate recording commands."""

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path


def emit(context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": context
    }}))


def _text_response(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("content"), list):
            return "\n".join(_text_response(item) for item in value["content"])
        if isinstance(value.get("text"), str):
            return value["text"]
    if isinstance(value, list):
        return "\n".join(_text_response(item) for item in value)
    return ""


def _published_keyframe(command: str, response: str) -> dict | None:
    """Return CLI args only for a successful publish-candidate-frame call."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    command_index = next((i for i in range(len(tokens) - 2)
                          if tokens[i:i + 3] == ["-m", "pacific_gym", "publish-candidate-frame"]), None)
    if command_index is None:
        return None
    try:
        import argparse
        command_tokens = tokens[command_index + 3:]
        for separator in ("&&", ";", "|", ">", ">>"):
            if separator in command_tokens:
                command_tokens = command_tokens[:command_tokens.index(separator)]
        parser = argparse.ArgumentParser()
        parser.add_argument("--reference-manifest", required=True)
        parser.add_argument("--candidate-dir", required=True)
        parser.add_argument("--frame-id", required=True)
        parser.add_argument("--timestamp", required=True)
        parser.add_argument("--image", required=True)
        args = vars(parser.parse_args(command_tokens))
        result = json.loads(response)
        sidecar = result.get("published_sidecar") if isinstance(result, dict) else None
        expected_sidecar = Path(args["candidate_dir"]).resolve() / f"{args['frame_id']}.json"
        if not sidecar or Path(sidecar).resolve() != expected_sidecar or not expected_sidecar.is_file():
            return None
        try:
            published = json.loads(expected_sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if published.get("complete") is not True or published.get("frame_id") != args["frame_id"]:
            return None
        args["published_sidecar"] = sidecar
        return args
    except (SystemExit, ValueError, json.JSONDecodeError):
        return None


def _compare_published_keyframe(args: dict, model: str, host: str) -> None:
    root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pacific_gym.keyframe_judge import run_judge

    manifest = Path(args["reference_manifest"]).resolve()
    candidate_dir = Path(args["candidate_dir"]).resolve()
    out_dir = candidate_dir.parent / "keyframe-judgments"
    receipt_dir = candidate_dir.parent / "receipts" / "keyframe-hooks"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{args['frame_id']}.json"
    receipt = {
        "schema_version": 1,
        "trigger": "PostToolUse",
        "command": "publish-candidate-frame",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "published_sidecar": str(Path(args["published_sidecar"]).resolve()),
        "reference_manifest": str(manifest),
        "candidate_dir": str(candidate_dir),
        "judgment_dir": str(out_dir),
    }
    try:
        result = run_judge(manifest, candidate_dir, out_dir, model, host, once=True)
        receipt["scan"] = result
    except Exception as exc:
        receipt["scan"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    temporary = receipt_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2) + "\n")
    os.replace(temporary, receipt_path)
    if receipt["scan"].get("status") == "failed":
        emit(f"Pacific Gym keyframe PostToolUse hook ran automatically after publish but comparison failed: "
             f"{receipt['scan']['error']}. Hook receipt: {receipt_path}. Candidate remains unjudged.")
    elif receipt["scan"]["judge_results_emitted"]:
        emit(f"Pacific Gym keyframe PostToolUse comparison ran automatically after publish: "
             f"{result['judge_results_emitted']} paired judgment(s); hook receipt: {receipt_path}. "
             "Results are advisory, not animation or physics acceptance.")
    else:
        emit(f"Pacific Gym keyframe PostToolUse hook ran automatically after publish; "
             f"{result['pairs_waiting']} synchronized pair(s) still waiting. Hook receipt: {receipt_path}.")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        tool_input = event.get("tool_input") or {}
        command = ""
        if isinstance(tool_input, dict):
            # Claude-style hooks expose Bash.command; Codex tool hooks can expose
            # exec_command.cmd directly. Codex may also normalize this to Bash.command.
            command = tool_input.get("command") or tool_input.get("cmd") or ""
        if event.get("tool_name") not in {"Bash", "exec_command"} or not isinstance(command, str):
            return 0
        output = _text_response(event.get("tool_response", ""))
        keyframe_args = _published_keyframe(command, output)
        if keyframe_args:
            model = os.environ.get("PACIFIC_GYM_VISION_MODEL", "hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M")
            host = os.environ.get("PACIFIC_GYM_OLLAMA_HOST", "http://127.0.0.1:11434")
            _compare_published_keyframe(keyframe_args, model, host)
            return 0
        if "candidate-add" not in command:
            return 0
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
