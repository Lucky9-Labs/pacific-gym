"""Redacted development trace export and exact RawTree read-back."""

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TABLE = "luckybucky_hackathon"
API_URL = "https://api.rawtree.com"
SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key)", re.I)
SECRET_TEXT = [
    re.compile(r"(?i)\bBearer\s+[^\s\"']+"),
    re.compile(r"\brt_[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;&\"']+"),
    re.compile(r"(?i)([?&](?:access_token|api_key|token|secret|signature)=)[^&#\s]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def redact(value, secrets=()):
    """Remove known credential fields and credential patterns recursively."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SECRET_KEY.search(key) else redact(item, secrets)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        for pattern in SECRET_TEXT:
            result = pattern.sub(lambda match: (match.group(1) if pattern is SECRET_TEXT[3] else "") + "[REDACTED]", result)
        return result
    return value


def known_secrets():
    return [value for key, value in os.environ.items() if SECRET_KEY.search(key) and len(value) >= 8]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def specimen(repo_root, run_id, codex_session_id, codex_thread_id):
    """Build a representative trace around the real slice 1 source inspection."""
    report_path = repo_root / "proof/slice-01/inspect.json"
    spec_path = repo_root / "plugins/pacific-gym/fixtures/strokah-source.json"
    report = json.loads(report_path.read_text())
    spec = json.loads(spec_path.read_text())
    start = time.time_ns()
    end = time.time_ns()
    events = [
            {"sequence": 1, "kind": "prompt", "text": "Inspect the immutable Strokah visual LOD0 and mechanical rig reference; report structure and hashes."},
            {"sequence": 2, "kind": "tool_call", "tool": "pacific-gym inspect",
             "input": {"spec": spec, "out": ".pacific-gym/inspect.json"}},
            {"sequence": 3, "kind": "tool_result", "tool": "pacific-gym inspect",
             "output": report, "status": "ok"},
            {"sequence": 4, "kind": "decision", "outcome": "source_intake_passed",
             "reason": "LOD0 is static; the separate rig reference has 62 joints and no animation. No rig equivalence is inferred."},
    ]
    artifacts = [
            {"uri": "repo://proof/slice-01/inspect.json", "sha256": sha256(report_path), "media_bytes_exported": False},
            {"uri": "repo://plugins/pacific-gym/fixtures/strokah-source.json", "sha256": sha256(spec_path), "media_bytes_exported": False},
            *[{"uri": asset["uri"], "sha256": asset["sha256"], "media_bytes_exported": False}
              for asset in report["assets"]],
    ]
    rows = []
    for event in events:
        kind = event["kind"]
        rows.append({
            "run_id": run_id,
            "codex_session_id": codex_session_id,
            "codex_thread_id": codex_thread_id,
            "event_id": f"{run_id}:{event['sequence']}",
            "sequence": event["sequence"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": kind,
            "agent_output": event.get("text") or event.get("reason") or "",
            "tool_name": event.get("tool", ""),
            "tool_input": canonical(event.get("input", {})),
            "tool_result": canonical(event.get("output", {})),
            "status": event.get("status", event.get("outcome", "")),
            "goal": "Produce an industrial-physics-ready robot model with evidence-backed validation; do not infer physics acceptance from visual or structural checks.",
            "artifact_refs": artifacts if event["sequence"] in (1, len(events)) else [],
            "trace_source": "representative-replay-of-slice-01-inspection",
            "capture_started_at_unix_ns": start,
            "capture_ended_at_unix_ns": end,
            "original_event_duration_ms": "unknown",
        })
    return rows


def request_json(method, url, key, body, database=None):
    if database:
        url += "?" + urlencode({"database": database})
    payload = canonical(body).encode()
    client_request_id = str(uuid.uuid4())
    request = Request(url, data=payload, method=method,
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                               "X-Request-ID": client_request_id})
    try:
        with urlopen(request, timeout=30) as response:
            return response.headers.get("x-request-id") or client_request_id, json.loads(response.read())
    except HTTPError as error:
        try:
            response_error = json.loads(error.read())
            safe_error = redact(response_error, [*known_secrets(), key])
            detail = json.dumps(safe_error, ensure_ascii=False)[:300]
        except (ValueError, UnicodeDecodeError):
            detail = ""
        suffix = ": " + detail if detail else ""
        raise RuntimeError(f"RawTree HTTP {error.code} at {url.split('?')[0]}{suffix}") from None
    except URLError as error:
        raise RuntimeError(f"RawTree connection failed: {error.reason}") from None


def query_rows(response):
    if isinstance(response, dict):
        for key in ("data", "result", "rows"):
            if isinstance(response.get(key), list):
                return response[key]
    raise ValueError("Unexpected RawTree query response shape")


def sql_for_run(run_id):
    if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        raise ValueError("run_id must be a UUID")
    return ("SELECT * FROM `" + TABLE + "` WHERE run_id = '" + run_id +
            "' ORDER BY sequence LIMIT 10000")


def validate_actual_trace(trace, expected_thread_id=None):
    """Refuse replay fixtures or incomplete current-thread captures."""
    if not trace or any(row.get("trace_source") != "codex-app-read_thread-current-thread" for row in trace):
        raise ValueError("Only explicitly captured current-thread events may be exported as actual traces")
    if expected_thread_id and any(row.get("codex_thread_id") != expected_thread_id for row in trace):
        raise ValueError("Captured trace identity does not match the expected current thread")
    types = {row.get("event_type") for row in trace}
    if not {"prompt", "tool_call", "tool_result", "decision"}.issubset(types):
        raise ValueError("Actual trace must contain prompt, tool call, tool result, and decision events")
    if not any(row.get("artifact_refs") for row in trace):
        raise ValueError("Actual trace must contain at least one artifact reference")
    run_ids = {row.get("run_id") for row in trace}
    threads = {row.get("codex_thread_id") for row in trace}
    if len(run_ids) != 1 or not next(iter(run_ids)) or len(threads) != 1 or not next(iter(threads)):
        raise ValueError("Actual trace must have one nonempty run ID and thread ID")


def export_and_verify(trace, api_key, base_url=API_URL, database=None, expected_thread_id=None):
    validate_actual_trace(trace, expected_thread_id)
    safe_rows = redact(trace, [*known_secrets(), api_key])
    run_id = safe_rows[0]["run_id"]
    sql = sql_for_run(run_id)
    digests = [hashlib.sha256(canonical(row).encode()).hexdigest() for row in safe_rows]
    safe_rows = [{**row, "row_sha256": digest} for row, digest in zip(safe_rows, digests)]
    base_url = base_url.rstrip("/")
    insert_start = time.monotonic_ns()
    insert_request_id, inserted = request_json("POST", base_url + "/v1/tables/" + TABLE,
                                               api_key, safe_rows, database)
    insert_ms = round((time.monotonic_ns() - insert_start) / 1_000_000, 3)
    if inserted.get("inserted") != len(safe_rows):
        raise ValueError(f"RawTree did not confirm {len(safe_rows)} inserted event rows")
    query_start = time.monotonic_ns()
    query_request_ids = []
    expected = safe_rows
    for delay in (0, 0.25, 0.5, 1, 2, 4):
        if delay:
            time.sleep(delay)
        query_request_id, queried = request_json("POST", base_url + "/v1/query",
                                                 api_key, {"sql": sql}, database)
        query_request_ids.append(query_request_id)
        rows = query_rows(queried)
        if len(rows) >= len(expected):
            break
    query_ms = round((time.monotonic_ns() - query_start) / 1_000_000, 3)
    if len(rows) != len(expected) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected {len(expected)} RawTree event rows for {run_id}; got {len(rows)}")
    returned_rows = sorted(rows, key=lambda row: int(row["sequence"]))
    if [row.get("sequence") for row in returned_rows] != [row["sequence"] for row in expected]:
        raise ValueError("RawTree event sequence did not round-trip exactly")
    for actual, wanted in zip(returned_rows, expected):
        extra_fields = set(actual) - set(wanted)
        permitted_metadata = {"timestamp"}  # RawTree supplies ingestion time for this reserved column.
        if any(actual[field] is not None and field not in permitted_metadata for field in extra_fields):
            raise ValueError("RawTree added non-null fields to an event row")
        normalized = {field: actual[field] for field in wanted}
        if normalized != wanted:
            raise ValueError("RawTree event row fields did not round-trip exactly")
        original = {field: value for field, value in wanted.items() if field != "row_sha256"}
        digest = hashlib.sha256(canonical(original).encode()).hexdigest()
        if wanted.get("row_sha256") != digest:
            raise ValueError("Exported event row checksum does not match its original fields")
    sessions = {row["codex_session_id"] for row in returned_rows}
    threads = {row["codex_thread_id"] for row in returned_rows}
    if len(sessions) != 1 or not next(iter(sessions)) or len(threads) != 1 or not next(iter(threads)):
        raise ValueError("RawTree rows have missing or inconsistent Codex session/thread identifiers")
    if expected_thread_id and threads != {expected_thread_id}:
        raise ValueError("RawTree read-back chat identity does not match the requested current thread")
    digest = hashlib.sha256("\n".join(canonical(row) for row in returned_rows).encode()).hexdigest()
    return {"status": "live_round_trip_passed", "run_id": run_id,
            "table": TABLE, "query": sql, "insert_request_id": insert_request_id,
            "query_request_id": query_request_id, "insert_duration_ms": insert_ms,
            "query_request_ids": query_request_ids, "query_duration_ms": query_ms, "trace_sha256": digest,
            "row_count": len(returned_rows), "returned_rows": returned_rows,
            "codex_session_id": next(iter(sessions)), "codex_thread_id": next(iter(threads)),
            "data_boundary": "Only redacted event text and artifact URI/SHA-256 references were transmitted; media bytes remained local."}


def cleanup_verification_rows(api_key, run_id, base_url=API_URL, database=None):
    """Report that the RawTree query API cannot delete rows; never drop the table."""
    if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        raise ValueError("run_id must be a UUID")
    return {"status": "cleanup_unavailable", "table": TABLE, "run_id": run_id,
            "reason": "RawTree query API accepts read queries only; row deletion is unavailable. The table will not be dropped automatically."}
