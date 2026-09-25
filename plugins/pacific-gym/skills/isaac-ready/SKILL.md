---
name: isaac-ready
description: Take an attached 3D reference through immutable, hash-tracked Blender and Isaac Sim work to verified GPU walking proof or a specific documented blocker.
---

# Isaac-ready asset workflow

Read `docs/PLAN.md` and `docs/ARCHITECTURE.md` before starting. Treat attached files as immutable inputs. Keep every derivative, receipt, and proof artifact under `.pacific-gym/runs/<run-id>/`; never write over an input or a tracked source asset.

## Start the user-requested long-running Goal

When the user asks to “Use Pacific Gym to take care of this” and says to keep going until Isaac Sim walking proof or a documented blocker:

1. Inspect the attached reference and identify the workspace-local path. Ask for the file only if it is not available in the task.
2. Start a run and pin useful PNG comparison frames when available:

   ```sh
   python3 -m pacific_gym run-start --workspace "$PWD" --reference /absolute/path/reference.png \
     --reference-frame /absolute/path/frame-0001.png --style "<requested style>"
   ```

   `run-start` prints a run ID, manifest, and explicit Goal text. Create a Codex task Goal only because the user explicitly requested persistence to completion. State the two acceptance gates and the documented-blocker alternative in the Goal. No lifecycle hook creates Goals.
3. Continue until either (a) a validated USD articulation and repeated stable forward walking are evidenced on a supported GPU host, or (b) a concrete blocker and its evidence are recorded with `run-block`. Do not report “complete” after local checks alone.

If the attached input is a GLB or another non-image asset, pass it as `--reference`; pin rendered PNG views separately with `--reference-frame`. For Strokah, source-intake inspection is available with:

```sh
cd plugins/pacific-gym
python3 -m pacific_gym inspect --spec fixtures/strokah-source.json --out .pacific-gym/inspect.json
```

The inspection must distinguish the static visual asset from the unanimated mechanical rig reference. Do not infer shared bindings from names. Do not request or infer a premade gait asset: FLUX supplies visual gait direction before production motion authoring.

## Candidate feedback
For the local static comparison pulse, use Liquid AI's official Ollama model `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M`. Check its resolved digest against `proof/slice-06/proof.json` and run `sh scripts/accept-comparison-pulse.sh` from the repository root.

For animation review, render Blender candidate PNGs at the sample timestamps in the pinned FLUX frame manifest. After each render is complete, run `python3 -m pacific_gym publish-candidate-frame` with the exact frame ID and timestamp, then run `python3 -m pacific_gym judge-keyframes` with the reference manifest, candidate directory, and run-local result directory. The watcher calls Ollama only for complete hash-verified pairs with matching IDs, timestamps, and dimensions. Its visual result is advisory; it does not prove gait or physics. The separate `PostToolUse` hook compares explicit `candidate-add` renders against pinned run frames. See the repository README for command examples.

Record each candidate PNG after a render or candidate-producing batch:

```sh
python3 -m pacific_gym candidate-add --manifest /absolute/path/.pacific-gym/runs/<id>/run.json \
  --image /absolute/path/candidate.png --stage blender-render
```

The installed `PostToolUse` hook reacts only to this explicit command while a run is active. If pinned reference PNGs exist and local Ollama is available, it compares them and records the model digest, source and render hashes, visual-pair hash, evidence, confidence, and next action in the run manifest. Comparison failure is advisory and never promotes a candidate. It does not create a Goal, infer acceptance, or watch unrelated tool batches. Set `PACIFIC_GYM_OLLAMA_HOST` or `PACIFIC_GYM_VISION_MODEL` to override local defaults.

## Blender derivative

Before starting target work, run the target preflight on that machine. It checks the resolved manifest/run path, every pinned input path, writable run storage, Blender and Isaac Sim executable startup probes, supported OS, and a live NVIDIA GPU query. Supply the actual target executable paths; do not infer installation from a prior machine's result:

