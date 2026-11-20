"""Local configuration loading shared by the project scripts.

Credentials come from (in order of precedence): command line flags, environment
variables, a ``.env`` file, or the free-form ``passwd.txt`` notes.  Nothing here
is imported by the SDK itself; it only serves the scripts in this directory.

The scripts also share a *session file*.  New-Star keeps exactly one session per
account (see ``docs/usage.md``), so when a session file exists the scripts reuse
it instead of logging in again; a fresh login would log the browser out.

The file is the single cache of the session: a password login (first run with
no file, expired file, or ``--relogin``) is written back to it, so the next run
reuses the same cookie instead of logging in a second time.  Scripts are
"script first" by default: a stale session is rebuilt without any flag.
``--no-relogin`` (or ``NEWSTAR_RELOGIN=0``) protects the browser instead, and
``--no-save-session`` opts out of writing the file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from newstar_sdk import NewStarClient
from utils.env import parse_dotenv

DEFAULT_SESSION_FILE = "state/session.json"

#: Exit code used when the session is gone and the policy forbids rebuilding it.
EXIT_SESSION_EXPIRED = 4

#: Values accepted as "off" for boolean environment flags.
_FALSE_VALUES = {"0", "false", "no", "off"}

SESSION_EXPIRED_HINT = """\
共享会话已失效（{path} 里的 Cookie 已不被服务器接受），当前策略不允许自动重登。
常见原因：浏览器端重新登录、点了退出，或会话超过 12 小时未活动。
请从浏览器 DevTools 复制 Cookie: 2Moons=... 后重新导入：

  .venv/bin/python scripts/session.py import '2Moons=<值>'

或者去掉 --no-relogin / 把 NEWSTAR_RELOGIN 设为 1，让脚本用账号密码自动重登
（会踢掉浏览器里的会话）。\
"""

SESSION_ABSENT_HINT = """\
没有共享会话文件 {path}，且当前策略不允许自动重登（--no-relogin / NEWSTAR_RELOGIN=0）。
先导入浏览器会话：

  .venv/bin/python scripts/session.py import '2Moons=<值>'

或允许脚本用账号密码登录（默认行为）。\
"""

SESSION_LOST_HINT = """\
会话已失效，脚本没有成功自动重登。可能账号/密码/宇宙有误，
或当前用了 --no-relogin / NEWSTAR_RELOGIN=0。
先检查凭据，或从浏览器重新导入：

  .venv/bin/python scripts/session.py import '2Moons=<值>'\
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
    session_target: str = DEFAULT_SESSION_FILE
    persist_session: bool = True
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
    "NEWSTAR_RELOGIN",
    "NEWSTAR_AGENT_BASE_URL",
    "NEWSTAR_AGENT_MODEL",
    "NEWSTAR_AGENT_API_KEY",
    "NEWSTAR_AGENT_TEMPERATURE",
    "NEWSTAR_AGENT_TIMEOUT",
    "NEWSTAR_FEISHU_WEBHOOK",
    "NEWSTAR_FEISHU_SECRET",
    "NEWSTAR_FEISHU_TIMEOUT",
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
            values.update(parse_dotenv(path))
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


def relogin_allowed(
    values: Dict[str, str],
    *,
    no_relogin: bool = False,
    relogin: bool = False,
) -> bool:
    """Whether a script may rebuild the session with username/password.

    Default is "script first": allowed.  ``--no-relogin`` or
    ``NEWSTAR_RELOGIN=0`` turns it off; an explicit ``--relogin`` (kept for
    compatibility) wins over the environment, matching CLI-over-env precedence.
    """

    if no_relogin:
        return False
    if relogin:
        return True
    raw = values.get("NEWSTAR_RELOGIN")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in _FALSE_VALUES


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
    relogin = relogin_allowed(
        values,
        no_relogin=bool(getattr(args, "no_relogin", False)),
        relogin=bool(getattr(args, "relogin", False)),
    )
    # Where a new session is written: the configured path, or the shared
    # default.  Kept separate from ``session_file`` because that one only
    # points at a file that is already usable.
    session_target = values.get("NEWSTAR_SESSION_FILE") or DEFAULT_SESSION_FILE
    persist_session = not bool(getattr(args, "no_save_session", False))

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
        session_target=session_target,
        persist_session=persist_session,
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
    relogin_group = parser.add_mutually_exclusive_group()
    relogin_group.add_argument(
        "--relogin",
        action="store_true",
        help="兼容保留：现在默认就允许会话失效时用账号密码重建",
    )
    relogin_group.add_argument(
        "--no-relogin",
        action="store_true",
        help=(
            "浏览器优先：会话失效时不自动登录，打印提示并以退出码 4 结束。"
            "也可用 NEWSTAR_RELOGIN=0"
        ),
    )
    parser.add_argument(
        "--no-save-session",
        action="store_true",
        help=(
            "不要把用账号密码取得的会话写入共享文件；该会话仅在本次进程有效。"
            "用于临时账号或一次性运行，避免覆盖共享会话"
        ),
    )


# ---------------------------------------------------------------------------
# Shared session file
# ---------------------------------------------------------------------------


