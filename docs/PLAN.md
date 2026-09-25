# Delivery plan

## Contract

Build the plugin in sequential feature worktrees. Before each worktree, fetch `origin`, safely switch the primary checkout to `main`, and fast-forward it to `origin/main`. Each slice has one local acceptance command and a proof record with input hashes, tool/model versions, results, and a visual artifact when useful. Merge its focused PR before starting the next slice.

A passing output is promoted only after its exact hash and immutable S3 VersionId are recorded in a run manifest. Downstream slices and the GPU agent consume those bytes. Failed experiments remain in run traces but do not become inputs.

## Slices

| Order | Integration | Local acceptance |
| --- | --- | --- |
| 1 | Plugin shell and source intake | Install and invoke the skill and trusted hook; inspect the visual GLB and unanimated rig reference without modification and verify their hashes and different rig structures. |
| 2 | RawTree traces | Send a sandbox trace with prompts, tool I/O, decisions, and artifact references; query it back by run ID with credentials redacted. |
| 3 | FLUX 3 Video | Make a live small generation from source renders and a style prompt; review form, foot contacts, and style; pin an acceptable video. |
| 4 | Keyframes | Extract timestamped frames with ffmpeg and label them with a local Liquid VLM in Ollama; reproduce the reference from the pinned video. |
| 5 | Blender tooling | Import, edit, and export a local test derivative; preserve the source and inspect the result from multiple views. Production GLB authoring starts on the target computer after the FLUX gait is pinned. |
| 6 | Comparison hook | Compare each candidate-producing tool batch to the pinned keyframes; show a deliberate regression and an improvement; find both in RawTree. |
| 7 | USD candidate | Check layered USD structure, references, articulation metadata, and package completeness locally. |

## GPU gate

Freeze a manifest of proven S3 objects and hand it to an Astra agent on the GPU host. Its `/goal` iterates on derivatives until Isaac Sim's validator passes and an actual simulation shows repeated forward walking, stable contacts, and no fall or invalid physics state. If no defensible route remains, it reports the blocker and evidence. FLUX 3 Action live control is a later milestone.
