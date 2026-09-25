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


TABLE = "pacific_gym_development_traces"
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


def specimen(repo_root, run_id):
    """Build a representative trace around the real slice 1 source inspection."""
    report_path = repo_root / "proof/slice-01/inspect.json"
    spec_path = repo_root / "plugins/pacific-gym/fixtures/strokah-source.json"
    report = json.loads(report_path.read_text())
    spec = json.loads(spec_path.read_text())
    start = time.time_ns()
    end = time.time_ns()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "target_scope": "unverified",
        "source": "representative-replay-of-slice-01-inspection",
        "timing": {"replay_capture_started_at_unix_ns": start,
                   "replay_capture_ended_at_unix_ns": end,
                   "replay_capture_duration_ms": round((end - start) / 1_000_000, 3),
                   "original_inspection_duration_ms": None},
        "events": [
            {"sequence": 1, "kind": "prompt", "text": "Inspect the immutable Strokah visual LOD0 and mechanical rig reference; report structure and hashes."},
            {"sequence": 2, "kind": "tool_call", "tool": "pacific-gym inspect",
             "input": {"spec": spec, "out": ".pacific-gym/inspect.json"}},
            {"sequence": 3, "kind": "tool_result", "tool": "pacific-gym inspect",
             "output": report, "status": "ok"},
            {"sequence": 4, "kind": "decision", "outcome": "source_intake_passed",
             "reason": "LOD0 is static; the separate rig reference has 62 joints and no animation. No rig equivalence is inferred."},
        ],
        "artifacts": [
            {"uri": "repo://proof/slice-01/inspect.json", "sha256": sha256(report_path), "media_bytes_exported": False},
            {"uri": "repo://plugins/pacific-gym/fixtures/strokah-source.json", "sha256": sha256(spec_path), "media_bytes_exported": False},
            *[{"uri": asset["uri"], "sha256": asset["sha256"], "media_bytes_exported": False}
              for asset in report["assets"]],
        ],
    }


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
        raise RuntimeError(f"RawTree HTTP {error.code} at {url.split('?')[0]}") from None
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
    return ("SELECT run_id, trace_json, trace_sha256 FROM " + TABLE +
            " WHERE run_id = '" + run_id + "' LIMIT 2")


def export_and_verify(trace, api_key, base_url=API_URL, database=None):
    safe = redact(trace, [*known_secrets(), api_key])
    document = canonical(safe)
    digest = hashlib.sha256(document.encode()).hexdigest()
    run_id = safe["run_id"]
    sql = sql_for_run(run_id)
    row = {"run_id": safe["run_id"], "trace_json": document, "trace_sha256": digest,
           "created_at": datetime.now(timezone.utc).isoformat()}
    base_url = base_url.rstrip("/")
    insert_start = time.monotonic_ns()
    insert_request_id, inserted = request_json("POST", base_url + "/v1/tables/" + TABLE,
                                               api_key, [row], database)
    insert_ms = round((time.monotonic_ns() - insert_start) / 1_000_000, 3)
    if inserted.get("inserted") != 1:
        raise ValueError("RawTree did not confirm one inserted row")
    query_start = time.monotonic_ns()
    query_request_ids = []
    for delay in (0, 0.25, 0.5, 1, 2, 4):
        if delay:
            time.sleep(delay)
        query_request_id, queried = request_json("POST", base_url + "/v1/query",
                                                 api_key, {"sql": sql}, database)
        query_request_ids.append(query_request_id)
        rows = query_rows(queried)
        if rows:
            break
    query_ms = round((time.monotonic_ns() - query_start) / 1_000_000, 3)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError(f"Expected one RawTree row for {run_id}; got {len(rows)}")
    returned = rows[0]
    if returned.get("run_id") != run_id or returned.get("trace_sha256") != digest:
        raise ValueError("RawTree run ID or digest mismatch")
    stored = returned.get("trace_json")
    if isinstance(stored, dict):
        stored = canonical(stored)
    if stored != document:
        raise ValueError("RawTree trace fields did not round-trip exactly")
    return {"status": "live_round_trip_passed", "run_id": run_id,
            "table": TABLE, "query": sql, "insert_request_id": insert_request_id,
            "query_request_id": query_request_id, "insert_duration_ms": insert_ms,
            "query_request_ids": query_request_ids, "query_duration_ms": query_ms, "trace_sha256": digest,
            "row_count": 1, "returned_row": returned,
            "data_boundary": "Only redacted JSON text and artifact URI/SHA-256 references were transmitted; media bytes remained local."}
