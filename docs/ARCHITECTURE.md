# Architecture

## Inputs and ownership

A run accepts a static source GLB, a separate mechanical rig reference, a gait style prompt, credentials for external services, and an output root. It does not require a premade gait asset. Every input is identified by an immutable URI or local path plus SHA-256. Originals are read-only. Each transformation writes a derivative under its own run ID and records the tool and model version that produced it.

Strokah is the first fixture. The versioned source packet at `s3://mech-art-library-20260905225047699700000001/models/mechs/strokah-skeleton/isaacsim-source/v1/` contains the clean static rig reference and the gameplay LOD0. A preflight must fetch and verify exact object versions before work begins.

## Plugin components

- The repo marketplace installs `plugins/pacific-gym`. Its `keyframe-generation` skill guides timestamped animation authoring and advisory visual review against FLUX frames. Its `isaac-ready` skill guides immutable asset preparation and the final GPU articulation and walking gate. When the user's request explicitly asks to persist through completion, the agent creates the Codex task Goal; hooks never create Goals.
- The plugin packages the official Black Forest Labs FLUX MCP at `https://mcp.bfl.ai` for user-requested motion-reference generation. Skills call local Ollama through the bounded HTTP adapters and use Pacific Gym's hash-checked CLI wrappers for Blender and Isaac Sim. The optional NVIDIA Isaac Sim MCP provides API documentation search only and requires a separately deployed service; it is not the simulation runner. The general Blender MCP is excluded because it runs generated code without safety guards. RawTree traces use the fixed-purpose adapter rather than the wider RawTree MCP surface.
- Local command tools expose repeatable `inspect`, `trace`, `generate-video`, `keyframes`, `blender-derive`, `compare`, `judge-keyframes`, `validate-usd`, and `promote` steps. Each produces machine-readable results and one documented acceptance command.
- `compare` remains a manual static-pose pulse. `judge-keyframes` can watch for completed Blender PNGs and invokes local Liquid AI only after it verifies the exact FLUX frame ID, timestamp, source hash, candidate completion hash, and image dimensions. Each advisory result stores both inputs and the comparison image. It does not infer missing pairs or claim physics acceptance.
- `SessionStart` restores a run only from the current workspace's `.pacific-gym/active.json`. `PostToolUse` filters for the explicit `candidate-add` command, compares that PNG with pinned references through local Liquid VLM, and stores exact input and image-pair hashes plus feedback in the run manifest. `Stop` rejects a false completion claim until hash-verified Isaac evidence or a structured, evidence-backed blocker exists. VLM edits remain advisory. These hooks do not observe arbitrary tool batches.
- RawTree stores full textual run traces and metadata after credential redaction. Large media lives in the versioned artifact package, with URI and hash in the trace.

## Reference and repair loop

Use Liquid AI's official `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` in local Ollama for the initial comparison pulse. Record the resolved digest in each run; `proof/slice-06/proof.json` pins the tested digest. The controlled fixture also measures the orange foot contact in the rendered PNGs because an unassisted VLM trial reversed the spatial direction. Do not treat that fixture proof as a general gait comparison.

Render the immutable source from useful views. FLUX 3 Video produces the first gait visual from those images and the style prompt. Once reviewed, the video becomes the motion target. ffmpeg sampling and local VLM labels create a permanent timestamped keyframe reference. Local Blender exercises prove the repair tooling; production GLB authoring begins on the target machine from the pinned references. USD layers add robot links, joints, collision, mass, inertia, and drives. Visual comparison directs iteration; structural checks and Isaac Sim runtime proof decide physics acceptance.

## Proof boundary

This Mac can validate plugin behavior, live API calls, source hashes, Blender derivatives, visual comparisons, traces, and USD structure. Isaac Sim runtime acceptance happens on a supported Linux or Windows GPU host. A local USD check is not a claim that the robot walks. The handoff contains exact artifact versions and proof links so the long-horizon agent starts from known-good inputs.
