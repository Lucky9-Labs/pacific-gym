# Pacific Gym

Pacific Gym is a Codex plugin for turning a generated 3D character and a separate motion rig into an Isaac Sim robot candidate. Its first fixture is Strokah. The repository records a proof for each integration before a long-running GPU agent attempts a forward-walking simulation.

Start with [the delivery plan](docs/PLAN.md), [the architecture](docs/ARCHITECTURE.md), and [the proof index](proof/index.json). A passing feature worktree updates the proof index before it is merged.

The current Strokah source packet is a reference handoff, not a validated physics asset. The static GLB has no skeleton; the gait GLB has its own 62-joint rig. Pacific Gym must inspect and bridge those structures rather than assume that they match.
