import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pacific_gym.keyframe_judge import (
    judge_pair,
    load_references,
    publish_candidate,
    ready_candidate,
    run_judge,
)


class KeyframeJudgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reference_dir = self.root / "references"
        self.candidate_dir = self.root / "candidates"
        self.result_dir = self.root / "results"
        self.reference_dir.mkdir()
        self.candidate_dir.mkdir()
        self.reference = self.reference_dir / "frame-001.png"
        Image.new("RGB", (32, 32), (120, 130, 140)).save(self.reference)
        digest = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        self.manifest = self.reference_dir / "manifest.json"
        self.manifest.write_text(json.dumps({"dimensions": [32, 32], "frames": [
            {"path": self.reference.name, "timestamp_seconds": 0.5, "sha256": digest}
        ]}))
        self.ref = load_references(self.manifest)[0]
        self.candidate = self.candidate_dir / "frame-001.png"
        Image.new("RGB", (32, 32), (120, 130, 140)).save(self.candidate)

    def tearDown(self):
        self.temp.cleanup()

    def test_no_candidate_or_mismatched_timestamp_never_reaches_judge(self):
        with patch("pacific_gym.keyframe_judge.verify_model", return_value={"tag": "test"}), \
             patch("pacific_gym.keyframe_judge.judge_pair") as judge:
            missing = run_judge(self.manifest, self.candidate_dir, self.result_dir, "test", "http://local", once=True)
            self.assertEqual(missing["judge_results_emitted"], 0)
            self.assertEqual(missing["pairs_waiting"], 1)
            sidecar = {"schema_version": 1, "frame_id": "frame-001", "timestamp_seconds": 9.0,
                       "image": self.candidate.name,
                       "sha256": hashlib.sha256(self.candidate.read_bytes()).hexdigest(), "complete": True}
            self.candidate.with_suffix(".json").write_text(json.dumps(sidecar))
            mismatch = run_judge(self.manifest, self.candidate_dir, self.result_dir, "test", "http://local", once=True)
            self.assertEqual(mismatch["judge_results_emitted"], 0)
            judge.assert_not_called()

    def test_candidate_publish_requires_matching_id_timestamp_dimensions_and_hash(self):
        with self.assertRaisesRegex(ValueError, "timestamp does not match"):
            publish_candidate(self.manifest, self.candidate_dir, "frame-001", 0.6, self.candidate)
        with self.assertRaisesRegex(ValueError, "timestamp does not match"):
            publish_candidate(self.manifest, self.candidate_dir, "frame-001", float("nan"), self.candidate)
        sidecar = publish_candidate(self.manifest, self.candidate_dir, "frame-001", 0.5, self.candidate)
        ready = ready_candidate(self.ref, self.candidate_dir)
        self.assertEqual(sidecar.resolve(), self.candidate.with_suffix(".json").resolve())
        self.assertEqual(ready["sha256"], hashlib.sha256(self.candidate.read_bytes()).hexdigest())
        self.candidate.write_bytes(b"changed after completion")
        self.assertIsNone(ready_candidate(self.ref, self.candidate_dir))

    def test_candidate_touching_right_edge_is_not_ready(self):
        image = Image.new("RGB", (32, 32), (20, 20, 20))
        for y in range(10, 20):
            image.putpixel((31, y), (220, 80, 20))
        image.save(self.candidate)
        publish_candidate(self.manifest, self.candidate_dir, "frame-001", 0.5, self.candidate)
        self.assertIsNone(ready_candidate(self.ref, self.candidate_dir))

    def test_judge_waits_for_every_reference_timestamp(self):
        second_reference = self.reference_dir / "frame-002.png"
        Image.new("RGB", (32, 32), (100, 110, 120)).save(second_reference)
        digest = hashlib.sha256(second_reference.read_bytes()).hexdigest()
        self.manifest.write_text(json.dumps({"dimensions": [32, 32], "frames": [
            {"path": self.reference.name, "timestamp_seconds": 0.0,
             "sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest()},
            {"path": second_reference.name, "timestamp_seconds": 1.0, "sha256": digest},
        ]}))
        publish_candidate(self.manifest, self.candidate_dir, "frame-001", 0.0, self.candidate)
        with patch("pacific_gym.keyframe_judge.verify_model", return_value={"tag": "test"}), \
             patch("pacific_gym.keyframe_judge.judge_pair") as judge:
            result = run_judge(self.manifest, self.candidate_dir, self.result_dir,
                               "test", "http://local", once=True)
        self.assertFalse(result["batch_ready"])
        self.assertEqual(result["judge_results_emitted"], 0)
        self.assertEqual(result["pairs_waiting"], 2)
        judge.assert_not_called()

    def test_reference_manifest_rejects_changed_reference_bytes(self):
        self.reference.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash-mismatched"):
            load_references(self.manifest)

    def test_pair_judge_records_only_structured_advisory_result(self):
        candidate = {"path": self.candidate, "sha256": hashlib.sha256(self.candidate.read_bytes()).hexdigest()}
        response = {"message": {"content": json.dumps({
            "verdict": "match", "evidence": "The visible leg bends align.", "discrepancies": [],
            "next_steer": "Keep this knee pose.", "confidence": "medium"
        })}, "done_reason": "stop"}
        with patch("pacific_gym.keyframe_judge.post_json", return_value=response) as post:
            result = judge_pair(self.ref, candidate, self.result_dir, "test-model", "http://local", {"tag": "test-model"})
        self.assertEqual(result["status"], "paired_judgment")
        self.assertEqual(result["judge"]["verdict"], "match")
        sent = post.call_args.args[1]
        self.assertIn("synchronized animation frames", sent["messages"][0]["content"])
        self.assertEqual(len(sent["messages"][0]["images"]), 1)
        self.assertTrue((self.result_dir / "frame-001-pair.png").is_file())

    def test_existing_judgment_cannot_be_reused_for_a_changed_candidate(self):
        publish_candidate(self.manifest, self.candidate_dir, "frame-001", 0.5, self.candidate)
        candidate = ready_candidate(self.ref, self.candidate_dir)
        response = {"message": {"content": json.dumps({
            "verdict": "match", "evidence": "The visible pose aligns.", "discrepancies": [],
            "next_steer": "Keep the pose.", "confidence": "medium"
        })}, "done_reason": "stop"}
        with patch("pacific_gym.keyframe_judge.post_json", return_value=response):
            judge_pair(self.ref, candidate, self.result_dir, "test-model", "http://local", {"tag": "test-model"})
        Image.new("RGB", (32, 32), (10, 20, 30)).save(self.candidate)
        publish_candidate(self.manifest, self.candidate_dir, "frame-001", 0.5, self.candidate)
        with patch("pacific_gym.keyframe_judge.verify_model", return_value={"tag": "test-model"}):
            with self.assertRaisesRegex(ValueError, "conflicts with current pair"):
                run_judge(self.manifest, self.candidate_dir, self.result_dir, "test-model", "http://local", once=True)


if __name__ == "__main__":
    unittest.main()
