# Architecture

## Inputs and ownership

A run accepts a static source GLB, a separate rig and motion GLB, a gait style prompt, credentials for optional external services, and an output root. Every input is identified by an immutable URI or local path plus SHA-256. Originals are read-only. Each transformation writes a derivative under its own run ID and records the tool and model version that produced it.

Strokah is the first fixture. The versioned source packet at `s3://mech-art-library-20260905225047699700000001/models/mechs/strokah-skeleton/isaacsim-source/v1/` contains the clean static rig reference and the gameplay LOD0. The original gait GLB remains a separate input. A preflight must fetch and verify exact object versions before work begins.

## Plugin components

- A Codex skill guides source inspection, visual reference generation, Blender repairs, and the final GPU goal.
- Local command tools expose repeatable `inspect`, `trace`, `generate-video`, `keyframes`, `blender-derive`, `compare`, `validate-usd`, and `promote` steps. Each produces machine-readable results and one documented acceptance command.
- A trusted `PostToolUse` hook observes candidate-producing batches while a Pacific Gym run is active. It invokes the local Liquid VLM against pinned reference frames, returns concise feedback to Codex, and writes a trace. It never changes the source asset.
- RawTree stores full textual run traces and metadata after credential redaction. Large media lives in the versioned artifact package, with URI and hash in the trace.

## Reference and repair loop

Render the immutable source from useful views. FLUX 3 Video produces a gait video from those images and the style prompt. Once reviewed, the video becomes the primary motion target, even where it differs from the source gait. ffmpeg sampling and local VLM labels create a permanent timestamped keyframe reference. Blender repairs visual geometry and rig correspondence. USD layers add robot links, joints, collision, mass, inertia, and drives. Visual comparison directs iteration; structural checks and Isaac Sim runtime proof decide physics acceptance.

## Proof boundary

This Mac can validate plugin behavior, live API calls, source hashes, Blender derivatives, visual comparisons, traces, and USD structure. Isaac Sim runtime acceptance happens on a supported Linux or Windows GPU host. A local USD check is not a claim that the robot walks. The handoff contains exact artifact versions and proof links so the long-horizon agent starts from known-good inputs.