```sh
python3 -m pacific_gym target-preflight --manifest /absolute/path/.pacific-gym/runs/<id>/run.json \
  --blender /path/to/blender --isaac /path/to/isaac-sim/python.sh
```

Resolve mount symlinks by using the canonical manifest location printed by the tool. The loader accepts a symlink alias only when its recorded manifest resolves to the same file, and normalizes subsequent writes to the canonical path. Stop on any failed prerequisite before running Blender or Isaac Sim.

Use Blender's background Python interface with a script that consumes the passed `--inputs` and `--outputs` lists. The wrapper stages read-only, hash-verified copies under the run, confines outputs to `derivatives/`, checks original and staged hashes after execution, captures Blender's version and stdout/stderr, and writes a JSON receipt with exact input/output SHA-256 values:

```sh
python3 -m pacific_gym blender-derive --manifest /absolute/path/.pacific-gym/runs/<id>/run.json \
  --executable /Applications/Blender.app/Contents/MacOS/blender \
  --script /absolute/path/derive.py --input /absolute/path/source.glb \
  --output /absolute/path/.pacific-gym/runs/<id>/derivatives/robot.glb
```

The script receives `--inputs <paths...> --outputs <paths...>` after Blender's `--`. Review the derivative from useful views. Treat output as a versioned derivative, never as a replacement for source bytes.

## Isaac Sim acceptance

Run on a supported Linux or Windows GPU host with the Isaac Sim Python launcher (`python.sh` or its platform equivalent). Supply a scenario adapter that opens the USD, validates its articulation, steps the actual simulation through at least three complete forward walking cycles, records GPU and motion evidence, and writes the required receipt and video. The wrapper confirms its OS and matches the receipt GPU name against a live `nvidia-smi` query. It stages the USD read-only, checks both staged and original input hashes, requires proof artifacts inside the run, verifies receipt fields and the proof-video hash, then records the result:

```sh
python3 -m pacific_gym isaac-run --manifest /absolute/path/.pacific-gym/runs/<id>/run.json \
  --executable /path/to/isaac-sim/python.sh --script /absolute/path/isaac_walk.py \
  --usd /absolute/path/.pacific-gym/runs/<id>/derivatives/robot.usda \
  --receipt /absolute/path/.pacific-gym/runs/<id>/receipts/isaac.json \
  --proof-video /absolute/path/.pacific-gym/runs/<id>/proof/walking.mp4
```

The adapter receipt schema is JSON with `schema_version: 1`, `usd_input_sha256`, `host_os` (`Linux` or `Windows`), `gpu: {"name": "..."}`, `usd_articulation_valid: true`, `walking: {"stable_forward": true, "fall_detected": false, "invalid_physics_state": false, "completed_cycles": 3, "net_forward_displacement_m": 0.1}`, and `proof_video_sha256`. The wrapper verifies the OS, live GPU name, exact staged USD, receipt, and video hashes again before `run-complete`. A static render, structural USD report, Mac run, or claimed receipt without its video cannot pass.

Only `run-complete` after a validated Isaac receipt can mark the run complete. If GPU access or a defensible scenario route is unavailable, record the blocker and supporting evidence:

```sh
python3 -m pacific_gym run-block --manifest /absolute/path/.pacific-gym/runs/<id>/run.json \
  --reason "<specific blocker>" \
  --evidence "<observed environment, command output, receipt path/hash, or other concrete evidence>"
```

## Session and hook behavior

At Codex SessionStart, the plugin restores an active run only from the current workspace's `.pacific-gym/active.json`. The Stop guard prevents an active run from being called complete until both GPU acceptance fields are present or a blocker has been documented. Hooks are reminders and integrity gates; the manifest and evidence artifacts remain authoritative.

For the local static comparison fixture, the pinned Ollama model and digest are in `proof/slice-06/proof.json`; run `sh scripts/accept-comparison-pulse.sh`. This controlled comparison does not prove gait or physics.
