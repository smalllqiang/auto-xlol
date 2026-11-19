#!/usr/bin/env python3
"""Show or claim the daily sign-in bonus (每日签到).

Read-only by default: it reports whether ``game.php?page=bonus`` is claimable
and when the 24h cooldown ends, using the left navigation of the overview.
``--claim`` performs the write.  Cron-friendly::

    0 8 * * * cd /path/to/auto-xlol && .venv/bin/python scripts/daily_bonus.py --claim

The check never touches ``page=bonus``: that page grants the bonus on GET, so a
"peek" would consume the daily claim.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from typing import Optional, Sequence

from local_config import add_session_args, load_config, make_client

from autoplay import run_daily_bonus
from newstar_sdk.exceptions import NewStarError


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
    parser.add_argument(
        "--claim",
        action="store_true",
        help="claim the bonus when it is available (default: only report)",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    add_session_args(parser)
    return parser.parse_args(argv)


def render(outcome, *, claim: bool) -> str:
    lines = [f"模式      {'claim' if claim else 'status'}"]
    lines.append(f"结果      {outcome.status}")
    lines.append(f"原因      {outcome.reason}")
    for item in outcome.items:
        name = item.get("name") or f"#{item.get('element_id')}"
        amount = item.get("amount")
        if isinstance(amount, (int, float)):
            lines.append(f"奖励      {name} × {amount:,.0f}")
        else:
            lines.append(f"奖励      {name}")
    if outcome.available_at:
        lines.append(f"下次可领取 {outcome.available_at}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    client = make_client(config)
    try:
        outcome = run_daily_bonus(client, dry_run=not args.claim)
        if args.json:
            print(json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(outcome, claim=args.claim))
        return 0
    except NewStarError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
