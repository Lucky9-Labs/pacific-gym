"""Use the primary main checkout for this machine's local plugin runtime."""
import os
from pathlib import Path
import subprocess
import sys

PRIMARY = Path("/Users/lucky/Code/pacific-gym")
source = PRIMARY / "plugins/pacific-gym"
if source.is_dir():
    branch = subprocess.check_output(
        ["git", "-C", str(PRIMARY), "branch", "--show-current"], text=True
    ).strip()
    if branch != "main":
        raise SystemExit("Pacific Gym primary checkout must be on main")
    os.environ["PLUGIN_ROOT"] = str(source)
    # The package may initially have been located through Codex's installed cache.
    # Load all CLI submodules from the stable main checkout instead.
    import pacific_gym
    pacific_gym.__path__ = [str(source / "pacific_gym")]
    sys.path.insert(0, str(source))

from .cli import main
raise SystemExit(main())
