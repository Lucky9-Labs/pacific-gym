# Pacific Gym

Pacific Gym is a Codex plugin for turning a generated 3D character and a mechanical rig reference into an Isaac Sim robot candidate. Its first fixture is Strokah. The repository records a proof for each integration before a long-running GPU agent attempts a forward-walking simulation.

Start with [the delivery plan](docs/PLAN.md), [the architecture](docs/ARCHITECTURE.md), and [the proof index](proof/index.json). A passing feature worktree updates the proof index before it is merged.

The current Strokah source packet is a reference handoff, not a validated physics asset. The gameplay LOD0 has no skeleton; the clean mechanical rig reference has 62 joints and no animation. FLUX 3 Video will generate the gait visual before the target machine starts authoring motion for the GLB derivative.

The first local acceptance command is `sh scripts/accept-source-intake.sh`. The repo-local plugin is in `plugins/pacific-gym` and its marketplace entry is in `.agents/plugins/marketplace.json`. Codex must trust a plugin hook before that hook executes in a session.

The latest industrial FLUX 3 Video candidate is [the full MP4](proof/slice-03/strokah-flux3-industrial.mp4). Its [S3 publication receipt](proof/slice-03/s3-publication.json) records immutable object versions and verified readback hashes for the MP4 and [Astra handoff manifest](proof/slice-03/s3-industrial-manifest.json). The manifest includes the full video, source asset versions, prompt, settings, and uncropped frame sampling details. This is visual direction only: planted feet drift, so gait and physics are not accepted.

For another BFL run, put `BFL_API_KEY` in a local root `.env` using [.env.example](.env.example) as the template, or export it in the environment. The runner reads either source. `.env` is ignored by Git; credentials are never part of the reference packet. Validate local artifacts with `sh scripts/accept-flux3-gait.sh`.
For local installs, `.codex-plugin/plugin.json` is the package manifest. Codex CLI 0.157.0 discovered the bundled `SessionStart` hook with this layout; a simultaneous portable root `plugin.json` hid the hook in runtime testing. Revalidate hook discovery before adding a portable root manifest. During local iteration, update the manifest cache version before reinstalling so Codex loads the changed package.

RawTree trace acceptance is `sh scripts/accept-rawtree-trace.sh`. It runs local trace/redaction checks, then uses `RAWTREE_API_KEY` (or `--api-key-file /absolute/path` passed to the script) to insert redacted event rows into `luckybucky_hackathon` and query them back by run ID. Every row includes the Codex session and thread IDs. RawTree table names cannot contain hyphens, and nested tool input/result values are stored as canonical JSON strings to prevent RawTree from flattening their keys into dotted columns. RawTree separates write ingestion from its read-only SQL query API; its published MCP reference documents whole-table deletion with admin permission, but no row-delete operation. A write-enabled data key therefore does not make mutation SQL available through the query endpoint. The acceptance command records this limitation and exits 2 after a successful round-trip until a supported row-level cleanup mechanism is available. See [the RawTree trace notes](proof/slice-02/README.md) for the live verification state and source links. Proof excludes API keys and media bytes. The current fixture is a representative replay of the source inspection proof, so its capture timing does not claim to measure the original inspection run.

The controlled Liquid vision comparison pulse uses Liquid AI's official `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` model in local Ollama. Its exact tested digest, setup, and visual proof are in [proof/slice-06/README.md](proof/slice-06/README.md). Run it with `sh scripts/accept-comparison-pulse.sh`. The installed plugin also compares explicit `candidate-add` renders against pinned frames; this returns advisory visual feedback and does not establish rig validity, physics, or walking. The fresh CLI hook proof is in [proof/slice-07/README.md](proof/slice-07/README.md).

For animation review, `pacific-gym judge-keyframes` watches for timestamp-paired FLUX reference frames and Blender candidate renders. Render each candidate to a PNG with the frame ID from the reference manifest, then mark it complete only after Blender finishes writing it:

```sh
PYTHONPATH=plugins/pacific-gym python3 -m pacific_gym publish-candidate-frame \
  --reference-manifest proof/slice-03/industrial-full-frames/manifest.json \
  --candidate-dir .pacific-gym/runs/<run-id>/synthetic-keyframes \
  --frame-id frame-001 --timestamp 0.0 \
  --image .pacific-gym/runs/<run-id>/synthetic-keyframes/frame-001.png

PYTHONPATH=plugins/pacific-gym python3 -m pacific_gym judge-keyframes \
  --reference-manifest proof/slice-03/industrial-full-frames/manifest.json \
  --candidate-dir .pacific-gym/runs/<run-id>/synthetic-keyframes \
  --out-dir .pacific-gym/runs/<run-id>/paired-judgments
```

The watcher checks immutable reference hashes, matching frame IDs and timestamps, candidate completion/hash, and equal image dimensions before calling local Ollama. It emits one structured advisory result per completed pair and waits on missing or mismatched frames. Stop it with Ctrl-C. The separate `PostToolUse` hook compares explicit `candidate-add` renders against pinned run frames; neither path establishes physics, gait, or Isaac Sim acceptance. Run the deterministic keyframe gate checks with `PYTHONPATH=plugins/pacific-gym python3 -m unittest discover -s plugins/pacific-gym/tests -p 'test_keyframe_judge.py' -v`.
