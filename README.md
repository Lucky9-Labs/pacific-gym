# Pacific Gym

Pacific Gym is a Codex plugin for turning a generated 3D character and a mechanical rig reference into an Isaac Sim robot candidate. Its first fixture is Strokah. The repository records a proof for each integration before a long-running GPU agent attempts a forward-walking simulation.

Start with [the delivery plan](docs/PLAN.md), [the architecture](docs/ARCHITECTURE.md), and [the proof index](proof/index.json). A passing feature worktree updates the proof index before it is merged.

The current Strokah source packet is a reference handoff, not a validated physics asset. The gameplay LOD0 has no skeleton; the clean mechanical rig reference has 62 joints and no animation. FLUX 3 Video will generate the gait visual before the target machine starts authoring motion for the GLB derivative.

The first local acceptance command is `sh scripts/accept-source-intake.sh`. The repo-local plugin is in `plugins/pacific-gym` and its marketplace entry is in `.agents/plugins/marketplace.json`. Codex must trust a plugin hook before that hook executes in a session.

The controlled Liquid vision comparison pulse uses Liquid AI's official `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M` model in local Ollama. Its exact tested digest, setup, and visual proof are in [proof/slice-06/README.md](proof/slice-06/README.md). Run it with `sh scripts/accept-comparison-pulse.sh`. This is a manual command; the plugin does not yet register a comparison hook or auto-steer Codex.
