#!/usr/bin/env python3
"""Inspect and validate the single local config file.

The other scripts merge four sources (command line > environment > ``.env`` >
``passwd.txt``); this command shows the merged result without connecting to the
game, so a typo in ``.env`` can be found before a cycle fails.

Secrets are masked unless ``--reveal`` is passed.  ``--validate`` exits non-zero
when a required key is missing or the model settings are half-filled.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import local_config

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
    has_session = bool(values.get("NEWSTAR_SESSION_FILE")) or Path(
        local_config.DEFAULT_SESSION_FILE
    ).exists()
    if not values.get("NEWSTAR_USERNAME"):
        errors.append("缺少 NEWSTAR_USERNAME")
    if not values.get("NEWSTAR_PASSWORD") and not has_session:
        errors.append("缺少 NEWSTAR_PASSWORD（也没有共享会话文件）")

    agent_url = values.get("NEWSTAR_AGENT_BASE_URL", "")
    agent_model = values.get("NEWSTAR_AGENT_MODEL", "")
    if bool(agent_url) != bool(agent_model):
        warnings.append(
            "模型配置只填了一半：NEWSTAR_AGENT_BASE_URL 与 NEWSTAR_AGENT_MODEL 必须同时给"
        )
    elif not agent_url:
        warnings.append("未配置模型：scripts/agent.py 将拒绝运行（其余脚本不受影响）")
    return errors, warnings


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
        "会话      " + (
            values.get("NEWSTAR_SESSION_FILE")
            or (local_config.DEFAULT_SESSION_FILE if Path(local_config.DEFAULT_SESSION_FILE).exists() else "-")
        ),
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
