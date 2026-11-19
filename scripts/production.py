#!/usr/bin/env python3
"""Production ratios and the power-balance response (read-only by default).

Shows the current ``prod[]`` percentages, the net power rate and, when power is
short, the one step the planner would take.  A real write needs ``--execute``
plus an allow-list of element ids; the SDK re-reads the page afterwards and
raises ``ActionRejected`` if the change did not stick.

Examples::

    .venv/bin/python scripts/production.py --no-verify-ssl
    .venv/bin/python scripts/production.py --no-verify-ssl --json
    .venv/bin/python scripts/production.py --no-verify-ssl --set 1=90
    .venv/bin/python scripts/production.py --no-verify-ssl --set 1=90 --execute --allow 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

from local_config import add_session_args, load_config, make_client

from autoplay.power import (
    ELEMENT_NAMES,
    apply_ratio_plan,
    plan_power_response,
)
from newstar_sdk.exceptions import ActionRejected, NewStarError


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
    parser.add_argument("--interval", type=float, default=0.4, help="seconds between requests")
    parser.add_argument("--json", action="store_true", help="print as JSON")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="ELEMENT=RATIO",
        help="explicit change in percent, e.g. 1=90 (repeatable)",
    )
    parser.add_argument(
        "--execute", action="store_true", help="actually post (requires --allow)"
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="element id whose ratio may change (repeatable)",
    )
    add_session_args(parser)
    return parser.parse_args(argv)


def _parse_changes(values: Sequence[str]) -> Dict[int, int]:
    changes: Dict[int, int] = {}
    for raw in values:
        element, _, ratio = raw.partition("=")
        if not element.strip().isdigit() or not ratio.strip().isdigit():
            raise ValueError(f"--set 需要 ELEMENT=PERCENT（0..100），收到 {raw!r}")
        level = int(ratio)
        if not 0 <= level <= 100 or level % 10:
            raise ValueError(f"比例必须是 0..100 的 10 的倍数，收到 {raw!r}")
        changes[int(element)] = level
    return changes


def _print_report(report, plan) -> None:
    net = plan.net_power
    print(f"电力净产  {net:+,.0f}/h" if net is not None else "电力净产  未知")
    for consumer in plan.consumers:
        print(
            f"  消耗  {consumer['name']} #{consumer['element_id']}"
            f"  {consumer['consumption']:,.0f}/h  当前 {consumer['ratio']}%"
        )
    ratios = ", ".join(
        f"{ELEMENT_NAMES.get(int(element), element)}={level}%"
        for element, level in sorted(report.ratios.items())
    )
    print(f"比例  {ratios}")
    print(f"判断  {plan.reason}")
    if plan.changes:
        print("计划：")
        for element, level in plan.changes.items():
            print(
                f"  {ELEMENT_NAMES.get(int(element), element)} "
                f"{plan.before[element]}% → {level}%"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        explicit = _parse_changes(args.set)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.execute and not args.allow:
        print("--execute needs at least one --allow element id", file=sys.stderr)
        return 2
    if args.execute and Path("state/STOP").exists():
        print("state/STOP exists; refusing to write production ratios", file=sys.stderr)
        return 2

    config = load_config(args)
    if not config.verify_ssl:
        import warnings

        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    with make_client(config) as client:
        try:
            report = client.get_production()
        except NewStarError as exc:
            print(f"读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        if explicit:
            plan = plan_power_response(report)
            plan.changes = dict(explicit)
            plan.before = {
                element: int(report.ratios.get(element, 10)) for element in explicit
            }
            plan.reason = "按 --set 指定的显式改动"
            if not args.json:
                unknown = sorted(set(explicit) - set(report.ratios))
                if unknown:
                    print(
                        f"--set 的元素不在页面上：{unknown}", file=sys.stderr
                    )
                    return 2
        else:
            plan = plan_power_response(report)

        if args.json:
            payload = {
                "net_power": plan.net_power,
                "ratios": {str(k): v for k, v in report.ratios.items()},
                "plan": plan.as_dict(),
            }
        else:
            _print_report(report, plan)

        if not plan.changes:
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        try:
            allowed = [int(item) for item in args.allow]
        except ValueError:
            print("--allow values must be element ids", file=sys.stderr)
            return 2

        try:
            result = apply_ratio_plan(
                client, plan, dry_run=not args.execute, allowed=allowed
            )
        except ActionRejected as exc:
            print(f"写入被拒：{exc}", file=sys.stderr)
            return 1

        if args.json:
            payload["result"] = result.as_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"结果  {result.status}：{result.reason}")
            if result.after:
                for element, level in result.after.items():
                    print(
                        f"  {ELEMENT_NAMES.get(int(element), element)} "
                        f"现在是 {level}%"
                    )
            if result.status == "preview":
                print("  预览模式；真实写入需要 --execute --allow <元素 id>")
        return 0


if __name__ == "__main__":
    sys.exit(main())
