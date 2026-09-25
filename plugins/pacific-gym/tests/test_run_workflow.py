import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pacific_gym import run
from pacific_gym.integrations import isaac_run


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "pacific-gym"


class RunWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.reference = self.workspace / "reference.png"
        self.reference.write_bytes(b"reference bytes")
        self.png = self.workspace / "candidate.png"
        self.png.write_bytes(b"candidate bytes")
        self.manifest = Path(run.start(self.workspace, self.reference)["manifest"])

    def tearDown(self):
        self.temp.cleanup()

    def test_active_context_is_workspace_scoped_and_inputs_are_hashed(self):
        active = run.for_workspace(self.workspace)
        self.assertEqual(active[0], self.manifest)
        self.assertEqual(active[1]["inputs"][0]["sha256"], run._input("x", self.reference)["sha256"])
        other = self.workspace / "other"
        other.mkdir()
        self.assertIsNone(run.for_workspace(other))

    def test_reference_research_job_is_saved_and_later_steers_tagged_handoff(self):
        import pacific_gym.nimble_research as research

        vision = {"tag": "local-test-model", "digest": "sha256:test"}
        job = {"run_id": "nimble-run", "agent_id": "nimble-agent", "status": "queued",
               "is_active": True}
        completed = {
            "status": "completed", "is_active": False, "request_id": "request-1",
            "content": "Use articulation docs for joint setup.",
            "trust": {"sources": [{"title": "Isaac Sim articulation",
                                    "url": "https://docs.nvidia.com/isaacsim/articulation"}],
                      "claims": []},
        }
        with patch.object(research, "caption_image", return_value=("A neutral articulated figure", vision)), \
                patch.object(research, "start_nimble_job", return_value=job), \
                patch.object(research, "poll_nimble_job", return_value=completed):
            started = research.start_run_research(self.manifest, "test-key", "local-test-model")
            self.assertEqual(started["status"], "running")
            self.assertEqual(run.load(self.manifest)["reference_research"]["job"]["run_id"], "nimble-run")
            finished = research.poll_run_research(self.manifest, "test-key")

        self.assertEqual(finished["status"], "complete")
        self.assertEqual(finished["source_count"], 1)
        receipt = json.loads(Path(finished["receipt"]).read_text())
        self.assertFalse(receipt["input"]["media_sent_to_nimble"])
        self.assertEqual(receipt["tagged_references"][0]["used_by"], "Isaac Sim handoff")
        self.assertTrue(Path(finished["flux_prompt"]).is_file())
        self.assertTrue(Path(finished["reference_map"]).is_file())
        self.assertIn("cultural-industrial-references", run.goal_text(run.load(self.manifest)))

    def test_nimble_key_loader_reads_only_the_named_workspace_setting(self):
        import pacific_gym.nimble_research as research

        (self.workspace / ".env").write_text("OTHER_SETTING=preserved\nNIMBLE_API_KEY='test-key-value'\n")
        with patch.dict(os.environ, {"NIMBLE_API_KEY": ""}):
            self.assertEqual(research.configured_key(self.workspace), "test-key-value")
        with patch.dict(os.environ, {"NIMBLE_API_KEY": "environment-key"}):
            self.assertEqual(research.configured_key(self.workspace), "environment-key")

    def test_reference_tags_require_a_domain_boundary_for_official_hosts(self):
        import pacific_gym.nimble_research as research

        refs = research.tag_references({"sources": [
            {"title": "Artist guide", "url": "https://docs.blender.org/manual"},
            {"title": "Artist guide", "url": "https://blender.org.attacker.example/manual"},
            {"title": "Engineering guide", "url": "https://docs.nvidia.com/isaac"},
            {"title": "Engineering guide", "url": "https://nvidia.com.attacker.example/isaac"},
        ], "claims": []})
        self.assertEqual(refs[0]["used_by"], "Blender authoring")
        self.assertEqual(refs[1]["used_by"], "Review before use")
        self.assertEqual(refs[2]["used_by"], "Isaac Sim handoff")
        self.assertEqual(refs[3]["used_by"], "Review before use")

    def test_manifest_load_normalizes_a_symlink_alias_to_its_resolved_path(self):
        resolved_workspace = self.workspace.resolve()
        alias_root = resolved_workspace / "mount-alias"
        alias_root.symlink_to(resolved_workspace, target_is_directory=True)
        alias_manifest = alias_root / self.manifest.relative_to(resolved_workspace)
        data = json.loads(self.manifest.read_text())
        data["manifest"] = str(alias_manifest)
        self.manifest.write_text(json.dumps(data))

        loaded = run.load(alias_manifest)

        self.assertEqual(loaded["manifest"], str(self.manifest.resolve()))

    def test_candidate_requires_png_and_feedback_is_tied_to_candidate_hash(self):
        candidate = run.add_candidate(self.manifest, self.png, "render")
        receipt = {"inputs": [{"sha256": "ref"}, {"sha256": candidate["sha256"]}],
                   "comparison": {"confidence": "high"}}
        run.add_feedback(self.manifest, candidate["id"], receipt)
        self.assertEqual(run.load(self.manifest)["feedback"][0]["candidate_id"], candidate["id"])
        receipt["inputs"][1]["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "does not match"):
            run.add_feedback(self.manifest, candidate["id"], receipt)

    def test_completion_requires_both_acceptance_gates_or_blocker(self):
        with self.assertRaisesRegex(ValueError, "requires valid USD"):
            run.complete(self.manifest)
        run.set_blocker(self.manifest, "No supported GPU host is available for this run.",
                        "Environment probe returned macOS arm64 and no Isaac Sim executable.")
        self.assertEqual(run.load(self.manifest)["state"], "blocked")

    def test_session_start_restores_only_current_workspace_context(self):
        event = json.dumps({"cwd": str(self.workspace), "source": "startup"})
        env = {**os.environ, "PLUGIN_ROOT": str(PLUGIN)}
        result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "session_start.py")],
                                input=event, capture_output=True, text=True, env=env, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(run.load(self.manifest)["run_id"], output["hookSpecificOutput"]["additionalContext"])

    def test_reference_research_hook_injects_skill_and_credential_path(self):
        event = json.dumps({"cwd": str(self.workspace), "source": "startup"})
        env = {key: value for key, value in os.environ.items() if key != "NIMBLE_API_KEY"}
        env["PLUGIN_ROOT"] = str(PLUGIN)
        result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "reference_research.py")],
                                input=event, capture_output=True, text=True, env=env, check=True)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("cultural-industrial-references", context)
        self.assertIn("run-research-start", context)
        self.assertEqual(run.load(self.manifest)["reference_research"]["status"],
                         "waiting_for_credentials")

    def test_stop_guard_continues_instead_of_claiming_incomplete_run(self):
        event = json.dumps({"cwd": str(self.workspace)})
        env = {**os.environ, "PLUGIN_ROOT": str(PLUGIN)}
        result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "stop_guard.py")],
                                input=event, capture_output=True, text=True, env=env, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("repeated stable forward walking", output["reason"])

    def test_stop_guard_rejects_forged_complete_label_without_hash_verified_proof(self):
        data = run.load(self.manifest)
        data["state"] = "complete"
        data["acceptance"] = {"usd_articulation_valid": True,
                              "stable_forward_walking_observed_on_gpu": True}
        run.save(data)
        event = json.dumps({"cwd": str(self.workspace)})
        env = {**os.environ, "PLUGIN_ROOT": str(PLUGIN)}
        result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "stop_guard.py")],
                                input=event, capture_output=True, text=True, env=env, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("hash-verified Isaac Sim receipt", output["reason"])

    def test_stop_guard_rejects_unstructured_blocker_label(self):
        data = run.load(self.manifest)
        data["state"] = "blocked"
        data["blocker"] = {"reason": "blocked", "evidence": None}
        run.save(data)
        event = json.dumps({"cwd": str(self.workspace)})
        env = {**os.environ, "PLUGIN_ROOT": str(PLUGIN)}
        result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "stop_guard.py")],
                                input=event, capture_output=True, text=True, env=env, check=True)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_post_tool_use_compares_only_explicit_candidate_add(self):
        import pacific_gym.compare as compare
        import pacific_gym.run as run_module
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import post_tool_use
        sys.path.remove(str(PLUGIN / "hooks"))
        frame = self.workspace / "frame.png"
        frame.write_bytes(b"frame")
        data = run.load(self.manifest)
        data["inputs"].append({**run._input("reference_frame", frame)})
        run.save(data)
        candidate = run.add_candidate(self.manifest, self.png, "render")
        receipt = {"inputs": [{"sha256": "frame"}, {"sha256": candidate["sha256"]}],
                   "comparison": {"confidence": "high", "evidence": "matched", "next_action": "keep"}}
        event = {"cwd": str(self.workspace), "tool_name": "Bash",
                 "tool_input": {"command": "python -m pacific_gym candidate-add"},
                 "tool_response": f"PACIFIC_GYM_CANDIDATE={candidate['id']}"}
        stdout = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(event))), patch("sys.stdout", stdout), \
                patch.object(compare, "compare_general", return_value=receipt):
            self.assertEqual(post_tool_use.main(), 0)
        self.assertIn("comparison recorded", stdout.getvalue())
        self.assertEqual(len(run.load(self.manifest)["feedback"]), 1)
        event["tool_input"]["command"] = "python -m pacific_gym run-status"
        stdout = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(event))), patch("sys.stdout", stdout), \
                patch.object(compare, "compare_general", side_effect=AssertionError("should not compare")):
            self.assertEqual(post_tool_use.main(), 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_post_tool_use_supports_codex_exec_command_payload(self):
        import pacific_gym.compare as compare
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import post_tool_use
        sys.path.remove(str(PLUGIN / "hooks"))
        frame = self.workspace / "frame.png"
        frame.write_bytes(b"frame")
        data = run.load(self.manifest)
        data["inputs"].append({**run._input("reference_frame", frame)})
        run.save(data)
        candidate = run.add_candidate(self.manifest, self.png, "render")
        receipt = {
            "inputs": [{"sha256": "frame"}, {"sha256": candidate["sha256"]}],
            "visual_pair": {"sha256": "pair-hash"},
            "model": {"tag": "liquid", "digest": "resolved-model-digest"},
            "comparison": {"confidence": "high", "evidence": "matched", "next_action": "keep"},
        }
        event = {
            "hook_event_name": "PostToolUse",
            "cwd": str(self.workspace),
            "tool_name": "exec_command",
            "tool_input": {"cmd": "python -m pacific_gym candidate-add render.png"},
            "tool_response": {"content": [{"type": "text", "text": f"PACIFIC_GYM_CANDIDATE={candidate['id']}"}]},
        }
        stdout = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(event))), patch("sys.stdout", stdout), \
                patch.object(compare, "compare_general", return_value=receipt):
            self.assertEqual(post_tool_use.main(), 0)
        self.assertIn("comparison recorded", stdout.getvalue())
        feedback = run.load(self.manifest)["feedback"]
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0]["receipt"]["inputs"][1]["sha256"], candidate["sha256"])
        self.assertEqual(feedback[0]["receipt"]["visual_pair"]["sha256"], "pair-hash")
        self.assertEqual(feedback[0]["receipt"]["model"]["digest"], "resolved-model-digest")

        event["tool_input"]["cmd"] = "python -m pacific_gym run-status"
        stdout = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(event))), patch("sys.stdout", stdout), \
                patch.object(compare, "compare_general", side_effect=AssertionError("should not compare")):
            self.assertEqual(post_tool_use.main(), 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_isaac_adapter_receipt_must_prove_hashed_video_and_walking(self):
        usd = self.workspace / "robot.usda"
        usd.write_text("#usda 1.0\n")
        adapter = self.workspace / "adapter.py"
        adapter.write_text(
            "import argparse,hashlib,json,pathlib\n"
            "p=argparse.ArgumentParser(); p.add_argument('--usd'); p.add_argument('--receipt'); p.add_argument('--proof'); a=p.parse_args()\n"
            "video=pathlib.Path(a.proof); video.write_bytes(b'video-proof')\n"
            "r={'schema_version':1,'usd_input_sha256':hashlib.sha256(pathlib.Path(a.usd).read_bytes()).hexdigest(),"
            "'host_os':'Linux','gpu':{'name':'Test GPU'},'usd_articulation_valid':True,"
            "'walking':{'stable_forward':True,'fall_detected':False,'invalid_physics_state':False,"
            "'completed_cycles':3,'net_forward_displacement_m':0.8},"
            "'proof_video_sha256':hashlib.sha256(video.read_bytes()).hexdigest()}\n"
            "pathlib.Path(a.receipt).write_text(json.dumps(r))\n")
        out_receipt = self.workspace / ".pacific-gym" / "runs" / run.load(self.manifest)["run_id"] / "receipts" / "isaac.json"
        video = self.workspace / ".pacific-gym" / "runs" / run.load(self.manifest)["run_id"] / "proof" / "walking.mp4"
        bin_dir = self.workspace / "bin"
        bin_dir.mkdir()
        gpu_query = bin_dir / "nvidia-smi"
        gpu_query.write_text("#!/bin/sh\nprintf 'Test GPU\\n'\n")
        gpu_query.chmod(0o755)
        with patch("pacific_gym.integrations.platform.system", return_value="Linux"), \
                patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}):
            result = isaac_run(self.manifest, sys.executable, adapter, usd, out_receipt, video, [])
        self.assertTrue(result["stable_forward_walking_observed_on_gpu"])
        self.assertEqual(run.load(self.manifest)["acceptance"]["usd_articulation_valid"], True)
        self.assertTrue(run.complete(self.manifest)["state"] == "complete")

    def test_isaac_run_rejects_unsupported_platform_before_launch(self):
        with patch("pacific_gym.integrations.platform.system", return_value="Darwin"):
            with self.assertRaisesRegex(ValueError, "requires a supported Linux or Windows GPU host"):
                isaac_run(self.manifest, "missing-isaac", Path("missing.py"), self.reference,
                          self.workspace / "receipt.json", self.workspace / "walking.mp4", [])


if __name__ == "__main__":
    unittest.main()
