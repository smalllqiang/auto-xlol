#!/usr/bin/env python3
"""Compute a plan and execute at most one action.

Defaults to a dry run: without ``--execute`` nothing is changed, the script
only reports what it *would* do.  Execution additionally requires an explicit
allow-list (``--allow``) and is refused while ``state/STOP`` exists.

Every run is journaled as one JSON line (``state/journal.jsonl`` by default).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from local_config import load_config

from autoplay import build_plan, execute_plan
from newstar_sdk import NewStarClient
from newstar_sdk.constants import BUILDINGS, TECHNOLOGIES

_NAME_INDEX: Dict[str, int] = {}
for _element_id, _element in BUILDINGS.items():
    _NAME_INDEX.setdefault(_element.name_zh, int(_element_id))
for _element_id, _element in TECHNOLOGIES.items():
    _NAME_INDEX.setdefault(_element.name_zh, int(_element_id))


def resolve_allow(values: Sequence[str]) -> List[int]:
    resolved: List[int] = []
    for value in values:
        text = str(value).strip()
        if text.isdigit():
            resolved.append(int(text))
            continue
        if text in _NAME_INDEX:
            resolved.append(_NAME_INDEX[text])
            continue
        raise SystemExit(f"unknown element in --allow: {value!r}")
    return resolved


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
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually perform the action (default: dry run)",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="ID|NAME",
        help="element id or Chinese name that may be acted on; repeatable",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="maximum metal-equivalent cost for one action",
    )
    parser.add_argument("--journal", default="state/journal.jsonl", help="journal JSONL file")
    parser.add_argument("--stop-file", default="state/STOP", help="kill-switch file")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser.parse_args(argv)


def _journal(path: Path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.execute and not args.allow:
        print("--execute requires at least one --allow element", file=sys.stderr)
        return 2

    stop_file = Path(args.stop_file)
    if stop_file.exists():
        print(f"kill switch present ({stop_file}); refusing to run", file=sys.stderr)
        return 3

    allowed = resolve_allow(args.allow) if args.allow else None
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
        result = execute_plan(
            client,
            plan,
            dry_run=not args.execute,
            allowed_elements=allowed,
            max_cost_equivalent=args.max_cost,
        )

        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "execute" if args.execute else "dry_run",
            "phase": plan.phase,
            "goal": plan.goal_status,
            **result.as_dict(),
        }
        _journal(Path(args.journal), entry)

        if args.json:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        else:
            print(f"阶段  {plan.phase_name} ({plan.phase})")
            print(f"目标  {plan.goal_status}")
            print(f"模式  {'execute' if args.execute else 'dry run'}")
            print(f"结果  {result.status}: {result.reason}")
            if result.queues:
                print(f"队列  {result.queues}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
