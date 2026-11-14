#!/usr/bin/env python3
"""Read-only TUI dashboard for the auto-play loop.

It only displays what the daemon already wrote to ``state/`` -- it never talks
to the game and never triggers actions.  Keys: ``q`` quit, ``r`` refresh,
``j``/``k`` move, ``PgUp``/``PgDn`` page, ``g``/``G`` top/bottom, ``d`` open
the selected journal entry as JSON.

``--once`` renders a single frame to stdout (no curses), which is handy for
scripts, logs and tests.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from autoplay.dashboard import (
    FrameLine,
    load_state,
    render_lines,
    render_text,
    text_width,
    truncate,
)

FOOTER = "q 退出 · r 刷新 · j/k 移动 · PgUp/PgDn 翻页 · g/G 顶/底 · d 详情"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--state-dir", default="state", help="directory with the JSONL files")
    parser.add_argument("--refresh", type=float, default=3.0, help="seconds between reloads")
    parser.add_argument("--width", type=int, default=110, help="width for --once output")
    parser.add_argument("--log-lines", type=int, default=10, help="journal lines to show")
    parser.add_argument("--event-lines", type=int, default=4, help="event/alert lines to show")
    parser.add_argument("--once", action="store_true", help="print one frame and exit")
    return parser.parse_args(argv)


def _frame(state_dir: Path, width: int, log_lines: int, event_lines: int) -> List[str]:
    state = load_state(state_dir)
    return render_text(
        state, width=width, log_lines=log_lines, event_lines=event_lines
    )


def _init_colors() -> bool:
    import curses

    if not curses.has_colors():
        return False
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_GREEN, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    return True


def _attr(style: str, colors: bool) -> int:
    import curses

    if style == "title":
        return curses.A_BOLD
    if style == "section":
        return curses.color_pair(2) | curses.A_BOLD if colors else curses.A_BOLD
    if style == "ok":
        return curses.color_pair(3) if colors else curses.A_NORMAL
    if style == "warn":
        return curses.color_pair(4) if colors else curses.A_BOLD
    if style == "error":
        return curses.color_pair(1) if colors else curses.A_BOLD
    if style == "alert":
        return curses.color_pair(1) | curses.A_BOLD if colors else curses.A_BOLD
    if style == "muted":
        return curses.A_DIM
    if style == "selected":
        return curses.color_pair(2) if colors else curses.A_BOLD
    return curses.A_NORMAL


def _write(
    stdscr,
    row: int,
    text: str,
    attr: int,
    width: int,
) -> None:
    import curses

    text = truncate(text, max(0, width))
    try:
        stdscr.addnstr(row, 0, text, max(0, len(text)), attr)
    except curses.error:
        pass


def _footer(stdscr, height: int, width: int, text: str) -> None:
    import curses

    left = truncate(text, max(0, width - 1))
    padded = left + " " * max(0, width - 1 - text_width(left))
    try:
        stdscr.addnstr(height - 1, 0, padded, max(0, width - 1), curses.A_REVERSE)
    except curses.error:
        pass


def _draw(
    stdscr,
    frame: List[FrameLine],
    scroll: int,
    cursor: int,
    colors: bool,
    footer: str,
) -> None:
    import curses

    height, width = stdscr.getmaxyx()
    stdscr.erase()
    viewport = max(1, height - 1)
    for row in range(viewport):
        index = scroll + row
        if index >= len(frame):
            break
        line = frame[index]
        attr = _attr(line.style, colors)
        if index == cursor:
            attr |= curses.A_REVERSE
        _write(stdscr, row, line.text, attr, max(0, width - 1))
    _footer(stdscr, height, width, footer)
    stdscr.refresh()


def _draw_detail(
    stdscr,
    title: str,
    lines: List[str],
    scroll: int,
    colors: bool,
) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    viewport = max(1, height - 1)
    try:
        stdscr.addnstr(0, 0, truncate(title, max(0, width - 1)), max(0, width - 1), _attr("title", colors))
    except Exception:  # noqa: BLE001 - curses errors are terminal quirks
        pass
    for row in range(1, viewport):
        index = scroll + row - 1
        if index >= len(lines):
            break
        _write(stdscr, row, lines[index], _attr("normal", colors), max(0, width - 1))
    total = max(1, len(lines))
    _footer(
        stdscr,
        height,
        width,
        f"q 退出 · d/Esc 返回 · j/k 滚动 · {min(len(lines), scroll + 1)}/{total}",
    )
    stdscr.refresh()


def _loop(stdscr, args: argparse.Namespace) -> None:
    import curses

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(250)
    colors = _init_colors()

    state_dir = Path(args.state_dir)
    last_reload = 0.0
    frame: List[FrameLine] = []
    scroll = 0
    cursor = 0
    detail_lines: Optional[List[str]] = None
    detail_title = ""
    detail_scroll = 0

    while True:
        now = time.monotonic()
        if now - last_reload >= args.refresh or not frame:
            height, width = stdscr.getmaxyx()
            state = load_state(state_dir)
            frame = render_lines(
                state,
                width=max(40, width - 1),
                log_lines=args.log_lines,
                event_lines=args.event_lines,
            )
            last_reload = now
            if cursor >= len(frame):
                cursor = max(0, len(frame) - 1)

        height, width = stdscr.getmaxyx()
        viewport = max(1, height - 1)

        if detail_lines is not None:
            _draw_detail(stdscr, detail_title, detail_lines, detail_scroll, colors)
        else:
            max_scroll = max(0, len(frame) - viewport)
            if cursor < scroll:
                scroll = cursor
            elif cursor >= scroll + viewport:
                scroll = cursor - viewport + 1
            scroll = max(0, min(scroll, max_scroll))
            footer = f"{FOOTER} · {cursor + 1}/{len(frame)}"
            _draw(stdscr, frame, scroll, cursor, colors, footer)

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key == 27:  # Esc
            if detail_lines is not None:
                detail_lines = None
            else:
                return
        if key in (ord("r"), ord("R"), curses.KEY_RESIZE):
            last_reload = 0.0
            continue

        if detail_lines is not None:
            if key in (ord("d"), ord("D")):
                detail_lines = None
            elif key in (ord("j"), curses.KEY_DOWN):
                detail_scroll = min(detail_scroll + 1, max(0, len(detail_lines) - 1))
            elif key in (ord("k"), curses.KEY_UP):
                detail_scroll = max(0, detail_scroll - 1)
            elif key == curses.KEY_NPAGE:
                detail_scroll = min(detail_scroll + viewport, max(0, len(detail_lines) - 1))
            elif key == curses.KEY_PPAGE:
                detail_scroll = max(0, detail_scroll - viewport)
            elif key == ord("g"):
                detail_scroll = 0
            elif key == ord("G"):
                detail_scroll = max(0, len(detail_lines) - 1)
            continue

        if key in (ord("j"), curses.KEY_DOWN):
            cursor = min(len(frame) - 1, cursor + 1)
        elif key in (ord("k"), curses.KEY_UP):
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_NPAGE:
            cursor = min(len(frame) - 1, cursor + viewport)
        elif key == curses.KEY_PPAGE:
            cursor = max(0, cursor - viewport)
        elif key == ord("g"):
            cursor = 0
            scroll = 0
        elif key == ord("G"):
            cursor = max(0, len(frame) - 1)
        elif key in (ord("d"), ord("D")):
            if frame and frame[cursor].entry is not None:
                entry = frame[cursor].entry or {}
                detail_title = f"详情 · {entry.get('kind') or 'cycle'} · {entry.get('at') or '-'}"
                detail_lines = json.dumps(
                    entry, ensure_ascii=False, indent=1
                ).splitlines()
                detail_scroll = 0


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
