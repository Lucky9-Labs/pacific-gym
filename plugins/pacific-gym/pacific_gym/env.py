"""Load Pacific Gym credentials from a dotenv file without executing shell code."""

import os
import subprocess
from pathlib import Path


def _parse(path: Path) -> dict[str, str]:
    values = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name or not (name[0].isalpha() or name[0] == "_") or not all(c.isalnum() or c == "_" for c in name):
            raise ValueError(f"Invalid dotenv assignment at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split("#", 1)[0].rstrip()
        values[name] = value
    return values


def bootstrap_worktree_env(workspace: str | Path | None = None) -> Path | None:
    """Link this checkout to its primary checkout's ignored .env, never replace."""
    cwd = Path(workspace or Path.cwd()).resolve()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(cwd), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    try:
        root = Path(git("rev-parse", "--show-toplevel"))
        common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    except (OSError, subprocess.CalledProcessError):
        return None
    destination = root / ".env"
    if destination.exists() or destination.is_symlink():
        return destination if destination.is_file() else None
    # Standard linked worktrees share the primary checkout's .git directory.
    source = common.parent / ".env"
    if common.name != ".git" or source == destination or not source.is_file():
        return None
    try:
        destination.symlink_to(source.resolve())
    except FileExistsError:
        pass  # Another session may have linked it concurrently.
    return destination if destination.is_file() else None


def host_config() -> dict:
    """Return only documented connection metadata, never API credentials."""
    load_dotenv()
    fields = {
        "access_method": "PACIFIC_GYM_GPU_ACCESS_METHOD",
        "sandbox_id": "PACIFIC_GYM_MODAL_SANDBOX_ID",
        "modal_python": "PACIFIC_GYM_MODAL_PYTHON",
        "workspace": "PACIFIC_GYM_GPU_WORKSPACE",
        "blender_executable": "PACIFIC_GYM_BLENDER_EXECUTABLE",
        "isaac_executable": "PACIFIC_GYM_ISAAC_EXECUTABLE",
        "budget_limit_usd": "PACIFIC_GYM_GPU_BUDGET_LIMIT_USD",
        "shutdown_deadline": "PACIFIC_GYM_GPU_SHUTDOWN_DEADLINE",
    }
    result = {field: os.environ.get(key) or None for field, key in fields.items()}
    result["source"] = "environment"
    result["runtime_verified"] = False
    return result


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load assignments from a local .env; environment wins unless override=True.

    An explicit path is honored. Otherwise PACIFIC_GYM_ENV_FILE may select a
    file, then .env is searched from the current directory toward its parents.
    Values are literal strings; interpolation and shell evaluation are absent.
    """
    explicit = path or os.environ.get("PACIFIC_GYM_ENV_FILE")
    if explicit:
        env_path = Path(explicit).expanduser().resolve()
        if not env_path.is_file():
            raise FileNotFoundError(f"Pacific Gym env file does not exist: {env_path}")
    else:
        bootstrap_worktree_env()
        env_path = next((candidate / ".env" for candidate in (Path.cwd(), *Path.cwd().parents)
                         if (candidate / ".env").is_file()), None)
        if env_path is None:
            return None
    for key, value in _parse(env_path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path
