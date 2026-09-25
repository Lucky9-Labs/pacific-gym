# Delivery plan

Each asset task begins with an immutable reference and a workspace-local run. Every derivative, receipt, generated video, and candidate stays under `.pacific-gym/runs/<run-id>/`. Inputs are hashed and are not overwritten.

1. Inspect immutable GLB sources and record structural facts without inferring rig bindings.
2. Generate a visual motion reference with `generate-video` into the active run.
3. Derive candidate assets with Blender while preserving source hashes.
4. Compare explicit candidates and synchronized keyframes as advisory visual checks.
5. Validate articulation and repeated forward walking through a scenario adapter on an authorized supported GPU host.
6. Complete only after recorded GPU walking acceptance; otherwise record a specific blocker and evidence.

Local checks and generated visual references do not establish physics readiness. Host provisioning is outside the package workflow.
