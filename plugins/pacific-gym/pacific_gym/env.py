"""Load Pacific Gym credentials from a dotenv file without executing shell code."""

import os
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
        env_path = next((candidate / ".env" for candidate in (Path.cwd(), *Path.cwd().parents)
                         if (candidate / ".env").is_file()), None)
        if env_path is None:
            return None
    for key, value in _parse(env_path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path
