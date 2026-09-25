# Pacific Gym

Pacific Gym is a Codex plugin for turning a generated 3D character and a mechanical rig reference into an Isaac Sim robot candidate. Its first fixture is Strokah. The repository records a proof for each integration before a long-running GPU agent attempts a forward-walking simulation.

Start with [the delivery plan](docs/PLAN.md), [the architecture](docs/ARCHITECTURE.md), and [the proof index](proof/index.json). A passing feature worktree updates the proof index before it is merged.

The current Strokah source packet is a reference handoff, not a validated physics asset. The gameplay LOD0 has no skeleton; the clean mechanical rig reference has 62 joints and no animation. FLUX 3 Video will generate the gait visual before the target machine starts authoring motion for the GLB derivative.

The first local acceptance command is `sh scripts/accept-source-intake.sh`. The repo-local plugin is in `plugins/pacific-gym` and its marketplace entry is in `.agents/plugins/marketplace.json`. Codex must trust a plugin hook before that hook executes in a session.

The latest industrial FLUX 3 Video candidate is [the full MP4](proof/slice-03/strokah-flux3-industrial.mp4). Its [S3 publication receipt](proof/slice-03/s3-publication.json) records immutable object versions and verified readback hashes for the MP4 and [Astra handoff manifest](proof/slice-03/s3-industrial-manifest.json). The manifest includes the full video, source asset versions, prompt, settings, and uncropped frame sampling details. This is visual direction only: planted feet drift, so gait and physics are not accepted.

For another BFL run, put `BFL_API_KEY` in a local root `.env` using [.env.example](.env.example) as the template, or export it in the environment. The runner reads either source. `.env` is ignored by Git; credentials are never part of the reference packet. Validate local artifacts with `sh scripts/accept-flux3-gait.sh`.

RawTree trace acceptance is `sh scripts/accept-rawtree-trace.sh`. It runs local trace/redaction checks, then uses `RAWTREE_API_KEY` (or `--api-key-file /absolute/path` passed to the script) to insert a redacted development trace and query it back by run ID. Without a key it writes a blocked proof under `proof/slice-02` and exits 2. The proof contains no API key or media bytes. This is a representative replay of the source inspection proof, so its capture timing does not claim to measure the original inspection run.

The controlled Liquid vision comparison pulse uses Liquid AI's official `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` model in local Ollama. Its exact tested digest, setup, and visual proof are in [proof/slice-06/README.md](proof/slice-06/README.md). Run it with `sh scripts/accept-comparison-pulse.sh`. This is a manual command; the plugin does not yet register a comparison hook or auto-steer Codex.
