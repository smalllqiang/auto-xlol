"""Local configuration loading shared by the project scripts.

Credentials come from (in order of precedence): command line flags, environment
variables, a ``.env`` file, or the free-form ``passwd.txt`` notes.  Nothing here
is imported by the SDK itself; it only serves the scripts in this directory.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class Config:
    base_url: str
    username: str
    password: str
    universe: Optional[str] = None
    verify_ssl: bool = True
    timeout: float = 20.0
    interval: float = 0.5


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def _parse_dotenv(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _parse_passwd_txt(path: Path) -> Dict[str, str]:
    """Best-effort parser for the local ``passwd.txt`` notes.

    The file is free-form Chinese text, e.g.::

        游戏实例 https://ol.lstyxl.com/
        宇宙: CQBX8573
        用户名: SLQ
        密码: ...
    """

    text = path.read_text(encoding="utf-8")
    values: Dict[str, str] = {}

    match = re.search(r"https?://\S+", text)
    if match:
        values["NEWSTAR_BASE_URL"] = match.group(0).rstrip("/")

    labels = {
        "宇宙": "NEWSTAR_UNIVERSE",
        "用户名": "NEWSTAR_USERNAME",
        "密码": "NEWSTAR_PASSWORD",
    }
    for raw in text.splitlines():
        line = raw.strip()
        for label, key in labels.items():
            prefix = label + ":"
            if line.startswith(prefix):
                values[key] = line[len(prefix) :].strip()
            elif line.startswith(label + "："):
                values[key] = line[len(label) + 1 :].strip()
    return values


def load_config(args: argparse.Namespace) -> Config:
    values: Dict[str, str] = {}

    explicit = [args.config] if args.config else []
    if not explicit:
        for candidate in (".env", "passwd.txt"):
            if Path(candidate).exists():
                explicit.append(candidate)

    for name in explicit:
        path = Path(name)
        if not path.exists():
            raise SystemExit(f"config file not found: {name}")
        if path.suffix == ".env" or name.endswith(".env"):
            values.update(_parse_dotenv(path))
        else:
            values.update(_parse_passwd_txt(path))

    # Environment variables win over the config file.
    for key in (
        "NEWSTAR_BASE_URL",
        "NEWSTAR_USERNAME",
        "NEWSTAR_PASSWORD",
        "NEWSTAR_UNIVERSE",
        "NEWSTAR_VERIFY_SSL",
    ):
        if key in os.environ and os.environ[key].strip():
            values[key] = os.environ[key].strip()

    if args.base_url:
        values["NEWSTAR_BASE_URL"] = args.base_url
    if args.username:
        values["NEWSTAR_USERNAME"] = args.username
    if args.password:
        values["NEWSTAR_PASSWORD"] = args.password
    if args.universe:
        values["NEWSTAR_UNIVERSE"] = args.universe
    if args.no_verify_ssl:
        values["NEWSTAR_VERIFY_SSL"] = "0"

    base_url = values.get("NEWSTAR_BASE_URL", "")
    username = values.get("NEWSTAR_USERNAME", "")
    password = values.get("NEWSTAR_PASSWORD", "")
    if not base_url or not username or not password:
        raise SystemExit(
            "missing credentials: pass --config, or set NEWSTAR_BASE_URL/"
            "NEWSTAR_USERNAME/NEWSTAR_PASSWORD"
        )

    verify = values.get("NEWSTAR_VERIFY_SSL", "1").strip().lower()
    return Config(
        base_url=base_url,
        username=username,
        password=password,
        universe=values.get("NEWSTAR_UNIVERSE") or None,
        verify_ssl=verify not in {"0", "false", "no", "off"},
        interval=args.interval,
    )
