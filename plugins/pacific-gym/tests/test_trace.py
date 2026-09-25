import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pacific_gym.trace import TABLE, canonical, cleanup_verification_rows, export_and_verify, redact, validate_actual_trace
from pacific_gym.trace_capture import capture_read_thread, export_thread_capture_file


THREAD = "01a0da85-23bf-7811-8531-a45e6cdca8bb"
RUN = "73e424af-9d3a-4287-ac3f-9811b847b42a"


def actual_thread(path):
    return {"thread": {"id": THREAD, "kind": "codex"}, "turns": [{"items": [
        {"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "Inspect these current inputs"}]},
        {"type": "commandExecution", "id": "t1", "command": "python inspect.py", "cwd": str(path),
         "status": "completed", "exitCode": 0, "output": {"text": str(path / "artifact.json")}},
        {"type": "agentMessage", "id": "a1", "phase": "final_answer", "text": "Inspection passed for the requested run."},
    ]}]}


class TraceTest(unittest.TestCase):
    def test_capture_maps_actual_thread_events_and_artifact_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.json"
            artifact.write_text('{"actual":true}')
            rows = capture_read_thread(actual_thread(root), THREAD, RUN, [root], codex_session_id=THREAD)
        kinds = [row["event_type"] for row in rows]
        self.assertEqual(kinds, ["prompt", "tool_call", "tool_result", "assistant_message", "decision"])
        self.assertEqual(rows[0]["codex_thread_id"], THREAD)
        self.assertEqual(rows[0]["codex_session_id"], THREAD)
        self.assertEqual(rows[2]["trace_source"], "codex-app-read_thread-current-thread")
        self.assertEqual(len(rows[-1]["artifact_refs"][0]["sha256"]), 64)
        self.assertEqual(TABLE, "luckybucky_hackathon")

    def test_rejects_identity_mismatch_and_missing_actual_evidence(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            capture_read_thread(actual_thread(Path("/tmp")), "other", RUN)
        with self.assertRaisesRegex(ValueError, "artifact"):
            capture_read_thread({"thread": {"id": THREAD, "kind": "codex"}, "turns": [{"items": [
                {"type": "userMessage", "content": [{"type": "text", "text": "prompt"}]},
                {"type": "commandExecution", "command": "x", "output": {"text": "no path"}},
                {"type": "agentMessage", "phase": "final_answer", "text": "decision"},
            ]}]}, THREAD, RUN, codex_session_id=THREAD)

    def test_refuses_truncated_tool_output_instead_of_silently_dropping_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifact.json").write_text("present")
            payload = actual_thread(root)
            payload["turns"][0]["items"][1]["output"]["truncated"] = True
            rows = capture_read_thread(payload, THREAD, RUN, [root], codex_session_id=THREAD)
            self.assertTrue(any("partial" in warning["reason"]
                                for warning in rows[0]["capture_warnings"]))

    def test_replay_fixture_is_never_accepted_as_actual_trace(self):
        with self.assertRaisesRegex(ValueError, "current-thread"):
            validate_actual_trace([{"trace_source": "representative-replay-of-slice-01-inspection"}])

    def test_redaction_removes_nested_credentials_and_known_values(self):
        safe = redact({"prompt": "Bearer rt_secret12345678 token=knownvalue",
                       "tool": {"api_key": "knownvalue", "url": "https://x.test/?token=knownvalue&versionId=immutable"}},
                      ["knownvalue"])
        serialized = canonical(safe)
        self.assertNotIn("knownvalue", serialized)
        self.assertNotIn("rt_secret12345678", serialized)
        self.assertIn("versionId=immutable", serialized)

    def test_export_round_trip_checks_exact_rows_and_current_chat_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifact.json").write_text('{"actual":true}')
            rows = capture_read_thread(actual_thread(root), THREAD, RUN, [root], codex_session_id=THREAD)
        captured = {}

        def fake_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                captured["rows"] = body
                self.assertNotIn("rt_secret12345678", canonical(body))
                return "insert-123", {"inserted": len(body)}
            self.assertIn(RUN, body["sql"])
            rows = [{**row, "timestamp": "2026-09-25T12:00:00+00:00",
                     "tool_input.generated_column": None} for row in captured["rows"]]
            return "query-456", {"data": list(reversed(rows))}

        with patch("pacific_gym.trace.request_json", side_effect=fake_request):
            result = export_and_verify(rows, "rt_secret12345678", expected_thread_id=THREAD)
        self.assertEqual(result["status"], "live_round_trip_passed")
        self.assertEqual(result["query_request_id"], "query-456")
        self.assertEqual(result["codex_thread_id"], THREAD)
        self.assertEqual(len(result["returned_rows"]), 5)

    def test_file_integration_saves_export_and_readback_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifact.json").write_text('{"actual":true}')
            event_file = root / "events.json"
            event_file.write_text(json.dumps(actual_thread(root)))

            def fake_request(method, url, key, body, database):
                if "/v1/tables/" in url:
                    return "insert-1", {"inserted": len(body)}
                return "query-1", {"rows": getattr(fake_request, "rows", [])}

            def request(method, url, key, body, database):
                if "/v1/tables/" in url:
                    request.rows = body
                    return "insert-1", {"inserted": len(body)}
                return "query-1", {"rows": request.rows}

            with patch("pacific_gym.trace.request_json", side_effect=request), patch.dict("os.environ", {"CODEX_SESSION_ID": THREAD}):
                receipt = export_thread_capture_file(event_file, THREAD, RUN, "rt_secret12345678", root / "out", allowed_artifact_roots=[root])
            self.assertEqual(receipt["status"], "live_round_trip_passed")
            self.assertTrue((root / "out/actual-chat-trace.json").is_file())
            self.assertTrue((root / "out/actual-chat-export-readback.json").is_file())
            self.assertNotIn("returned_rows", json.loads((root / "out/actual-chat-export-readback.json").read_text()))

    def test_cleanup_does_not_mutate_table(self):
        with patch("pacific_gym.trace.request_json") as request:
            result = cleanup_verification_rows("rt_secret12345678", RUN)
        request.assert_not_called()
        self.assertEqual(result["status"], "cleanup_unavailable")


if __name__ == "__main__":
    unittest.main()
