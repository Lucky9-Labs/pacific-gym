# Architecture

## Inputs and ownership

A run accepts a static source GLB, a separate mechanical rig reference, a gait style prompt, credentials for external services, and an output root. It does not require a premade gait asset. Every input is identified by an immutable URI or local path plus SHA-256. Originals are read-only. Each transformation writes a derivative under its own run ID and records the tool and model version that produced it.

Strokah is the first fixture. The versioned source packet at `s3://mech-art-library-20260905225047699700000001/models/mechs/strokah-skeleton/isaacsim-source/v1/` contains the clean static rig reference and the gameplay LOD0. A preflight must fetch and verify exact object versions before work begins.

## Plugin components

- The repo marketplace installs `plugins/pacific-gym`, whose Codex skill guides source inspection, visual reference generation, Blender repairs, and the final GPU goal.
- Local command tools expose repeatable `inspect`, `trace`, `generate-video`, `keyframes`, `blender-derive`, `compare`, `judge-keyframes`, `validate-usd`, and `promote` steps. Each produces machine-readable results and one documented acceptance command.
- `compare` remains a manual static-pose pulse. `judge-keyframes` can watch for completed Blender PNGs and invokes local Liquid AI only after it verifies the exact FLUX frame ID, timestamp, source hash, candidate completion hash, and image dimensions. Each advisory result stores both inputs and the comparison image. It does not infer missing pairs or claim physics acceptance.
- The planned trusted `PostToolUse` hook is still unwired. The package has a `SessionStart` install check and the explicit pair watcher, but it does not yet automatically observe arbitrary candidate-producing tool batches or auto-steer Codex.
- RawTree stores full textual run traces and metadata after credential redaction. Large media lives in the versioned artifact package, with URI and hash in the trace.

## Reference and repair loop

Use Liquid AI's official `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` in local Ollama for the initial comparison pulse. Record the resolved digest in each run; `proof/slice-06/proof.json` pins the tested digest. The controlled fixture also measures the orange foot contact in the rendered PNGs because an unassisted VLM trial reversed the spatial direction. Do not treat that fixture proof as a general gait comparison.

Render the immutable source from useful views. FLUX 3 Video produces the first gait visual from those images and the style prompt. Once reviewed, the video becomes the motion target. ffmpeg sampling and local VLM labels create a permanent timestamped keyframe reference. Local Blender exercises prove the repair tooling; production GLB authoring begins on the target machine from the pinned references. USD layers add robot links, joints, collision, mass, inertia, and drives. Visual comparison directs iteration; structural checks and Isaac Sim runtime proof decide physics acceptance.

## Proof boundary

This Mac can validate plugin behavior, live API calls, source hashes, Blender derivatives, visual comparisons, traces, and USD structure. Isaac Sim runtime acceptance happens on a supported Linux or Windows GPU host. A local USD check is not a claim that the robot walks. The handoff contains exact artifact versions and proof links so the long-horizon agent starts from known-good inputs.
