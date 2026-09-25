import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pacific_gym.trace import (TABLE, canonical, cleanup_verification_rows,
                               export_and_verify, redact, specimen)


ROOT = Path(__file__).resolve().parents[3]
RUN_ID = "73e424af-9d3a-4287-ac3f-9811b847b42a"


class TraceTest(unittest.TestCase):
    def test_specimen_contains_ordered_rows_and_codex_identity(self):
        trace = specimen(ROOT, RUN_ID, "session-123", "thread-456")
        self.assertEqual([event["event_type"] for event in trace],
                         ["prompt", "tool_call", "tool_result", "decision"])
        self.assertEqual(json.loads(trace[2]["tool_result"])["assets"][1]["structure"]["skins"][0]["joints"], 62)
        self.assertTrue(all(row["codex_session_id"] == "session-123" and row["codex_thread_id"] == "thread-456"
                            for row in trace))
        self.assertEqual(TABLE, "luckybucky_hackathon")
        refs = [ref for row in trace for ref in row["artifact_refs"]]
        self.assertTrue(all(not ref["media_bytes_exported"] and len(ref["sha256"]) == 64 for ref in refs))

    def test_redaction_removes_nested_credentials_before_export(self):
        original = {"prompt": "Bearer rt_secret12345678 and token=knownvalue",
                    "tool": {"api_key": "knownvalue", "url": "https://x.test/?token=knownvalue&versionId=immutable"},
                    "bytes": ["knownvalue", "safe"]}
        safe = redact(original, ["knownvalue"])
        serialized = canonical(safe)
        self.assertNotIn("knownvalue", serialized)
        self.assertNotIn("rt_secret12345678", serialized)
        self.assertIn("versionId=immutable", serialized)
        self.assertEqual(safe["tool"]["api_key"], "[REDACTED]")

    def test_round_trip_requires_exact_rows_and_preserves_order(self):
        trace = specimen(ROOT, RUN_ID, "session-123", "thread-456")
        trace[0]["agent_output"] += " Bearer rt_secret12345678"
        captured = {}

        def fake_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                captured["rows"] = body
                self.assertNotIn("rt_secret12345678", canonical(body))
                return "insert-123", {"inserted": len(body)}
            self.assertIn(RUN_ID, body["sql"])
            return "query-456", {"data": list(reversed(captured["rows"]))}

        with patch("pacific_gym.trace.request_json", side_effect=fake_request):
            result = export_and_verify(trace, "rt_secret12345678")
        self.assertEqual(result["status"], "live_round_trip_passed")
        self.assertEqual(result["query_request_id"], "query-456")
        self.assertEqual(result["row_count"], 4)
        self.assertEqual([row["sequence"] for row in result["returned_rows"]], [1, 2, 3, 4])
        self.assertEqual(result["codex_session_id"], "session-123")
        self.assertEqual(result["codex_thread_id"], "thread-456")
        self.assertTrue(captured["rows"][0]["agent_output"].endswith("[REDACTED]"))

        def tampered_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                return None, {"inserted": len(captured["rows"])}
            altered = [{**row} for row in captured["rows"]]
            altered[0]["agent_output"] = "tampered"
            return None, {"rows": altered}

        with patch("pacific_gym.trace.request_json", side_effect=tampered_request):
            with self.assertRaisesRegex(ValueError, "event sequence|event row fields"):
                export_and_verify(trace, "rt_secret12345678")

    def test_query_retries_until_all_inserted_events_are_visible(self):
        trace = specimen(ROOT, RUN_ID, "session-123", "thread-456")
        captured = {}

        def delayed_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                captured["rows"] = body
                return "insert-123", {"inserted": len(body)}
            captured["queries"] = captured.get("queries", 0) + 1
            rows = [] if captured["queries"] == 1 else captured["rows"]
            return "query-" + str(captured["queries"]), {"data": rows}

        with patch("pacific_gym.trace.request_json", side_effect=delayed_request), patch("pacific_gym.trace.time.sleep"):
            result = export_and_verify(trace, "rt_secret12345678")
        self.assertEqual(result["query_request_ids"], ["query-1", "query-2"])
        self.assertEqual(result["row_count"], 4)

    def test_cleanup_reports_read_only_api_without_mutating_table(self):
        with patch("pacific_gym.trace.request_json") as request:
            result = cleanup_verification_rows("rt_secret12345678", RUN_ID)
        request.assert_not_called()
        self.assertEqual(result["status"], "cleanup_unavailable")
        self.assertIn("read queries only", result["reason"])


if __name__ == "__main__":
    unittest.main()
