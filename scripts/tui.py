#!/usr/bin/env python3
"""Read-only TUI dashboard for the auto-play loop.

It only displays what the daemon already wrote to ``state/`` -- it never talks
to the game and never triggers actions.  There are four tabs: the live
dashboard, the leaderboard collected into ``state/statistics.json``, the inbox
reader from ``state/messages.json`` and the battle hall from ``state/battles.json``.

Keys: ``q`` quit, ``r`` refresh, ``Tab``/``1``-``4`` switch tabs,
``j``/``k`` move, ``PgUp``/``PgDn`` page, ``g``/``G`` top/bottom, ``d`` open
the selected journal entry as JSON, ``w`` switch players/alliances on the
leaderboard tab, Enter/``d`` read a message or battle, ``o`` switch the battle
ordering between unit score and date.

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
    battle_rows,
    fmt_message_time,
    load_state,
    render_battle_detail,
    render_battle_text,
    render_battles_lines,
    render_battles_text,
    render_lines,
    render_message_body,
    render_message_text,
    render_messages_lines,
    render_messages_text,
    render_statistics_lines,
    render_statistics_text,
    render_text,
    text_width,
    truncate,
)

DASHBOARD_FOOTER = "q 退出 · r 刷新 · Tab 切页 · j/k 移动 · PgUp/PgDn 翻页 · g/G · d 详情"
LEADERBOARD_FOOTER = "q 退出 · r 刷新 · Tab 切页 · w 玩家/联盟 · j/k 移动 · PgUp/PgDn 翻页 · g/G"
MESSAGES_FOOTER = "q 退出 · r 刷新 · Tab 切页 · j/k 选择 · Enter/d 阅读 · PgUp/PgDn 翻页 · g/G"
BATTLES_FOOTER = "q 退出 · r 刷新 · Tab 切页 · o 单位/日期 · j/k 选择 · Enter/d 详情 · PgUp/PgDn 翻页 · g/G"
FOOTERS = (DASHBOARD_FOOTER, LEADERBOARD_FOOTER, MESSAGES_FOOTER, BATTLES_FOOTER)
TAB_LABELS = (" 1 看板 ", " 2 排行榜 ", " 3 消息 ", " 4 战役 ")


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
    parser.add_argument(
        "--tab",
        choices=("dashboard", "leaderboard", "messages", "battles"),
        default="dashboard",
        help="initial tab for --once and the interactive TUI",
    )
    parser.add_argument(
        "--stats-who",
        type=int,
        choices=(1, 2),
        default=1,
        help="leaderboard view: 1=players, 2=alliances",
    )
    parser.add_argument(
        "--battles-order",
        choices=("units", "date"),
        default="units",
        help="battle hall ordering: units=unit score, date=battle time",
    )
    parser.add_argument(
        "--message-index",
        type=int,
        default=None,
        help="with --once --tab messages, print this message body instead of the list",
    )
    parser.add_argument(
        "--battle-index",
        type=int,
        default=None,
        help="with --once --tab battles, print this battle detail instead of the list",
    )
    return parser.parse_args(argv)


def _frame(
    state_dir: Path,
    width: int,
    log_lines: int,
    event_lines: int,
    *,
    tab: str = "dashboard",
    stats_who: int = 1,
    battles_order: str = "units",
    message_index: Optional[int] = None,
    battle_index: Optional[int] = None,
) -> List[str]:
    state = load_state(state_dir)
    if tab == "leaderboard":
        return render_statistics_text(state, who=stats_who, width=width)
    if tab == "messages":
        if message_index is not None:
            return render_message_text(state, index=message_index, width=width)
        return render_messages_text(state, width=width)
    if tab == "battles":
        if battle_index is not None:
            return render_battle_text(
                state, order=battles_order, index=battle_index, width=width
            )
        return render_battles_text(state, order=battles_order, width=width)
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


def _draw_tabs(stdscr, tab: int, colors: bool, width: int) -> None:
    import curses

    try:
        stdscr.move(0, 0)
        stdscr.clrtoeol()
    except curses.error:
        pass
    x = 0
    for index, label in enumerate(TAB_LABELS):
        attr = (
            curses.A_REVERSE | curses.A_BOLD
            if index == tab
            else (curses.A_DIM if colors else curses.A_NORMAL)
        )
        try:
            stdscr.addnstr(0, x, label, max(0, width - 1 - x), attr)
        except curses.error:
            pass
        x += text_width(label)


def _draw(
    stdscr,
    frame: List[FrameLine],
    scroll: int,
    cursor: int,
    colors: bool,
    footer: str,
    tab: int,
) -> None:
    import curses

    height, width = stdscr.getmaxyx()
    stdscr.erase()
    _draw_tabs(stdscr, tab, colors, width)
    viewport = max(1, height - 2)
    for row in range(viewport):
        index = scroll + row
        if index >= len(frame):
            break
        line = frame[index]
        attr = _attr(line.style, colors)
        if index == cursor:
            attr |= curses.A_REVERSE
        _write(stdscr, row + 1, line.text, attr, max(0, width - 1))
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
    tab = {"dashboard": 0, "leaderboard": 1, "messages": 2, "battles": 3}[
        args.tab
    ]
    stats_who = args.stats_who
    battle_order = args.battles_order
    last_reload = 0.0
    state = None
    frame: List[FrameLine] = []
    scroll = 0
    cursor = 0
    msg_cursor = 0
    battle_cursor = 0
    detail_lines: Optional[List[str]] = None
    detail_title = ""
    detail_scroll = 0

    while True:
        now = time.monotonic()
        if now - last_reload >= args.refresh or not frame:
            height, width = stdscr.getmaxyx()
            state = load_state(state_dir)
            if tab == 0:
                frame = render_lines(
                    state,
                    width=max(40, width - 1),
                    log_lines=args.log_lines,
                    event_lines=args.event_lines,
                )
            elif tab == 1:
                frame = render_statistics_lines(
                    state,
                    who=stats_who,
                    width=max(40, width - 1),
                )
            elif tab == 2:
                messages = (state.message_log or {}).get("messages") or []
                msg_cursor = max(0, min(msg_cursor, max(0, len(messages) - 1)))
                frame = render_messages_lines(
                    state,
                    width=max(40, width - 1),
                    selected=msg_cursor,
                )
            else:
                rows = battle_rows(state.battle_log or {}, battle_order, "desc")
                battle_cursor = max(0, min(battle_cursor, max(0, len(rows) - 1)))
                frame = render_battles_lines(
                    state,
                    order=battle_order,
                    width=max(40, width - 1),
                    selected=battle_cursor,
                )
            last_reload = now
            cursor = max(0, min(cursor, len(frame) - 1))

        height, width = stdscr.getmaxyx()
        viewport = max(1, height - 2)
        if tab == 3:
            rows = battle_rows(state.battle_log or {}, battle_order, "desc")
            cursor = max(0, len(frame) - len(rows) + battle_cursor) if rows else 0

        if detail_lines is not None:
            _draw_detail(stdscr, detail_title, detail_lines, detail_scroll, colors)
        else:
            max_scroll = max(0, len(frame) - viewport)
            if cursor < scroll:
                scroll = cursor
            elif cursor >= scroll + viewport:
                scroll = cursor - viewport + 1
            scroll = max(0, min(scroll, max_scroll))
            base = FOOTERS[tab]
            if tab == 3:
                position = f"{battle_cursor + 1}/{len(rows)}" if rows else "-"
            else:
                position = f"{cursor + 1}/{len(frame)}"
            footer = f"{base} · {position}"
            _draw(stdscr, frame, scroll, cursor, colors, footer, tab)

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key == 27:  # Esc
            if detail_lines is not None:
                detail_lines = None
            else:
                return

        if key in (9, curses.KEY_BTAB):
            tab = (tab + (1 if key == 9 else -1)) % len(TAB_LABELS)
            cursor = 0
            scroll = 0
            detail_lines = None
            last_reload = 0.0
            continue
        if key in (ord("1"), ord("2"), ord("3"), ord("4")):
            tab = key - ord("1")
            cursor = 0
            scroll = 0
            detail_lines = None
            last_reload = 0.0
            continue
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

        if tab == 2:
            messages = (state.message_log or {}).get("messages") or []
            if messages:
                if key in (ord("j"), curses.KEY_DOWN):
                    msg_cursor = min(len(messages) - 1, msg_cursor + 1)
                elif key in (ord("k"), curses.KEY_UP):
                    msg_cursor = max(0, msg_cursor - 1)
                elif key == curses.KEY_NPAGE:
                    msg_cursor = min(len(messages) - 1, msg_cursor + viewport)
                elif key == curses.KEY_PPAGE:
                    msg_cursor = max(0, msg_cursor - viewport)
                elif key == ord("g"):
                    msg_cursor = 0
                elif key == ord("G"):
                    msg_cursor = max(0, len(messages) - 1)
                elif key in (10, 13, curses.KEY_ENTER, ord("d"), ord("D")):
                    message = messages[msg_cursor]
                    detail_title = (
                        "消息 · "
                        f"{fmt_message_time(message, now=state.now)}"
                        f" · {message.get('sender') or '?'}"
                    )
                    detail_lines = render_message_body(
                        message, width=max(40, width - 1)
                    )
                    detail_scroll = 0
            if detail_lines is None:
                frame = render_messages_lines(
                    state,
                    width=max(40, width - 1),
                    selected=msg_cursor,
                )
            continue

        if tab == 3:
            rows = battle_rows(state.battle_log or {}, battle_order, "desc")
            if key in (ord("o"), ord("O")):
                battle_order = "date" if battle_order == "units" else "units"
                battle_cursor = 0
                scroll = 0
                last_reload = 0.0
                continue
            if rows:
                if key in (ord("j"), curses.KEY_DOWN):
                    battle_cursor = min(len(rows) - 1, battle_cursor + 1)
                elif key in (ord("k"), curses.KEY_UP):
                    battle_cursor = max(0, battle_cursor - 1)
                elif key == curses.KEY_NPAGE:
                    battle_cursor = min(len(rows) - 1, battle_cursor + viewport)
                elif key == curses.KEY_PPAGE:
                    battle_cursor = max(0, battle_cursor - viewport)
                elif key == ord("g"):
                    battle_cursor = 0
                elif key == ord("G"):
                    battle_cursor = max(0, len(rows) - 1)
                elif key in (10, 13, curses.KEY_ENTER, ord("d"), ord("D")):
                    row = rows[battle_cursor]
                    payload = state.battle_log or {}
                    detail_title = (
                        f"战役 · 第 {row.get('rank') or battle_cursor + 1} 名"
                        f" · {row.get('date') or '-'}"
                    )
                    detail_lines = render_battle_detail(
                        row,
                        base_url=str(payload.get("base_url") or ""),
                        width=max(40, width - 1),
                    )
                    detail_scroll = 0
            if detail_lines is None:
                frame = render_battles_lines(
                    state,
                    order=battle_order,
                    width=max(40, width - 1),
                    selected=battle_cursor,
                )
            continue

        if tab == 1 and key in (ord("w"), ord("W")):
            stats_who = 2 if stats_who == 1 else 1
            cursor = 0
            scroll = 0
            last_reload = 0.0
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
        elif tab == 0 and key in (ord("d"), ord("D")):
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
        print(
            "\n".join(
                _frame(
                    state_dir,
                    args.width,
                    args.log_lines,
                    args.event_lines,
                    tab=args.tab,
                    stats_who=args.stats_who,
                    battles_order=args.battles_order,
                    message_index=args.message_index,
                    battle_index=args.battle_index,
                )
            )
        )
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
