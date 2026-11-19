"""Local configuration loading shared by the project scripts.

Credentials come from (in order of precedence): command line flags, environment
variables, a ``.env`` file, or the free-form ``passwd.txt`` notes.  Nothing here
is imported by the SDK itself; it only serves the scripts in this directory.

The scripts also share a *session file*.  New-Star keeps exactly one session per
account (see ``docs/usage.md``), so when a session file exists the scripts reuse
it instead of logging in again; a fresh login would log the browser out.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from newstar_sdk import NewStarClient

DEFAULT_SESSION_FILE = "state/session.json"

SESSION_EXPIRED_HINT = """\
共享会话已失效（{path} 里的 Cookie 已不被服务器接受）。
常见原因：浏览器端重新登录、点了退出，或会话超过 12 小时未活动。
请从浏览器 DevTools 复制 Cookie: 2Moons=... 后重新导入：

  .venv/bin/python scripts/session.py import '2Moons=<值>'

如果你确实要用账号密码重新登录（会踢掉浏览器里的会话），加 --relogin。\
"""


@dataclass
class AgentSettings:
    """Model endpoint settings, loaded from the same file as the credentials.

    Keeping them here (instead of reading ``os.environ`` directly) means a
    ``.env`` only has to be filled once: the planning script sees the model,
    the endpoint and the game credentials through the same loader.
    """

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2
    timeout: float = 60.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def as_dict(self, *, reveal: bool = False) -> Dict[str, object]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key if reveal else ("***" if self.api_key else ""),
            "temperature": self.temperature,
            "timeout": self.timeout,
        }


@dataclass
class Config:
    base_url: str
    username: str
    password: str
    universe: Optional[str] = None
    verify_ssl: bool = True
    timeout: float = 20.0
    interval: float = 0.5
    session_file: Optional[str] = None
    relogin: bool = True
    agent: AgentSettings = field(default_factory=AgentSettings)


#: Keys copied from the process environment over the config file.
ENV_KEYS = (
    "NEWSTAR_BASE_URL",
    "NEWSTAR_USERNAME",
    "NEWSTAR_PASSWORD",
    "NEWSTAR_UNIVERSE",
    "NEWSTAR_VERIFY_SSL",
    "NEWSTAR_TIMEOUT",
    "NEWSTAR_SESSION_FILE",
    "NEWSTAR_AGENT_BASE_URL",
    "NEWSTAR_AGENT_MODEL",
    "NEWSTAR_AGENT_API_KEY",
    "NEWSTAR_AGENT_TEMPERATURE",
    "NEWSTAR_AGENT_TIMEOUT",
)

#: CLI flag -> config key.  Every destination is read with ``getattr`` so a
#: script that only declares a subset of these flags still works.
CLI_KEYS = (
    ("base_url", "NEWSTAR_BASE_URL"),
    ("username", "NEWSTAR_USERNAME"),
    ("password", "NEWSTAR_PASSWORD"),
    ("universe", "NEWSTAR_UNIVERSE"),
    ("timeout", "NEWSTAR_TIMEOUT"),
    ("session_file", "NEWSTAR_SESSION_FILE"),
    ("agent_base_url", "NEWSTAR_AGENT_BASE_URL"),
    ("agent_model", "NEWSTAR_AGENT_MODEL"),
    ("agent_api_key", "NEWSTAR_AGENT_API_KEY"),
    ("agent_temperature", "NEWSTAR_AGENT_TEMPERATURE"),
    ("agent_timeout", "NEWSTAR_AGENT_TIMEOUT"),
)



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


def load_values(args: argparse.Namespace) -> Dict[str, str]:
    """Merge config files, environment and CLI flags into one flat dict.

    Precedence is command line > environment > config file, which is what the
    scripts advertise.  Split out from :func:`load_config` so a diagnostic
    command can inspect a half-filled file without tripping the "missing base
    URL" check.
    """

    values: Dict[str, str] = {}

    explicit = []
    configured_path = getattr(args, "config", None)
    if configured_path:
        explicit = [configured_path]
    else:
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

    for key in ENV_KEYS:
        if key in os.environ and os.environ[key].strip():
            values[key] = os.environ[key].strip()

    for flag, key in CLI_KEYS:
        value = getattr(args, flag, None)
        if value not in (None, ""):
            values[key] = str(value)
    if getattr(args, "no_verify_ssl", False):
        values["NEWSTAR_VERIFY_SSL"] = "0"
    return values


def _float_value(values: Dict[str, str], key: str, default: float) -> float:
    raw = values.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{key} 必须是数字，当前是 {raw!r}") from exc


def load_config(args: argparse.Namespace) -> Config:
    values = load_values(args)

    session_file = values.get("NEWSTAR_SESSION_FILE") or None
    if not session_file and Path(DEFAULT_SESSION_FILE).exists():
        session_file = DEFAULT_SESSION_FILE
    session_ready = bool(session_file) and Path(session_file).exists()
    relogin = bool(getattr(args, "relogin", False)) or not session_ready

    base_url = values.get("NEWSTAR_BASE_URL", "")
    username = values.get("NEWSTAR_USERNAME", "")
    password = values.get("NEWSTAR_PASSWORD", "")
    if not base_url:
        raise SystemExit(
            "missing base url: pass --config, or set NEWSTAR_BASE_URL"
        )

    verify = values.get("NEWSTAR_VERIFY_SSL", "1").strip().lower()
    return Config(
        base_url=base_url,
        username=username,
        password=password,
        universe=values.get("NEWSTAR_UNIVERSE") or None,
        verify_ssl=verify not in {"0", "false", "no", "off"},
        timeout=_float_value(values, "NEWSTAR_TIMEOUT", 20.0),
        interval=getattr(args, "interval", 0.5),
        session_file=session_file if session_ready else None,
        relogin=relogin,
        agent=AgentSettings(
            base_url=values.get("NEWSTAR_AGENT_BASE_URL", ""),
            model=values.get("NEWSTAR_AGENT_MODEL", ""),
            api_key=values.get("NEWSTAR_AGENT_API_KEY", ""),
            temperature=_float_value(values, "NEWSTAR_AGENT_TEMPERATURE", 0.2),
            timeout=_float_value(values, "NEWSTAR_AGENT_TIMEOUT", 60.0),
        ),
    )


def add_session_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared-session flags used by :func:`make_client`."""

    parser.add_argument(
        "--session-file",
        default=None,
        help=(
            "共享会话文件；默认 state/session.json（存在时自动使用）。"
            "由 scripts/session.py 生成/查看"
        ),
    )
    parser.add_argument(
        "--relogin",
        action="store_true",
        help="会话失效时用账号密码重新登录（会踢掉浏览器里的会话）",
    )


