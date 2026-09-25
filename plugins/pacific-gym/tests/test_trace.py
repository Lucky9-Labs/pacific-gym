import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pacific_gym.trace import canonical, export_and_verify, redact, specimen


ROOT = Path(__file__).resolve().parents[3]


class TraceTest(unittest.TestCase):
    def test_specimen_contains_complete_development_trace_and_local_media_references(self):
        trace = specimen(ROOT, "73e424af-9d3a-4287-ac3f-9811b847b42a")
        self.assertEqual([event["kind"] for event in trace["events"]],
                         ["prompt", "tool_call", "tool_result", "decision"])
        self.assertEqual(trace["events"][2]["output"]["assets"][1]["structure"]["skins"][0]["joints"], 62)
        self.assertTrue(all(not artifact["media_bytes_exported"] and len(artifact["sha256"]) == 64
                            for artifact in trace["artifacts"]))

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

    def test_round_trip_requires_exact_stored_fields(self):
        trace = specimen(ROOT, "73e424af-9d3a-4287-ac3f-9811b847b42a")
        trace["events"][0]["text"] += " Bearer rt_secret12345678"
        captured = {}

        def fake_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                captured["row"] = body[0]
                self.assertNotIn("rt_secret12345678", canonical(body))
                return "insert-123", {"inserted": 1}
            self.assertIn(trace["run_id"], body["sql"])
            return "query-456", {"data": [captured["row"]]}

        with patch("pacific_gym.trace.request_json", side_effect=fake_request):
            result = export_and_verify(trace, "rt_secret12345678")
        self.assertEqual(result["status"], "live_round_trip_passed")
        self.assertEqual(result["query_request_id"], "query-456")
        self.assertEqual(json.loads(result["returned_row"]["trace_json"])["events"][2]["output"],
                         trace["events"][2]["output"])

        def tampered_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                return None, {"inserted": 1}
            return None, {"rows": [{**captured["row"], "trace_json": "{}"}]}

        with patch("pacific_gym.trace.request_json", side_effect=tampered_request):
            with self.assertRaisesRegex(ValueError, "round-trip exactly"):
                export_and_verify(trace, "rt_secret12345678")

    def test_query_retries_until_insert_is_visible(self):
        trace = specimen(ROOT, "73e424af-9d3a-4287-ac3f-9811b847b42a")
        captured = {}

        def delayed_request(method, url, key, body, database):
            if "/v1/tables/" in url:
                captured["row"] = body[0]
                return "insert-123", {"inserted": 1}
            captured["queries"] = captured.get("queries", 0) + 1
            return "query-" + str(captured["queries"]), {"data": [] if captured["queries"] == 1 else [captured["row"]]}

        with patch("pacific_gym.trace.request_json", side_effect=delayed_request), patch("pacific_gym.trace.time.sleep"):
            result = export_and_verify(trace, "rt_secret12345678")
        self.assertEqual(result["query_request_ids"], ["query-1", "query-2"])
        self.assertEqual(result["row_count"], 1)


if __name__ == "__main__":
    unittest.main()
