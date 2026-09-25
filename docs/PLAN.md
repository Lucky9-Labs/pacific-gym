# Delivery plan

## Contract

Build the plugin in sequential feature worktrees. Before each worktree, fetch `origin`, safely switch the primary checkout to `main`, and fast-forward it to `origin/main`. Each slice has one local acceptance command and a proof record with input hashes, tool/model versions, results, and a visual artifact when useful. Merge its focused PR before starting the next slice.

A passing output is promoted only after its exact hash and immutable S3 VersionId are recorded in a run manifest. Downstream slices and the GPU agent consume those bytes. Failed experiments remain in run traces but do not become inputs.

The local vision comparison pulse uses Liquid AI's official `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` through Ollama. Pin the resolved model digest in each proof record; the tested digest for the controlled fixture is `4e3daebe4fb42e458b26090e6b5e6b38d34ddd1ff9f0673565d83acb3a2c5682`. The tag alone can move. The plugin now compares only explicitly recorded candidate renders against pinned reference PNGs; this is visual feedback and not a gait or physics gate.

## Slices

| Order | Integration | Local acceptance |
| --- | --- | --- |
| 1 | Plugin shell and source intake | Install and invoke the skill and trusted hook; inspect the visual GLB and unanimated rig reference without modification and verify their hashes and different rig structures. |
| 2 | RawTree traces | Send a sandbox trace with prompts, tool I/O, decisions, and artifact references; query it back by run ID with credentials redacted. |
| 3 | FLUX 3 Video | Make a live small generation from source renders and a style prompt; review form, foot contacts, and style; pin an acceptable video. |
| 4 | Keyframes | Extract timestamped frames with ffmpeg and label them with a local Liquid VLM in Ollama; reproduce the reference from the pinned video. |
| 5 | Blender tooling | Run a background script on read-only staged inputs, hash and receipt each derivative, preserve originals, and inspect the result from multiple views. Production GLB authoring starts on the target computer after the FLUX gait is pinned. |
| 6 | Comparison hook | Selectively compare explicit candidate-add outputs to pinned keyframes, record exact hashes and feedback in the workspace run. |
| 7 | USD candidate | Check layered USD structure, references, articulation metadata, and package completeness locally. |

## GPU gate

The starter workflow creates a Codex task Goal only when the user explicitly requests persistence. Freeze the exact manifest of proven inputs and derivatives and hand it to an Isaac Sim scenario adapter on a supported GPU host. The adapter validates the articulation and runs repeated forward walking with stable contacts, positive net displacement, and no fall or invalid physics state. Both gates need structured receipts and video evidence. Otherwise record a concrete blocker and supporting evidence. FLUX 3 Action live control is a later milestone.
