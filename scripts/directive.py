#!/usr/bin/env python3
"""Inspect, validate or seed the strategy directive (AI-1).

The directive says *what to aim at* (phases, constraints, risk posture); the
planner still reads every number from the game pages.  This script is read-only
unless ``--write-default`` is passed.

Examples::

    .venv/bin/python scripts/directive.py                    # 当前生效的是什么
    .venv/bin/python scripts/directive.py --json
    .venv/bin/python scripts/directive.py --write-default    # 生成可编辑的默认策略
    .venv/bin/python scripts/directive.py --validate other.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from autoplay.directive import (
    DEFAULT_PATH,
    DirectiveError,
    default_directive,
    load_directive,
    parse_directive,
    save_directive,
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help=f"directive file to inspect or write (default: {DEFAULT_PATH})",
    )
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument(
        "--write-default",
        action="store_true",
        help="write the built-in default directive to --path (overwrites)",
    )
    parser.add_argument(
        "--validate",
        metavar="FILE",
        help="only validate FILE and exit (never writes)",
    )
    return parser.parse_args(argv)


def _print_human(directive) -> None:
    payload = directive.as_dict()
    age = payload.get("age_seconds")
    source = payload["source"]
    where = f"（{payload['path']}）" if payload.get("path") else ""
    print(f"来源    {source}{where}")
    print(f"版本    {payload['version']}")
    print(f"年龄    {'内置默认' if age is None else f'{int(age)} 秒'}")
    print(f"风险    {payload['risk_posture']}")
    print(f"失效     {payload['expires_when'] or '-'}")
    for phase in directive.phases:
        goal = f"{phase.goal_kind} {phase.goal_element}→{phase.goal_level}"
        if phase.goal_kind == "milestone":
            goal = "milestone"
        print(f"  - {phase.key:16} {phase.name:8} {goal}  {phase.goal}")
    if payload["errors"]:
        print("校验错误：")
        for problem in payload["errors"]:
            print(f"  ! {problem}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.validate:
        target = Path(args.validate)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            directive = parse_directive(payload, path=str(target))
        except (OSError, json.JSONDecodeError, DirectiveError) as exc:
            print(f"{target}: 无效 — {exc}", file=sys.stderr)
            return 1
        print(f"{target}: OK（{len(directive.phases)} 个阶段）")
        return 0

    if args.write_default:
        target = Path(args.path)
        save_directive(str(target), default_directive())
        print(f"已写入默认策略：{target}")
        return 0

    directive = load_directive(args.path)
    if args.json:
        print(json.dumps(directive.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(directive)
    return 1 if directive.errors else 0


if __name__ == "__main__":
    sys.exit(main())
