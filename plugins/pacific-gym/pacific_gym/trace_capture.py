"""Convert an explicitly supplied Codex read_thread response into trace rows.

The caller must obtain this response for the current thread through the Codex
app API. This module never scans session files or other conversations.
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .trace import canonical, redact, known_secrets, export_and_verify


def _thread_payload(source):
    """Accept the JSON text returned by read_thread or its decoded envelope."""
    if isinstance(source, str):
        source = json.loads(source)
    if isinstance(source, dict) and isinstance(source.get("content"), list):
        blocks = [item.get("text", "") for item in source["content"]
                  if item.get("type") == "text"]
        if len(blocks) != 1:
            raise ValueError("Expected one text block from current-thread read_thread")
        source = json.loads(blocks[0])
    if not isinstance(source, dict) or not isinstance(source.get("thread"), dict):
        raise ValueError("Input must be a Codex read_thread response")
    if not isinstance(source.get("turns"), list):
        raise ValueError("Codex read_thread response has no turns")
    return source


def _text_content(item):
    return "\n".join(part.get("text", "") for part in item.get("content", [])
                    if part.get("type") == "text")


def _artifact_refs(text, allowed_roots=()):
    refs = []
    roots = [Path(root).resolve() for root in allowed_roots]
    for candidate in dict.fromkeys(re.findall(r"(?<![\w])/(?:[^\s\"'<>]+?\.(?:json|md|png|jpg|jpeg|glb|usd|mp4))(?:[:),.]|$)", text, re.I)):
        path = Path(candidate.rstrip(":),."))
        try:
            resolved = path.resolve(strict=True)
            if roots and not any(resolved.is_relative_to(root) for root in roots):
                continue
            if not resolved.is_file():
                continue
            refs.append({"uri": "file://" + str(resolved),
                         "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                         "media_bytes_exported": False})
        except (OSError, RuntimeError):
            continue
    return refs


def capture_read_thread(source, expected_thread_id, run_id, allowed_artifact_roots=(), codex_session_id=None):
    """Build actual event rows from one explicitly scoped read_thread result."""
    payload = _thread_payload(source)
    thread = payload["thread"]
    if thread.get("id") != expected_thread_id:
        raise ValueError("read_thread response identity does not match requested current thread")
    if thread.get("kind") != "codex":
        raise ValueError("Only Codex task threads can be captured")
    started = time.time_ns()
    raw_events = []
    decisions = []
    artifacts = []
    exclusions = []
    warnings = []
    for turn in reversed(payload["turns"]):
        for item in turn.get("items", []):
            kind = item.get("type")
            if kind == "userMessage":
                text = _text_content(item)
                if text:
                    raw_events.append(("prompt", {"text": text, "source_item_id": item.get("id")}))
            elif kind == "commandExecution":
                output = item.get("output") or {}
                if isinstance(output, dict) and output.get("truncated"):
                    warnings.append({"source_item_id": item.get("id", ""),
                                     "reason": "read_thread truncated this actual tool result; captured text is partial"})
                command = item.get("command", "")
                if len(command) > 8000:
                    exclusions.append({"source_item_id": item.get("id", ""),
                                       "reason": "oversized transport command excluded to avoid recursively uploading serialized thread data"})
                    continue
                output_text = output.get("text", "") if isinstance(output, dict) else str(output)
                raw_events.append(("tool_call", {"tool": "exec_command", "input": {"cmd": command,
                                  "cwd": item.get("cwd", "")}, "source_item_id": item.get("id")}))
                raw_events.append(("tool_result", {"tool": "exec_command", "output": output_text,
                                  "status": item.get("status", ""), "exit_code": item.get("exitCode"),
                                  "source_item_id": item.get("id")}))
                artifacts.extend(_artifact_refs(output_text, allowed_artifact_roots))
            elif kind == "mcpToolCall":
                raw_events.append(("tool_call", {"tool": item.get("server", "") + "." + item.get("tool", ""),
                                  "input": item.get("arguments", {}), "status": item.get("status", ""),
                                  "source_item_id": item.get("id")}))
            elif kind == "functionCallOutput":
                output = item.get("output", {})
                raw_events.append(("tool_result", {"tool": item.get("name", ""),
                                  "output": output, "source_item_id": item.get("id")}))
                if item.get("name") == "create_thread":
                    text = output.get("text", "") if isinstance(output, dict) else str(output)
                    match = re.search(r"<input>([\s\S]*?)</input>", text)
                    if match:
                        prompt = match.group(1).strip()
                        raw_events.append(("prompt", {"text": prompt, "source_item_id": item.get("id")}))
                        decisions.append("User explicitly dispatched this current Codex task with the captured scope and acceptance requirements.")
            elif kind == "agentMessage":
                text = item.get("text", "")
                if not text:
                    continue
                raw_events.append(("assistant_message", {"text": text, "phase": item.get("phase", ""),
                                  "source_item_id": item.get("id")}))
                if item.get("phase") in ("final_answer", "analysis"):
                    decisions.append(text)
            elif kind == "fileChange":
                changes = item.get("changes", [])
                raw_events.append(("artifact", {"output": changes, "source_item_id": item.get("id")}))
                for change in changes:
                    file_path = change.get("path") if isinstance(change, dict) else None
                    if not file_path:
                        continue
                    path = Path(file_path)
                    try:
                        resolved = path.resolve(strict=True)
                        if roots := [Path(root).resolve() for root in allowed_artifact_roots]:
                            if not any(resolved.is_relative_to(root) for root in roots):
                                continue
                        if resolved.is_file():
                            artifacts.append({"uri": "file://" + str(resolved),
                                              "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                                              "media_bytes_exported": False})
                    except (OSError, RuntimeError):
                        continue
            elif kind == "artifact":
                artifacts.append({"uri": item.get("uri", ""), "sha256": item.get("sha256", ""),
                                  "media_bytes_exported": False})
    if not any(kind == "prompt" for kind, _ in raw_events):
        raise ValueError("Current-thread source has no actual prompt event")
    if not any(kind == "tool_call" for kind, _ in raw_events) or not any(kind == "tool_result" for kind, _ in raw_events):
        raise ValueError("Current-thread source must contain actual tool calls and results")
    if not decisions:
        raise ValueError("Current-thread source has no actual assistant decision/message event")
    if not artifacts:
        raise ValueError("Current-thread source has no resolvable actual artifact reference")
    raw_events.append(("decision", {"text": decisions[-1], "basis": "actual current-thread assistant message"}))
    ended = time.time_ns()
    timestamp = datetime.now(timezone.utc).isoformat()
    session_id = codex_session_id or os.environ.get("CODEX_SESSION_ID", "")
    if not session_id:
        raise ValueError("CODEX_SESSION_ID is required; a thread ID is not a session ID")
    rows = []
    for sequence, (kind, data) in enumerate(raw_events, 1):
        rows.append({
            "run_id": run_id, "codex_session_id": session_id, "codex_thread_id": thread["id"],
            "event_id": f"{run_id}:{sequence}", "sequence": sequence, "captured_at": timestamp,
            "event_type": kind, "agent_output": data.get("text", ""),
            "tool_name": data.get("tool", ""), "tool_input": canonical(data.get("input", {})),
            "tool_result": canonical(data.get("output", {})),
            "status": data.get("status", data.get("phase", data.get("exit_code", ""))),
            "goal": "Capture redacted actual events from the explicitly requested current Codex thread.",
            "artifact_refs": artifacts if kind in ("prompt", "decision") else [],
            "trace_source": "codex-app-read_thread-current-thread",
            "source_item_id": data.get("source_item_id", ""),
            "capture_exclusions": exclusions,
            "capture_warnings": warnings,
            "capture_started_at_unix_ns": started, "capture_ended_at_unix_ns": ended,
            "original_event_duration_ms": "unknown",
        })
    return redact(rows, known_secrets())


def export_thread_capture_file(event_file, expected_thread_id, run_id, api_key,
                               output_dir, database=None, allowed_artifact_roots=()):
    """Integration point for the packaging CLI's explicit --events-file route.

    event_file must contain the JSON envelope returned by codex_app.read_thread
    for expected_thread_id. Writes sanitized event and transport/read-back receipts.
    """
    event_file = Path(event_file)
    output_dir = Path(output_dir)
    source = json.loads(event_file.read_text())
    rows = capture_read_thread(source, expected_thread_id, run_id, allowed_artifact_roots,
                               codex_session_id=os.environ.get("CODEX_SESSION_ID"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "actual-chat-trace.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    try:
        receipt = export_and_verify(rows, api_key, database=database,
                                    expected_thread_id=expected_thread_id)
    except Exception as error:
        receipt = {"status": "live_round_trip_failed", "run_id": run_id,
                   "codex_thread_id": expected_thread_id, "row_count": len(rows),
                   "inserted_rows_may_remain": True,
                   "error": redact(str(error), [*known_secrets(), api_key]),
                   "provenance": "codex-app-read_thread-current-thread"}
        (output_dir / "actual-chat-export-readback.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        raise
    public_receipt = {key: value for key, value in receipt.items() if key != "returned_rows"}
    public_receipt["readback_row_sha256"] = hashlib.sha256(
        "\n".join(canonical(row) for row in receipt["returned_rows"]).encode()).hexdigest()
    (output_dir / "actual-chat-export-readback.json").write_text(
        json.dumps(public_receipt, indent=2, sort_keys=True) + "\n")
    return public_receipt
