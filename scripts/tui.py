#!/usr/bin/env python3
"""Read-only TUI dashboard for the auto-play loop.

It only displays what the daemon already wrote to ``state/`` -- it never talks
to the game and never triggers actions.  Keys: ``q`` quit, ``r`` refresh now.

``--once`` renders a single frame to stdout (no curses), which is handy for
scripts, logs and tests.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from autoplay.dashboard import load_state, render_text

FOOTER = "q 退出 · r 刷新 · 数据来自 state/（只读）"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--state-dir", default="state", help="directory with the JSONL files")
    parser.add_argument("--refresh", type=float, default=3.0, help="seconds between reloads")
    parser.add_argument("--width", type=int, default=110, help="width for --once output")
    parser.add_argument("--log-lines", type=int, default=10, help="journal lines to show")
    parser.add_argument("--event-lines", type=int, default=6, help="event/alert lines to show")
    parser.add_argument("--once", action="store_true", help="print one frame and exit")
    return parser.parse_args(argv)


def _frame(state_dir: Path, width: int, log_lines: int, event_lines: int) -> List[str]:
    state = load_state(state_dir)
    return render_text(
        state, width=width, log_lines=log_lines, event_lines=event_lines
    )


def _loop(stdscr, args: argparse.Namespace) -> None:
    import curses

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(250)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)

    state_dir = Path(args.state_dir)
    last_reload = 0.0
    lines: List[str] = []

    while True:
        now = time.monotonic()
        if now - last_reload >= args.refresh or not lines:
            height, width = stdscr.getmaxyx()
            lines = _frame(state_dir, max(40, width - 1), args.log_lines, args.event_lines)
            last_reload = now

        height, width = stdscr.getmaxyx()
        stdscr.erase()
        for index, line in enumerate(lines[: max(1, height - 1)]):
            attributes = curses.A_NORMAL
            if "ALERT" in line or "error" in line:
                attributes = curses.color_pair(1) if curses.has_colors() else curses.A_BOLD
            elif index == 0:
                attributes = curses.A_BOLD
            try:
                stdscr.addnstr(index, 0, line, max(0, width - 1), attributes)
            except curses.error:
                pass

        try:
            stdscr.addnstr(
                height - 1,
                0,
                FOOTER.ljust(max(0, width - 1)),
                max(0, width - 1),
                curses.A_REVERSE,
            )
        except curses.error:
            pass
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return
        if key in (ord("r"), ord("R"), curses.KEY_RESIZE):
            last_reload = 0.0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    state_dir = Path(args.state_dir)
    if not state_dir.exists():
        print(f"state directory not found: {state_dir}", file=sys.stderr)
        return 2

    if args.once:
        print("\n".join(_frame(state_dir, args.width, args.log_lines, args.event_lines)))
        return 0

    try:
        import curses
    except ImportError:
        print("curses is not available; use --once", file=sys.stderr)
        return 2

    curses.wrapper(_loop, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
