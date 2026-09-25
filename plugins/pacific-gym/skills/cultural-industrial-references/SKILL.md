---
name: cultural-industrial-references
description: Research cultural, artistic, animation, robotics, and physics references from an asset image, tag each source by downstream use, and hand the evidence to a later asset task.
---

# Cultural and industrial reference intake

Use this skill as a separate research input before visual generation or technical authoring. It produces a sourced, tagged handoff; it does not generate assets or certify physics.

## Start from a Pacific Gym run

1. Read the active run manifest and select its local reference image. For a GLB input, use a pinned PNG/JPG/WebP reference frame. Never upload image bytes; the plugin captions the image locally with Ollama and sends only that text description to Nimble.
2. If `reference_research.status` is `waiting_for_credentials` or `failed`, start the asynchronous job:

   ```sh
   python3 -m pacific_gym run-research-start --manifest /absolute/path/.pacific-gym/runs/<run-id>/run.json --clipboard-key
   ```

   Omit `--clipboard-key` when `NIMBLE_API_KEY` is set in the process environment or workspace `.env`; use the flag only when the key is still on the macOS clipboard. The key is held in memory and is never written to the run.
3. Continue independent local work while the provider runs. At the FLUX prompt, Blender rigging, and Isaac Sim planning checkpoints, poll the same job:

   ```sh
   python3 -m pacific_gym run-research-poll --manifest /absolute/path/.pacific-gym/runs/<run-id>/run.json
   ```

   Add `--clipboard-key` only if the key is not available from the process environment or workspace `.env` and remains on the clipboard.
4. Once complete, read the run manifest's `reference_research` section and its `receipt` path. The run-local `nimble-research.json` is the tagged source of truth; `flux-prompt-draft.txt` and `reference-map.html` are derived review aids.

## Route references by tag

- `FLUX prompt`: use artist and motion leads as optional direction. Preserve the supplied asset identity, silhouette, proportions, and user style; ignore identity guesses.
- `Blender authoring`: use rigging and inverse-kinematics resources to inform authoring choices.
- `Isaac Sim handoff`: use robotics, USD, articulation, and physics resources to inform later setup and investigation.
- `Excluded from prompt`: do not use inferred lookalikes as design direction.
- `Review before use`: inspect the source before allowing it to steer the task.

Treat provider citations and tags as leads. Verify a source before relying on a technical claim. Research cannot demonstrate a working rig, stable gait, articulation, or physical validity. Keep task decisions tied to the correct downstream phase and cite the source IDs in the handoff or prompt when used.

If the job is still running, poll later and resume from the saved run state. If the reference hash changed, credentials are unavailable, or the provider fails, report that state and proceed only with work that does not depend on those findings.