def _session_meta(path: Path) -> Dict[str, object]:
    """Best-effort read of a session file's metadata (never raises)."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _check_session_owner(path: Path, client: NewStarClient) -> None:
    """Refuse to overwrite a shared session that belongs to someone else.

    Overwriting it would silently hand the wrong account the shared file, so
    this is a hard error instead of a warning.
    """

    payload = _session_meta(path)
    if not payload:
        return
    stored_base = str(payload.get("base_url") or "")
    stored_user = str(payload.get("username") or "")
    same_base = stored_base.rstrip("/").casefold() == client.base_url.rstrip("/").casefold()
    if stored_base and not same_base:
        raise SystemExit(
            f"共享会话 {path} 属于 {stored_base}，本次连接的是 {client.base_url}；"
            "请用 --session-file 指向对应文件，或加 --no-save-session"
        )
    if stored_user and client.username and stored_user != client.username:
        raise SystemExit(
            f"共享会话 {path} 属于账号 {stored_user}，本次使用的是 {client.username}；"
            "请用 --session-file 指向对应文件，或加 --no-save-session"
        )


class _SharedSessionClient(NewStarClient):
    """A client that writes every session it creates to the shared file.

    :func:`make_client` configures ``session_target`` / ``persist_session``.
    Overriding :meth:`login` also covers logins that happen *after* the initial
    acquisition (``auto_relogin`` on an expired cookie): those would otherwise
    live only in memory and be lost when the process exits.
    """

    session_target: Optional[Path] = None
    persist_session: bool = False

    def login(self, force: bool = False) -> "NewStarClient":
        if self._logged_in and not force:
            return self
        super().login(force=force)
        if self.persist_session and self.session_target is not None:
            _check_session_owner(self.session_target, self)
            self.save_session(self.session_target)
            print(
                f"会话：已重新登录并写入 {self.session_target}",
                file=sys.stderr,
            )
        return self


def _report_reused_session(path: Path, *, relogin: bool) -> None:
    saved_at = str(_session_meta(path).get("saved_at") or "")
    suffix = f"（保存于 {saved_at}）" if saved_at else ""
    policy = "" if relogin else "（浏览器优先：失效时不自动登录）"
    print(f"会话：复用 {path}{suffix}{policy}", file=sys.stderr)


def _report_new_session(persisted: bool, target: Optional[Path]) -> None:
    if persisted and target is not None:
        print(
            f"会话：账号密码登录（浏览器里的会话已被替换），已写入 {target}",
            file=sys.stderr,
        )
    else:
        print(
            "会话：账号密码登录（浏览器里的会话已被替换；"
            "--no-save-session，不写共享文件，仅本次进程有效）",
            file=sys.stderr,
        )


def _persist_session(client: NewStarClient, target: Optional[Path]) -> bool:
    """Write a freshly created session to the shared file."""

    if target is None:
        return False
    _check_session_owner(target, client)
    client.save_session(target)
    return True


def make_client(
    config: Config,
    *,
    interval: Optional[float] = None,
    timeout: Optional[float] = None,
    relogin: Optional[bool] = None,
    persist: Optional[bool] = None,
) -> NewStarClient:
    """Build a client around the shared session file.

    Resolution order: an existing file that the server still accepts, otherwise
    a password login (only when allowed; a fresh login replaces the account's
    only session and logs the browser out).  Every session created here -- or
    later by ``auto_relogin`` -- is written back to the shared file unless
    ``--no-save-session`` is set, so the next run can reuse it.
    """

    allow_relogin = config.relogin if relogin is None else relogin
    persist_session = config.persist_session if persist is None else persist
    target = Path(config.session_target) if config.session_target else None
    persist = bool(persist_session and target is not None)
    client = _SharedSessionClient(
        config.base_url,
        config.username or None,
        config.password or None,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout if timeout is None else timeout,
        request_interval=config.interval if interval is None else interval,
        auto_relogin=allow_relogin,
        # The server refreshes lastActivity on every request and does not
        # resend Set-Cookie; a local expires would drop a live shared session.
        ignore_cookie_expiry=True,
    )
    client.session_target = target
    # The initial acquisition below saves explicitly (so it can report once);
    # the hook is armed afterwards for mid-run ``auto_relogin``.
    client.persist_session = False

    def hand_over() -> NewStarClient:
        client.persist_session = persist
        return client

    session_file = Path(config.session_file) if config.session_file else None
    if session_file is not None and session_file.exists():
        if client.load_session(session_file):
            _report_reused_session(session_file, relogin=allow_relogin)
            return hand_over()
        if not allow_relogin:
            client.close()
            print(SESSION_EXPIRED_HINT.format(path=session_file), file=sys.stderr)
            raise SystemExit(EXIT_SESSION_EXPIRED)
        print(
            f"共享会话 {session_file} 已失效，用账号密码重新登录"
            "（浏览器里的会话会被替换）",
            file=sys.stderr,
        )
        if persist:
            _check_session_owner(target, client)
        client.login(force=True)
        _report_new_session(persist and _persist_session(client, target), target)
        return hand_over()
    if not allow_relogin:
        client.close()
        print(
            SESSION_ABSENT_HINT.format(path=target or DEFAULT_SESSION_FILE),
            file=sys.stderr,
        )
        raise SystemExit(EXIT_SESSION_EXPIRED)
    if not client.username or not client.has_password():
        client.close()
        raise SystemExit(
            "missing credentials: provide --username/--password (or a config "
            "file) and import a session file with scripts/session.py"
        )
    client.ensure_login()
    _report_new_session(persist and _persist_session(client, target), target)
    return hand_over()

