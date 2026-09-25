# Pacific Gym

Pacific Gym keeps asset work inside a workspace run directory and records input hashes, generated candidates, and validation receipts. A run can be completed only after the required GPU walking evidence is recorded, or stopped with a specific blocker.

Read [the workflow plan](docs/PLAN.md) and [the command architecture](docs/ARCHITECTURE.md) before starting. See `skills/isaac-ready/SKILL.md` for the detailed workflow. The source GLB and rig reference are separate assets; a skin declaration alone does not establish a bound or animated character.

## Install and credentials

The plugin uses Python 3.10 or later. Pillow is required only for image comparison and keyframe commands. `BFL_API_KEY` and `RAWTREE_API_KEY` can be set in the shell or a local `.env`; explicit shell values take precedence. The loader reads literal dotenv assignments and never evaluates shell syntax. Keep `.env` outside version control.

For a worktree, an existing ignored credential file can be made available without copying its contents:

```sh
plugins/pacific-gym/scripts/bootstrap-worktree-env.sh /absolute/path/to/existing/.env
```

This creates `.env` only when the destination is absent. It does not acquire credentials or provision infrastructure.

## Commands

Run `python3 -m pacific_gym --help` from this directory for the complete command list. `run-start`, `run-status`, `candidate-add`, `run-block`, `run-complete`, `inspect`, `compare`, `publish-candidate-frame`, `extract-keyframes`, `judge-keyframes`, `trace`, `blender-derive`, `isaac-run`, `target-preflight`, and `generate-video` are implemented. No `validate-usd` or `promote` command is provided.

Start a run, then generate into that run's `generation/bfl-video` directory:

```sh
python3 -m pacific_gym run-start --workspace "$PWD" --reference /absolute/path/reference.glb
python3 -m pacific_gym generate-video --manifest /absolute/path/.pacific-gym/runs/<run-id>/run.json \
  --request /absolute/path/request.json --start-frame /absolute/path/start.png
```

The request JSON supplies `endpoint`, `model`, `mode` (`i2v`), `prompt`, and `settings`. This command submits a fresh BFL job; it never accepts an archived video as a result. It can resume only the BFL job state created in that run-local output directory. Generated videos are visual references, not gait or physics proof.

Extract full-frame PNGs into the same run after generation or when reviewing another explicitly supplied video:

```sh
python3 -m pacific_gym extract-keyframes --manifest /absolute/path/.pacific-gym/runs/<run-id>/run.json \
  --video /absolute/path/video.mp4 --fps 2
```

This creates `keyframes/reference/manifest.json` and timestamped PNGs. Existing output is preserved; choose a new run when extracting a different video.

For an authorized existing GPU host, fill in [the optional host setup record](docs/existing-host.example.json) with its endpoint/access method, Blender and Isaac executable paths, current spend, and shutdown evidence. Null fields mean that setup is unverified. The current authorization ceiling is $20 and the shutdown deadline is 2026-09-25 17:00 America/Los_Angeles. This record is documentation only; the package does not read it or create/start infrastructure. Never extend the cap or deadline implicitly.

## Shared worktree environment and GPU discovery

SessionStart and every CLI invocation automatically link a missing checkout `.env`
to the primary checkout's `.env`, using Git's common-directory metadata. Existing
files and symlinks are preserved. No home-directory search or secret copy occurs.
For linking immediately at Git checkout, this repository also supplies
`.githooks/post-checkout`; enable it with `git config core.hooksPath` set to the
absolute primary checkout `.githooks` path (preserve any existing hook setup).
The manual bootstrap accepts an explicit source file and targets the current
checkout even when invoked from the installed plugin.

Run `python3 -m pacific_gym host-config` to discover GPU settings from the shared
`.env`. It returns only connection metadata, never BFL/RawTree credentials:
`PACIFIC_GYM_GPU_ACCESS_METHOD`, `PACIFIC_GYM_MODAL_SANDBOX_ID`,
`PACIFIC_GYM_MODAL_PYTHON`, `PACIFIC_GYM_GPU_WORKSPACE`,
`PACIFIC_GYM_BLENDER_EXECUTABLE`, `PACIFIC_GYM_ISAAC_EXECUTABLE`,
`PACIFIC_GYM_GPU_BUDGET_LIMIT_USD`, and `PACIFIC_GYM_GPU_SHUTDOWN_DEADLINE`.
For Modal, use the configured local Python runtime and its authenticated Modal
SDK: `modal.Sandbox.from_id(sandbox_id)` connects to existing compute; it does not
create a sandbox. Check `poll()` and run the target preflight on that host before
work. Transfer only required run assets/configuration; do not copy the entire
credential file to compute. `target-preflight` uses the executable environment
settings as defaults when its explicit flags are omitted. Metadata never proves
current availability, spend or shutdown; retain live validation and expiry checks.

## Local runtime root

This machine's authoritative plugin source is
`/Users/lucky/Code/pacific-gym/plugins/pacific-gym`, with its primary checkout on
`main`. The local marketplace installs from that checkout. Hook definitions use
absolute script paths there and explicitly set `PLUGIN_ROOT`; runtime imports
therefore survive replacement of Codex's versioned install cache. CLI module
startup also redirects to the primary checkout when it exists, and refuses to
use it if it is not on `main`. Task cwd and run output paths are preserved.

Pulling main updates runtime code. Changes to hook definitions or registered
skill metadata still require refreshing the installation and reviewing hook
trust in Codex; this does not bypass trust. Do not reinstall from feature
worktrees. On remote hosts without the local path, CLI uses the supplied package.
