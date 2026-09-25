---
name: keyframe-generation
description: Turn a pinned FLUX motion reference into timestamped, reviewed Blender animation keyframes without claiming Isaac Sim physics acceptance.
---

# Keyframe generation and visual review

Use this skill to create and refine animation poses from a pinned FLUX gait reference. This workflow owns reference sampling, Blender candidate rendering, timestamp-paired visual review, and keyframe iteration. It does not prepare or certify an Isaac Sim robot.

## Use these services and tools

- For a new motion reference, use the bundled official Black Forest Labs `flux` MCP server. Discover its current video-generation tool and arguments from the server rather than guessing a tool name. Request FLUX 3 Video image-to-video from the approved, hash-pinned start render, with the requested style and settings. BFL bills the authenticated organization for generations; generate only when the user asked for a new reference. Save the returned video into the active run, record the request/settings and SHA-256, and keep any remote result URL as provenance rather than as the only copy.
- Use `scripts/extract-full-video-frames.py --video <run-local-video> --out <run-local-reference-frames> --fps 2` to extract uncropped frames and write a source-hash and timestamp manifest. Adjust the sample rate only when the motion needs it and record the chosen rate.
- Use local Ollama for image-pair review, with `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` unless the user configured an explicit override. Check the resolved digest against `proof/slice-06/proof.json` when relying on the controlled fixture. The built-in `judge-keyframes` command calls Ollama locally and records its version and model digest; do not send candidate or source frames to another vision service.
- Use Blender's background Python interface to produce animation renders. Do not use a general Blender MCP for this workflow: Pacific Gym's scripted path keeps the target files and outputs bounded and hash-checked.

## Inputs and output ownership

- Use the reviewed, pinned FLUX video and its frame manifest as the motion target. Preserve source video and sampled reference frames as immutable inputs.
- Work under the active run's `.pacific-gym/runs/<run-id>/` directory. Keep synthetic keyframes, completion sidecars, and judgments there; do not overwrite source or pinned reference files.
- Match each Blender render to the exact reference `frame_id` and `timestamp_seconds`. Render every sample timestamp; do not infer or judge missing frames.
- If no timestamped reference manifest exists, use the bundled FLUX MCP when a new reference is requested, then sample it with the repository script above. Record the source hash and frame timestamps before authoring candidates.

## Render and publish candidates

Render one Blender PNG per manifest timestamp into a run-local candidate directory. Only publish a frame after Blender has finished writing it. Use the manifest's frame ID and exact timestamp:

```sh
python3 -m pacific_gym publish-candidate-frame \
  --reference-manifest /absolute/path/reference-frames/manifest.json \
  --candidate-dir /absolute/path/.pacific-gym/runs/<run-id>/keyframes \
  --frame-id frame-001 --timestamp 0.0 \
  --image /absolute/path/.pacific-gym/runs/<run-id>/keyframes/frame-001.png
```

Repeat for every frame listed in the reference manifest. The command verifies the frame ID and timestamp and writes a completion record containing the candidate hash. Do not rename or modify a published PNG; render and publish a replacement after making changes.

## Pair-gated visual review

Run the local watcher against the exact manifest and candidate directory:

```sh
python3 -m pacific_gym judge-keyframes \
  --reference-manifest /absolute/path/reference-frames/manifest.json \
  --candidate-dir /absolute/path/.pacific-gym/runs/<run-id>/keyframes \
  --out-dir /absolute/path/.pacific-gym/runs/<run-id>/keyframe-judgments
```

The watcher waits for the complete timestamp set. It checks reference hashes, matching frame IDs and timestamps, candidate completion and hash, and equal image dimensions before judging. It holds candidates whose visible foreground reaches the right image edge; correct framing and republish those frames. Review the returned paired images and advisory results, refine Blender animation as needed, and publish revised PNGs to a fresh candidate directory with a fresh output directory when prior judgments conflict. For the separate controlled static-pose comparison pulse, use local Ollama through `sh scripts/accept-comparison-pulse.sh`; that fixture is not a general gait judge.

Visual judgments help direct iteration. They do not establish planted-foot stability, rig validity, collision setup, articulation validity, or physics. Do not report Isaac acceptance from this workflow. For hash-tracked Blender derivatives and the Isaac Sim articulation and walking gate, use `isaac-ready`.

## Boundary with other plugin work

After a successful `publish-candidate-frame` CLI call, the PostToolUse hook runs a one-shot local scan automatically. It judges only when the complete timestamp set is present and hash-verified; earlier publishes produce a hook receipt showing the waiting state. Judgments are written under the active run's `keyframe-judgments/` directory and hook trigger receipts under `receipts/keyframe-hooks/`. Ollama or schema failures remain advisory and do not promote candidates. The explicit `judge-keyframes` watcher remains available for continuous waiting. The separate `candidate-add` hook compares renders to pinned run frames and does not replace synchronized keyframe review. Private source intake and Isaac Sim GPU validation belong to `isaac-ready`. FLUX motion video is generated with the bundled `flux` MCP; keyframe extraction, Blender rendering, and Ollama review remain local.
