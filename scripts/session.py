#!/usr/bin/env python3
"""Manage the session cookie shared by the browser and the scripts.

New-Star stores exactly one session per account (``session`` table primary key
is ``userID``) and rewrites it on every request, so a second login replaces the
first session.  To keep the browser and the scripts logged in at the same time
they must use the *same* ``2Moons`` cookie.

Typical flow (browser first)::

    # 1. Log in with the browser, then open DevTools -> Application -> Cookies
    #    and copy the value of the 2Moons cookie.
    python scripts/session.py import '2Moons=v63a...'

    # 2. Every script now reuses state/session.json and never logs in again.
    python scripts/plan.py

The other direction (scripts first)::

    python scripts/session.py login      # 会用账号密码登录（浏览器会被踢下线）
    python scripts/session.py show       # 复制输出到浏览器 DevTools 的 2Moons
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Optional, Sequence

from local_config import DEFAULT_SESSION_FILE, add_session_args, load_config

from newstar_sdk import NewStarClient

SESSION_COOKIE_NAME = "2Moons"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="path to .env or passwd.txt (default: autodetect)")
    parser.add_argument("--base-url", help="override game base url")
    parser.add_argument("--username", help="override username")
    parser.add_argument("--password", help="override password")
    parser.add_argument("--universe", help="override universe name/id")
    parser.add_argument("--no-verify-ssl", action="store_true", help="skip TLS verification")
    parser.add_argument("--interval", type=float, default=0.3, help="seconds between requests")

    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="从浏览器导入 2Moons Cookie 并保存")
    p_import.add_argument(
        "cookie",
        help="浏览器里的 Cookie 值，可带 2Moons= 前缀或整行 Cookie 头",
    )
    p_import.set_defaults(func=cmd_import)

    p_show = sub.add_parser("show", help="打印会话文件里的 Cookie（粘贴回浏览器）")
    p_show.set_defaults(func=cmd_show)

    p_status = sub.add_parser("status", help="检查共享会话是否仍被服务器接受")
    p_status.set_defaults(func=cmd_status)

    p_login = sub.add_parser(
        "login", help="用账号密码登录并保存会话（会替换浏览器里的会话）"
    )
    p_login.set_defaults(func=cmd_login)

    for sub_parser in (p_import, p_show, p_status, p_login):
        add_session_args(sub_parser)

    return parser.parse_args(argv)


def _target(args: argparse.Namespace) -> Path:
    configured = args.session_file or os.getenv("NEWSTAR_SESSION_FILE")
    return Path(configured or DEFAULT_SESSION_FILE)


def _make_client(config, *, relogin: bool = False) -> NewStarClient:
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    return NewStarClient(
        config.base_url,
        config.username or None,
        config.password or None,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        request_interval=config.interval,
        auto_relogin=relogin,
        # The shared file should not carry a client-side expiry: the server
        # refreshes lastActivity on every request and is the only authority.
        ignore_cookie_expiry=True,
    )


def cmd_import(args: argparse.Namespace) -> int:
    config = load_config(args)
    target = _target(args)
    client = _make_client(config)
    try:
        client.set_session_cookie(args.cookie)
        if not client.is_logged_in():
            print(
                "Cookie 无效或会话已失效。请确认：\n"
                "  1. 浏览器里的游戏页面能正常打开；\n"
                "  2. 复制的是 DevTools 里 2Moons 这一条的值（不是别的 Cookie）；\n"
                "  3. 复制期间没有在别处重新登录。",
                file=sys.stderr,
            )
            return 1
        if getattr(args, "no_save_session", False):
            overview = client.get_overview()
            print(
                f"会话有效（属主 {overview.username or '-'}，宇宙 "
                f"{overview.universe or '-'}）；按 --no-save-session 未写入共享文件"
            )
            return 0
        path = client.save_session(target)
        overview = client.get_overview()
    finally:
        client.close()

    print(f"会话已保存到 {path}（属主 {overview.username or '-'}，宇宙 {overview.universe or '-'}）")
    print("浏览器保持登录即可；现在运行脚本会自动复用这个会话，不再重新登录。")
    print("会话最长 12 小时，过期或被浏览器重新登录替换后需要重新 import。")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    target = _target(args)
    if not target.exists():
        print(f"没有会话文件：{target}（先运行 import）", file=sys.stderr)
        return 1
    payload = json.loads(target.read_text(encoding="utf-8"))
    cookies = [
        item
        for item in payload.get("cookies", [])
        if item.get("name") == SESSION_COOKIE_NAME
    ]
    if not cookies:
        print(f"{target} 里没有 {SESSION_COOKIE_NAME} Cookie", file=sys.stderr)
        return 1
    item = cookies[0]
    print(f"{item['name']}={item['value']}")
    print()
    print("在浏览器里：DevTools -> Application -> Cookies -> 该站点，")
    print(f"双击 {SESSION_COOKIE_NAME} 的值粘贴上面的值，然后刷新页面。")
    print(f"文件保存时间：{payload.get('saved_at') or '-'}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args)
    target = _target(args)
    if not target.exists():
        print(f"没有会话文件：{target}（先运行 import）", file=sys.stderr)
        return 2
    client = _make_client(config)
    try:
        valid = client.load_session(target)
        if not valid:
            print(f"会话已失效：{target}（重新 import 浏览器 Cookie）")
            return 1
        overview = client.get_overview()
    finally:
        client.close()
    print(
        f"会话有效：{target}（属主 {overview.username or '-'}，"
        f"宇宙 {overview.universe or '-'}，行星 {overview.planet.name}）"
    )
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    config = load_config(args)
    if not config.username or not config.password:
        print("login 需要账号密码（--config / passwd.txt / --username --password）", file=sys.stderr)
        return 2
    if getattr(args, "no_save_session", False):
        print(
            "login 的目的就是保存会话，不能和 --no-save-session 一起用",
            file=sys.stderr,
        )
        return 2
    target = _target(args)
    print(
        "注意：这会创建新会话，浏览器里现有的登录会被替换；"
        "保存后请用 `session.py show` 把 Cookie 粘贴回浏览器。",
        file=sys.stderr,
    )
    client = _make_client(config, relogin=True)
    try:
        client.login()
        path = client.save_session(target)
        overview = client.get_overview()
    finally:
        client.close()
    print(f"已登录并保存到 {path}（属主 {overview.username or '-'}，宇宙 {overview.universe or '-'}）")
    return cmd_show(args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
