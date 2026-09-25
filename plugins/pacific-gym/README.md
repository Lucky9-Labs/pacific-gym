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
