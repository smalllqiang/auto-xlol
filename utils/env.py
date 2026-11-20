"""Small ``.env`` helpers shared by scripts and lower-level utilities.

The project deliberately keeps one flat ``.env`` file (see ``.env.example``).
Scripts merge it with the process environment and CLI flags through
``scripts/local_config.py``; utilities that must work without the scripts layer
(such as the Feishu reporting channel) use the helpers here instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Optional, Union

PathLike = Union[str, Path]


def parse_dotenv(path: PathLike) -> Dict[str, str]:
    """Parse ``KEY=VALUE`` lines from a ``.env`` file.

    Blank lines and comments are ignored; surrounding single or double quotes
    are stripped.  Inline comments are intentionally *not* stripped because a
    password or token may legitimately contain ``#``.
    """

    values: Dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def find_env_file(path: Optional[PathLike] = None) -> Optional[Path]:
    """Return an existing ``.env`` path (explicit, or ``./.env``).

    Defaulting to the current directory matches how the scripts are documented
    to run (from the repository root) and keeps tests hermetic: nothing is
    discovered outside the working directory.
    """

    candidate = Path(path) if path is not None else Path(".env")
    return candidate if candidate.exists() else None


def read_env(path: Optional[PathLike] = None) -> Dict[str, str]:
    """Read a ``.env`` file, returning an empty mapping when it is absent."""

    found = find_env_file(path)
    return parse_dotenv(found) if found is not None else {}


def resolve_env(
    names: Iterable[str],
    path: Optional[PathLike] = None,
) -> Dict[str, str]:
    """Resolve ``names`` with process environment taking precedence over ``.env``.

    Empty values count as unset, so a blank ``KEY=`` line in ``.env`` never
    hides a real value from the process environment.
    """

    file_values = read_env(path)
    resolved: Dict[str, str] = {}
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            value = file_values.get(name, "").strip()
        resolved[name] = value
    return resolved


def get_env(name: str, path: Optional[PathLike] = None) -> str:
    """Return one setting, or an empty string when it is not configured."""

    return resolve_env((name,), path).get(name, "")
