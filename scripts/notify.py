#!/usr/bin/env python3
"""Send a report to the group behind a Feishu custom-bot webhook.

This is the thin command-line wrapper around :mod:`utils.notify`.  Configure
``NEWSTAR_FEISHU_WEBHOOK`` (and optionally ``NEWSTAR_FEISHU_SECRET``) in
``.env``, then use this script to test the channel or to report something from
another shell command::

    .venv/bin/python scripts/notify.py --test
    echo "cycle failed: timeout" | .venv/bin/python scripts/notify.py --title 告警

Exit codes: 0 sent, 1 send failed, 2 not configured (or missing input).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Optional, Sequence

import local_config

from utils.notify import (
    ENV_SECRET,
    ENV_TIMEOUT,
    ENV_WEBHOOK,
    FeishuNotifier,
    SendResult,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 2


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="要发送的文本；省略时从 stdin 读取")
    source.add_argument(
        "--test",
        action="store_true",
        help="发送一条带当前时间的测试消息",
    )
    parser.add_argument(
        "--title",
        help="用富文本(post)发送并带标题；正文每行一段",
    )
    parser.add_argument(
        "--webhook",
        help="临时覆盖 webhook（会留在 shell 历史里，优先写进 .env）",
    )
    parser.add_argument(
        "--secret",
        help="临时覆盖签名密钥（会留在 shell 历史里）",
    )
    parser.add_argument("--timeout", type=float, help="HTTP 超时（秒）")
    parser.add_argument(
        "--config",
        help="指定 .env / passwd.txt；默认自动探测（同其它脚本）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser.parse_args(argv)


def _text_from_args(args: argparse.Namespace) -> Optional[str]:
    if args.text is not None:
        return args.text
    if args.test:
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return f"auto-xlol 飞书消息上报测试\n时间：{stamp}"
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data.rstrip("\n")
    return None


def _emit(result: SendResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    if result.ok:
        print("已发送到飞书群")
        return
    print(f"发送失败：{result.error or result.msg or '未知错误'}", file=sys.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    text = _text_from_args(args)
    if text is None:
        print(
            "缺少消息内容：用 --text/--test，或通过 stdin 传入正文",
            file=sys.stderr,
        )
        return EXIT_NOT_CONFIGURED

    try:
        values = local_config.load_values(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED

    webhook = (
        args.webhook
        if args.webhook is not None
        else values.get(ENV_WEBHOOK, "")
    ).strip()
    secret = (
        args.secret
        if args.secret is not None
        else values.get(ENV_SECRET, "")
    ).strip()
    timeout = args.timeout if args.timeout is not None else values.get(ENV_TIMEOUT, "")

    notifier = FeishuNotifier(webhook, secret, timeout or 10.0)
    try:
        result = (
            notifier.send_post(args.title, text)
            if args.title
            else notifier.send_text(text)
        )
    finally:
        notifier.close()

    _emit(result, args.json)
    if result.ok:
        return EXIT_OK
    return EXIT_NOT_CONFIGURED if result.skipped else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