def make_client(
    config: Config,
    *,
    interval: Optional[float] = None,
    timeout: Optional[float] = None,
    relogin: Optional[bool] = None,
) -> NewStarClient:
    """Build a client that reuses the shared session file when it exists.

    A shared session is never re-created automatically: logging in again would
    replace the account's only session and log the browser out.  Use
    ``--relogin`` (or delete the session file) when a fresh login is intended.
    """

    allow_relogin = config.relogin if relogin is None else relogin
    client = NewStarClient(
        config.base_url,
        config.username or None,
        config.password or None,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout if timeout is None else timeout,
        request_interval=config.interval if interval is None else interval,
        auto_relogin=allow_relogin,
    )
    session_file = Path(config.session_file) if config.session_file else None
    if session_file is not None and session_file.exists():
        if client.load_session(session_file):
            return client
        if not allow_relogin:
            client.close()
            raise SystemExit(SESSION_EXPIRED_HINT.format(path=session_file))
        print(
            f"共享会话 {session_file} 已失效，按 --relogin 用账号密码重新登录"
            "（浏览器里的会话会被替换）",
            file=sys.stderr,
        )
        client.login(force=True)
        return client
    if not client.username or not client.has_password():
        client.close()
        raise SystemExit(
            "missing credentials: provide --username/--password (or a config "
            "file) and import a session file with scripts/session.py"
        )
    client.ensure_login()
    return client
