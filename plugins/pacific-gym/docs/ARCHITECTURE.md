# Command architecture

The `pacific-gym` CLI is implemented in `pacific_gym/cli.py`. It exposes `inspect`, `compare`, `publish-candidate-frame`, `extract-keyframes`, `judge-keyframes`, `trace`, the run lifecycle commands, `generate-video`, `blender-derive`, `isaac-run`, and `target-preflight`.

Generation delegates to the packaged `scripts/run-flux3-video.py`. It requires an active run, explicit request and start-frame files, and writes job state and outputs under that run. It never searches proof history or substitutes an archived video. The dotenv loader supports `BFL_API_KEY` and `RAWTREE_API_KEY`, preserves values already present in the process environment, and does not perform interpolation or shell evaluation.

Keyframe extraction stores timestamped, whole-frame images and a source-hash manifest under the active run. It refuses to replace nonempty output. Candidate publication validates timestamp, dimensions, and image integrity before writing a completion sidecar. The judge waits for complete matching reference/candidate pairs and stores advisory results. It does not infer missing frames or certify motion or physics.

Blender and Isaac adapters hash immutable inputs, constrain outputs to the run, and record receipts. `target-preflight` checks a user-supplied existing host; it does not provision one. The CLI does not expose `validate-usd` or `promote`; articulation and walking acceptance are gated by the Isaac adapter receipt and run lifecycle.

Optional existing-host configuration is documented in `existing-host.example.json`: endpoint/access route, exact Blender and Isaac executable paths, remaining budget, and shutdown control/evidence. Null values indicate unverified setup. The record is informational and is not consumed by the CLI. No infrastructure changes are performed by this package.
