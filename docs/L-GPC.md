# L-GPC local GPU workspace

The active target is the user's owned L-GPC machine, reached with `ssh L-GPC`
into WSL2. Run Blender, Isaac Sim and Ollama natively on Windows, with all
Pacific Gym storage on D:. The Mac's primary main checkout remains the plugin
source of truth; do not use the expired Modal connection.

| Purpose | Windows path | WSL path |
| --- | --- | --- |
| Workspace / runs | `D:\PacificGym` | `/mnt/d/PacificGym` |
| Temporary data | `D:\PacificGym\tmp` | `/mnt/d/PacificGym/tmp` |
| Logs / caches | `D:\PacificGym\logs`, `D:\PacificGym\cache` | same under `/mnt/d` |
| Blender | `D:\Program Files\Blender Foundation\Blender 5.2\blender.exe` | Windows executable through WSL interoperability |
| Isaac launcher | `D:\IsaacSim\python.bat` | native Windows launcher |
| Dedicated Ollama | `D:\PacificGym\tools\Ollama\ollama.exe` | Windows executable |
| Models | `D:\Ollama\models` | `/mnt/d/Ollama/models` |

`host-config` reads the shared ignored `.env`: access method, SSH alias, target
OS, Windows/WSL workspace paths, executables, and target Ollama URL. The target
URL `http://127.0.0.1:11435` is Windows loopback, NOT Mac localhost. The existing
user Ollama service on 11434 is separate and has not been replaced.

Start the dedicated Ollama process with `OLLAMA_MODELS=D:\Ollama\models`,
`OLLAMA_HOST=127.0.0.1:11435`, and `TEMP`/`TMP=D:\PacificGym\tmp`. Its logs go
under the workspace logs directory. The server currently requires starting
again after reboot; no machine startup policy was changed. The Liquid model is
`hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M`, expected digest
`4e3daebe4fb42e458b26090e6b5e6b38d34ddd1ff9f0673565d83acb3a2c5682`.

The RTX 3060 has 12 GiB VRAM. Run inference and Isaac sequentially; unload the
model before simulation. Blender's CUDA/OptiX enumeration is verified, not a
rendering or physics acceptance claim. Isaac startup and actual simulation are
separate gates. Keep shader caches and Python bytecode on D:, and use Kit's
portable-root option plus an explicit D: log path when starting SimulationApp.

The loopback relay in `scripts/l-gpc` is enabled through an SSH tunnel at
Mac `http://127.0.0.1:11436`. The shared `.env` sets
`PACIFIC_GYM_OLLAMA_HOST` to that endpoint. Windows retains `AllSigned`;
the deployed PowerShell relay is signed with the user-approved, non-exportable
30-day `Pacific Gym Local Relay` certificate. Its thumbprint is
`8FEB26A1312C5966C3AC2393A347C2C6D466A2E1`, trusted in the current user's Root
and TrustedPublisher stores. Changes to the deployed script require re-signing;
renew the certificate before expiration. The checked-in source is unsigned.
The relay and SSH tunnel currently require restarting after reboot/disconnection.
A real Mac-to-GPU request returned `READY`, with GPU allocation confirmed in
`.pacific-gym/host-setup/signed-relay-proof.json`. Native hook triggering and
visual judgments remain separate acceptance checks.

Verified on 2026-09-27: Liquid returned `READY` from a real generation request,
with `size_vram=1808489511` reported by Ollama. This proves target-side text
inference on the GPU; image comparison and native hook steering remain unverified.
Local evidence is saved under `.pacific-gym/host-setup/`.

Isaac startup passed with exit code 0 after ten app updates (first startup took
about 6 minutes). The probe revealed Warp defaults to a C: cache; set
`WARP_CACHE_PATH=D:\PacificGym\cache\warp` before importing Isaac/Warp, in
addition to `CUDA_CACHE_PATH`, `PYTHONPYCACHEPREFIX`, `TEMP` and `TMP` on D:.

Repeatable native launchers live in `scripts/l-gpc/run-isaac.cmd`,
`run-blender.cmd`, and `run-ollama.cmd`, copied to `D:\PacificGym\tools`.
Pass an Isaac Python script, Blender arguments, or `serve` for Ollama respectively.
These set process-local D: paths.

The second Isaac probe passed in approximately 15 seconds with exit code 0,
and explicitly reported `D:\PacificGym\cache\warp\1.16.0` as its kernel
cache. No articulation, walking, or end-to-end VLM hook acceptance is claimed.
