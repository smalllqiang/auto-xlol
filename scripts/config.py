#!/usr/bin/env python3
"""Inspect and validate the single local config file.

The other scripts merge four sources (command line > environment > ``.env`` >
``passwd.txt``); this command shows the merged result without connecting to the
game, so a typo in ``.env`` can be found before a cycle fails.

Secrets are masked unless ``--reveal`` is passed.  ``--validate`` exits non-zero
only when a required key is missing; model and notification settings are optional
and produce warnings instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlsplit

import local_config

from utils.notify import ENV_SECRET, ENV_TIMEOUT, ENV_WEBHOOK, is_feishu_webhook

CONFIG_CANDIDATES = (".env", "passwd.txt")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="path to .env or passwd.txt (default: autodetect)")
    parser.add_argument("--reveal", action="store_true", help="show secrets instead of ***")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--validate", action="store_true", help="exit 1 when incomplete")
    return parser.parse_args(argv)


def _mask(value: str, reveal: bool) -> str:
    if not value:
        return ""
    return value if reveal else "***"


def _files(args: argparse.Namespace) -> List[str]:
    if args.config:
        return [args.config] if Path(args.config).exists() else []
    return [name for name in CONFIG_CANDIDATES if Path(name).exists()]


def _problems(values: Dict[str, str], files: List[str]) -> tuple:
    """Return ``(errors, warnings)``; only errors make ``--validate`` fail."""

    errors: List[str] = []
    warnings: List[str] = []
    if not files:
        errors.append("没有找到配置文件（.env / passwd.txt）")
    if not values.get("NEWSTAR_BASE_URL"):
        errors.append("缺少 NEWSTAR_BASE_URL")
    session_path = (
        values.get("NEWSTAR_SESSION_FILE") or local_config.DEFAULT_SESSION_FILE
    )
    has_session = Path(session_path).exists()
    if not values.get("NEWSTAR_USERNAME"):
        errors.append("缺少 NEWSTAR_USERNAME")
    if not values.get("NEWSTAR_PASSWORD") and not has_session:
        errors.append("缺少 NEWSTAR_PASSWORD（也没有共享会话文件）")
    if not has_session and not local_config.relogin_allowed(values):
        errors.append(
            "NEWSTAR_RELOGIN=0 且没有共享会话文件：脚本无法登录（退出码 4）"
        )

    agent_url = values.get("NEWSTAR_AGENT_BASE_URL", "")
    agent_model = values.get("NEWSTAR_AGENT_MODEL", "")
    if bool(agent_url) != bool(agent_model):
        warnings.append(
            "模型配置只填了一半：NEWSTAR_AGENT_BASE_URL 与 NEWSTAR_AGENT_MODEL 必须同时给"
        )
    elif not agent_url:
        warnings.append("未配置模型：scripts/agent.py 将拒绝运行（其余脚本不受影响）")

    webhook = values.get(ENV_WEBHOOK, "").strip()
    secret = values.get(ENV_SECRET, "").strip()
    timeout = values.get(ENV_TIMEOUT, "").strip()
    if webhook and not is_feishu_webhook(webhook):
        warnings.append(f"{ENV_WEBHOOK} 不是有效的 http(s) 地址")
    if secret and not webhook:
        warnings.append(
            f"配置了 {ENV_SECRET} 但缺少 {ENV_WEBHOOK}，消息上报不会启用"
        )
    if timeout:
        try:
            float(timeout)
        except ValueError:
            warnings.append(f"{ENV_TIMEOUT} 必须是数字")
    return errors, warnings


def _notify_label(values: Dict[str, str], reveal: bool) -> str:
    webhook = values.get(ENV_WEBHOOK, "").strip()
    if not webhook:
        return "飞书 未配置（可选）"
    secret = values.get(ENV_SECRET, "").strip()
    timeout = values.get(ENV_TIMEOUT, "").strip() or "10"
    if reveal:
        target = webhook
    else:
        try:
            host = urlsplit(webhook).hostname or "-"
        except ValueError:
            host = "-"
        target = f"{host}（已配置）"
    return "飞书 {}  签名 {}  超时 {}s".format(
        target,
        "已配置" if secret else "未配置",
        timeout,
    )


def _session_label(values: Dict[str, str]) -> str:
    path = values.get("NEWSTAR_SESSION_FILE") or local_config.DEFAULT_SESSION_FILE
    if Path(path).exists():
        return f"{path}（存在，自动复用）"
    return f"{path}（不存在，登录后写入）"


def _session_policy(values: Dict[str, str]) -> str:
    if local_config.relogin_allowed(values):
        return "脚本优先（失效自动登录，踢浏览器）"
    return "浏览器优先（--no-relogin / NEWSTAR_RELOGIN=0）"


def _render(values: Dict[str, str], files: List[str], reveal: bool) -> str:
    lines = [
        "配置文件  " + ("、".join(files) if files else "（无）"),
        "游戏      {}  宇宙 {}  用户 {}  密码 {}  TLS 校验 {}".format(
            values.get("NEWSTAR_BASE_URL") or "-",
            values.get("NEWSTAR_UNIVERSE") or "-",
            values.get("NEWSTAR_USERNAME") or "-",
            _mask(values.get("NEWSTAR_PASSWORD", ""), reveal) or "-",
            "关闭"
            if values.get("NEWSTAR_VERIFY_SSL", "1").strip().lower() in {"0", "false", "no", "off"}
            else "开启",
        ),
        "模型      {}  {}  key {}  temperature {}  timeout {}s".format(
            values.get("NEWSTAR_AGENT_BASE_URL") or "-",
            values.get("NEWSTAR_AGENT_MODEL") or "-",
            _mask(values.get("NEWSTAR_AGENT_API_KEY", ""), reveal) or "-",
            values.get("NEWSTAR_AGENT_TEMPERATURE") or "0.2",
            values.get("NEWSTAR_AGENT_TIMEOUT") or "60",
        ),
        "会话      " + _session_label(values),
        "会话策略  " + _session_policy(values),
        "消息上报  " + _notify_label(values, reveal),
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    files = _files(args)
    try:
        values = local_config.load_values(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    errors, warnings = _problems(values, files)
    if args.json:
        print(
            json.dumps(
                {
                    "files": files,
                    "game": {
                        "base_url": values.get("NEWSTAR_BASE_URL", ""),
                        "universe": values.get("NEWSTAR_UNIVERSE", ""),
                        "username": values.get("NEWSTAR_USERNAME", ""),
                        "password": _mask(values.get("NEWSTAR_PASSWORD", ""), args.reveal),
                        "verify_ssl": values.get("NEWSTAR_VERIFY_SSL", "1"),
                    },
                    "agent": {
                        "base_url": values.get("NEWSTAR_AGENT_BASE_URL", ""),
                        "model": values.get("NEWSTAR_AGENT_MODEL", ""),
                        "api_key": _mask(values.get("NEWSTAR_AGENT_API_KEY", ""), args.reveal),
                        "temperature": values.get("NEWSTAR_AGENT_TEMPERATURE", ""),
                        "timeout": values.get("NEWSTAR_AGENT_TIMEOUT", ""),
                    },
                    "notify": {
                        "feishu_webhook": _mask(values.get(ENV_WEBHOOK, ""), args.reveal),
                        "feishu_secret": _mask(values.get(ENV_SECRET, ""), args.reveal),
                        "feishu_timeout": values.get(ENV_TIMEOUT, ""),
                    },
                    "errors": errors,
                    "warnings": warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_render(values, files, args.reveal))
        if errors or warnings:
            print("\n问题：")
            for problem in errors:
                print(f"  - [错误] {problem}")
            for problem in warnings:
                print(f"  - [提示] {problem}")

    if args.validate and errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
