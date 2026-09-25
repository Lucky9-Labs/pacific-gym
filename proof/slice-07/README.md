# Goal workflow and hook runtime proof

Codex CLI 0.157.0 loaded the reinstalled Pacific Gym plugin in fresh ephemeral sessions. SessionStart restored the workspace-scoped run ID. The explicit `candidate-add` Bash call triggered PostToolUse, which compared the candidate against the pinned PNG with the installed Liquid vision model and recorded a feedback receipt containing input hashes, model digest, and visual-pair hash. Stop allowed the run to end only after a specific blocker and evidence were written. A follow-up session confirmed `run-complete` rejects missing proof and Stop allows exit after `run-block` records evidence. The latest install is `0.1.0+codex.20260925201018`.

The run is intentionally recorded as `blocked`: this macOS ARM64 environment did not provide a supported Isaac Sim GPU host. The smoke render and VLM feedback do not establish articulation validity, walking, or asset readiness. The VLM action is advisory; the evidence and limitation are retained verbatim in `hook-run.json`.

The stored comparison pair is `installed-hook-comparison.png`; `hook-run.json` records its SHA-256 and the exact input/model hashes.

The Blender 5.2.1 background CLI smoke produced `blender-smoke.blend`. `blender-derive.json` records the source, staged-input, output, and receipt hashes and confirms the original fixture bytes were unchanged. This verifies subprocess wiring and immutable staging only; it is not a production mech derivative.
