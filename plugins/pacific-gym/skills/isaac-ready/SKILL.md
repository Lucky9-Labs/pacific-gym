---
name: isaac-ready
description: Inspect immutable GLB sources, build versioned derivatives, and gather one proof per integration before an Isaac Sim GPU run.
---

# Isaac-ready asset workflow

Read the repository's `docs/PLAN.md` and `docs/ARCHITECTURE.md` before starting. Keep original source bytes unchanged. Give every run a unique ID, verify SHA-256 for each input, and save reports and artifacts outside Git-tracked source paths.

For the Strokah fixture, inspect with:

```sh
cd plugins/pacific-gym
python3 -m pacific_gym inspect --spec fixtures/strokah-source.json --out .pacific-gym/inspect.json
```

The source inspection report must distinguish the static visual asset from the unanimated mechanical rig reference. Do not request or infer a premade gait asset. A skin name or similar part names alone are not proof of matching bindings. FLUX creates the gait visual before production motion authoring begins on the target machine. Record an acceptance command and its output in `proof/index.json` before promoting a derivative.

For the local static comparison pulse, use Liquid AI's official Ollama model `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M`. Check its resolved digest against `proof/slice-06/proof.json` and run `sh scripts/accept-comparison-pulse.sh` from the repository root. This command is manual; the installed plugin currently has no comparison `PostToolUse` hook and does not auto-steer Codex.

Only claim Isaac Sim readiness after a GPU-host run loads the USD articulation and demonstrates stable forward walking. Local validation on macOS proves narrower properties.
