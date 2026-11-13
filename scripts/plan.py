#!/usr/bin/env python3
"""Compute a read-only plan for the account and print it.

The plan is advisory: nothing is queued or clicked.  It has two sections:

* dependency blockers -- what the game says must be built/researched next to
  reach the current phase goal;
* production candidates -- buildings ranked by payback time.

Run with ``--json`` for machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from typing import Optional, Sequence

from local_config import load_config

from autoplay import build_plan
from newstar_sdk import NewStarClient


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
    parser.add_argument("--json", action="store_true", help="print the plan as JSON")
    return parser.parse_args(argv)


def render(plan) -> str:
    lines = [
        f"阶段  {plan.phase_name} ({plan.phase})",
        f"目标  {plan.goal_status or plan.goal}",
    ]

    blockers = [action for action in plan.actions if action.category == "blocker"]
    if blockers:
        lines.append("依赖（按深度与投入排序）:")
        for action in blockers:
            mark = "✔" if action.affordable_total else "✘"
            target = action.required_level or action.target_level
            lines.append(
                f"  - {action.name} {action.current_level}->{target}"
                f"（还差 {action.steps} 级）"
                f"  总投入 {action.total_cost_equivalent:,.0f} 等价 {mark}\n"
                f"    {action.reason}"
            )

    roi = [action for action in plan.actions if action.category == "roi"]
    if roi:
        lines.append("产能候选（按回本排序）:")
        for action in roi:
            payback = f"{action.payback_hours:.2f}h" if action.payback_hours else "-"
            mark = "✔" if action.affordable else "✘"
            lines.append(
                f"  - {action.name} {action.current_level}->{action.target_level}"
                f"  回本 {payback:>7}  等价 {action.cost_equivalent:,.0f} {mark}"
                f"  {action.reason}"
            )

    if plan.notes:
        lines.append("备注:")
        for note in plan.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    client = NewStarClient(
        config.base_url,
        config.username,
        config.password,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        request_interval=config.interval,
    )
    try:
        client.login()
        plan = build_plan(client)
        if args.json:
            print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(plan))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
