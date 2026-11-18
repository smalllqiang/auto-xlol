#!/usr/bin/env python3
"""Threat report and optional fleet recall (P2-2).

Read-only by default: reads the espionage/battle message tabs, the fleet table
and the overview, then says how exposed the account is and what could be done.
``--recall`` only previews; a real recall needs ``--execute`` + ``--allow``
(fleet ids from the report), one fleet per call, verified by the fleet table.

Examples::

    .venv/bin/python scripts/threat.py --no-verify-ssl
    .venv/bin/python scripts/threat.py --no-verify-ssl --json
    .venv/bin/python scripts/threat.py --no-verify-ssl --recall
    .venv/bin/python scripts/threat.py --no-verify-ssl --recall --execute --allow 123
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from local_config import load_config

from autoplay.threat import assess_threat
from autoplay.threat_executor import recall_candidates, recall_fleets
from newstar_sdk import NewStarClient
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
    parser.add_argument("--json", action="store_true", help="print the assessment as JSON")
    parser.add_argument("--recall", action="store_true", help="preview recalling a fleet")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually recall (requires --recall and --allow)",
    )
    parser.add_argument("--interval", type=float, default=0.4, help="seconds between requests")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="fleet id that may be recalled (repeatable)",
    )
    return parser.parse_args(argv)


def _print_assessment(assessment) -> None:
    print(f"威胁等级  {assessment.level}")
    for reason in assessment.reasons:
        print(f"  - {reason}")
    if assessment.exposure is not None:
        print(f"资源暴露  仓容的 {assessment.exposure:.1f} 倍")
    print(f"在途舰队  {assessment.in_flight} 支（可召回 {len(assessment.recallable)}）")
    if assessment.recallable:
        print("可召回 id  " + ", ".join(str(item) for item in assessment.recallable))
    for event in assessment.espionage[:5]:
        origin = event.attacker_coordinates or "?"
        target = event.target_coordinates or "?"
        when = event.at.isoformat(sep=" ") if event.at is not None else "?"
        print(f"  侦察  {when}  {event.attacker}{origin} → {event.target}{target}")
    if assessment.recommendations:
        print("建议：")
        for item in assessment.recommendations:
            print(f"  * {item}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.execute and not args.recall:
        print("--execute 需要配合 --recall", file=sys.stderr)
        return 2
    if args.execute and Path("state/STOP").exists():
        print("state/STOP exists; refusing to recall", file=sys.stderr)
        return 2
    try:
        allowed: List[int] = [int(item) for item in args.allow]
    except ValueError:
        print("--allow values must be fleet ids", file=sys.stderr)
        return 2
    if args.execute and not allowed:
        print("--execute needs at least one --allow fleet id", file=sys.stderr)
        return 2

    config = load_config(args)
    if not config.verify_ssl:
        import warnings

        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    with NewStarClient(
        config.base_url,
        config.username,
        config.password,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        request_interval=0.4,
    ) as client:
        try:
            messages = []
            for category in (0, 3):
                page = client.get_messages(category=category)
                messages.extend(getattr(page, "messages", []) or [])
            fleets = client.get_fleets()
            resources = client.get_overview().resources
            assessment = assess_threat(messages, fleets, resources=resources)
        except NewStarError as exc:
            print(f"读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(assessment.as_dict(), ensure_ascii=False, indent=2))
        else:
            _print_assessment(assessment)

        if not args.recall:
            return 0

        candidates = recall_candidates(fleets)
        preview = recall_fleets(
            client,
            dry_run=not args.execute,
            allowed=allowed,
            fleets=fleets,
        )
        print()
        print(f"召回 {preview.status}：{preview.reason}")
        if preview.fleet_id is not None:
            print(f"  舰队 {preview.fleet_id}  {preview.mission}  {preview.target}")
        if not args.execute:
            print(
                "  预览模式；真实召回需要 --execute --allow <舰队 id>"
                f"（候选：{', '.join(str(item.id) for item in candidates) or '无'}）"
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
