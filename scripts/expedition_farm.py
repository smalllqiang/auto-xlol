#!/usr/bin/env python3
"""Keep every expedition lane filled with a fixed fleet composition.

Default mode is read-only: it prints the current lanes, the configured fleet
and a fleetStep1+fleetStep2 preview.  ``--execute --allow 207`` starts the
unattended loop: every tick it reads the fleet table, fills free lanes and then
sleeps until the earliest expected return plus ``--return-delay`` (5 s by
default) or the next lane check (``--check-hours``, 10 h by default).

The composition is sent verbatim: the script only checks that the chosen
planet has enough of each ship type.  It does not calculate fleet points or
enforce the guide's 500,000-point target.  ``--ships`` accepts ids, Chinese or
English names and common aliases, e.g. ``--ships 战列巡洋舰=1000`` or
``--ships 207=1000,203=100``.  The default is 8621 battleships (207), which
happens to be the 500,000-point composition.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from local_config import (
    EXIT_SESSION_EXPIRED,
    SESSION_LOST_HINT,
    add_session_args,
    load_config,
    load_values,
    make_client,
)

from autoplay.expedition import EXPEDITION_TARGET_POSITION
from autoplay.expedition_scheduler import (
    DEFAULT_CHECK_SECONDS,
    DEFAULT_MAX_SLEEP,
    DEFAULT_RETRY_DELAY,
    DEFAULT_RETURN_DELAY,
    DEFAULT_SHIP_COUNT,
    DEFAULT_SHIP_ID,
    ExpeditionFillResult,
    ExpeditionPlan,
    ExpeditionTimetable,
    GameClock,
    ScheduledSlot,
    fill_expedition_slots,
    format_ships,
    iso_local,
    parse_composition,
    read_live_slots,
    read_max_slots,
)
from autoplay.journal import append_jsonl
from autoplay.runtime import EXIT_BUSY, acquire_lock, release_lock, write_status
from newstar_sdk.constants import CATEGORY_SHIP, resolve_element
from newstar_sdk.exceptions import AuthenticationError, NewStarError, SessionExpired
from newstar_sdk.models import Coordinates
from utils.notify import (
    DEFAULT_TIMEOUT,
    ENV_SECRET,
    ENV_TIMEOUT,
    ENV_WEBHOOK,
    FeishuNotifier,
)

#: Prefix used for every Feishu report so the group can tell topics apart.
NOTIFY_TITLE = "探险"


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
        "--planet",
        help="planet id, coordinates (3:505:8) or 1-based index; default: first planet",
    )
    parser.add_argument(
        "--target",
        help=f"expedition target coordinates (default: current system position {EXPEDITION_TARGET_POSITION})",
    )
    parser.add_argument(
        "--ships",
        default=f"{DEFAULT_SHIP_ID}={DEFAULT_SHIP_COUNT}",
        help=(
            "fleet composition as type=count pairs, e.g. 215=1000, "
            "战列巡洋舰=1000 or 207=1000,203=100; ids, Chinese/English names "
            "and aliases are accepted (default: "
            f"{DEFAULT_SHIP_ID}={DEFAULT_SHIP_COUNT}, battleships)"
        ),
    )
    parser.add_argument("--speed", type=int, default=10, help="fleet speed 1..10")
    parser.add_argument("--stay-time", type=int, default=1, help="expedition stay option (1..tech)")
    parser.add_argument(
        "--check-hours",
        type=float,
        default=DEFAULT_CHECK_SECONDS / 3600.0,
        help="re-read the maximum lane count every N hours (default: 10)",
    )
    parser.add_argument(
        "--return-delay",
        type=float,
        default=DEFAULT_RETURN_DELAY,
        help="seconds to wait after the expected return before the next wave (default: 5)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY,
        help="seconds before retrying when a lane is still busy or a send failed (default: 60)",
    )
    parser.add_argument(
        "--max-sleep",
        type=float,
        default=DEFAULT_MAX_SLEEP,
        help="maximum seconds between fleet-table refreshes (default: 900)",
    )
    parser.add_argument(
        "--max-sends",
        type=int,
        default=0,
        help="maximum fleets to send per tick (0 = fill every free lane)",
    )
    parser.add_argument(
        "--no-ship-check",
        action="store_true",
        help=(
            "skip the local available-ship count and send exactly the configured "
            "counts; the game decides whether the fleet is valid"
        ),
    )
    parser.add_argument(
        "--timetable",
        default="state/expedition_timetable.json",
        help="persistent timetable file (default: state/expedition_timetable.json)",
    )
    parser.add_argument(
        "--status-file",
        default="state/expedition_farm.json",
        help="heartbeat JSON for monitoring (default: state/expedition_farm.json)",
    )
    parser.add_argument(
        "--journal",
        default="state/journal.jsonl",
        help="append one expedition entry per tick (default: state/journal.jsonl)",
    )
    parser.add_argument(
        "--lock-file",
        default="state/daemon.lock",
        help="single-writer pid lock (shared with run_forever.py by default)",
    )
    parser.add_argument("--stop-file", default="state/STOP", help="emergency stop file")
    parser.add_argument("--execute", action="store_true", help="actually send (default: preview)")
    parser.add_argument("--once", action="store_true", help="with --execute: one fill pass, then exit")
    parser.add_argument(
        "--notify",
        action="store_true",
        help=(
            "把开始、报错和停止上报到飞书（默认关闭）；每个 tick 的正常结果不上报。"
            f"需要 .env 里的 {ENV_WEBHOOK}；没配置时本次不上报"
        ),
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="ID",
        help="repeatable ship id/name allow-list; required with --execute",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    add_session_args(parser)
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _planet_fields(planet: Any) -> Tuple[Optional[int], str, Optional[Coordinates]]:
    planet_id = getattr(planet, "id", None)
    try:
        planet_id = int(planet_id) if planet_id else None
    except (TypeError, ValueError):
        planet_id = None
    name = str(getattr(planet, "name", "") or "")
    coordinates = getattr(planet, "coordinates", None)
    return planet_id, name, coordinates


def resolve_planet(
    overview: Any,
    selector: Optional[str],
    saved_id: Optional[int] = None,
) -> Any:
    """Pick a planet from the overview list.

    ``--planet`` accepts an id, coordinates, or a 1-based index.  Without it,
    the saved timetable planet is preferred and the first planet is the final
    default.
    """

    planets: List[Any] = list(getattr(overview, "planets", []) or [])
    current = getattr(overview, "planet", None)
    chosen: Optional[Any] = None

    if selector:
        text = selector.strip()
        if text.isdigit():
            number = int(text)
            chosen = next(
                (item for item in planets if _planet_fields(item)[0] == number),
                None,
            )
            if chosen is None and 1 <= number <= len(planets):
                chosen = planets[number - 1]
            if chosen is None:
                current_id, _, _ = _planet_fields(current)
                if current_id == number:
                    chosen = current
        else:
            try:
                wanted = Coordinates.parse(text)
            except ValueError as exc:
                raise ValueError(f"无法解析星球坐标：{selector!r}") from exc
            chosen = next(
                (
                    item
                    for item in planets
                    if _planet_fields(item)[2] is not None
                    and _planet_fields(item)[2].galaxy == wanted.galaxy
                    and _planet_fields(item)[2].system == wanted.system
                    and _planet_fields(item)[2].planet == wanted.planet
                ),
                None,
            )
            if chosen is None:
                _, _, current_coordinates = _planet_fields(current)
                if (
                    current_coordinates is not None
                    and current_coordinates.galaxy == wanted.galaxy
                    and current_coordinates.system == wanted.system
                    and current_coordinates.planet == wanted.planet
                ):
                    chosen = current
        if chosen is None:
            raise ValueError(f"找不到星球：{selector!r}")
        return chosen

    if saved_id is not None:
        chosen = next(
            (item for item in planets if _planet_fields(item)[0] == saved_id),
            None,
        )
    if chosen is None and planets:
        chosen = planets[0]
    if chosen is None and current is not None:
        chosen = current
    if chosen is None:
        raise ValueError("概览页没有可用星球")
    return chosen


def resolve_target(coordinates: Optional[Coordinates], override: Optional[str]) -> str:
    if override:
        try:
            target = Coordinates.parse(override)
        except ValueError as exc:
            raise ValueError(f"无法解析目标坐标：{override!r}") from exc
        return f"{target.galaxy}:{target.system}:{target.planet}"
    if coordinates is None:
        raise ValueError("当前星球没有坐标，无法确定探险目标")
    return f"{coordinates.galaxy}:{coordinates.system}:{EXPEDITION_TARGET_POSITION}"


def _ship_ids(values: Sequence[str]) -> List[int]:
    ids: List[int] = []
    for value in values:
        for part in str(value).replace("，", ",").split(","):
            text = part.strip()
            if not text:
                continue
            try:
                ids.append(resolve_element(text, CATEGORY_SHIP))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"--allow 只接受舰船 id/名称：{text!r}") from exc
    return ids


def _check_args(args: argparse.Namespace, ships: Dict[int, int]) -> Optional[int]:
    if args.speed < 1 or args.speed > 10:
        print("--speed 必须在 1..10", file=sys.stderr)
        return 2
    if args.stay_time < 1:
        print("--stay-time 必须至少为 1（0 会被游戏拒绝）", file=sys.stderr)
        return 2
    if args.check_hours <= 0:
        print("--check-hours 必须大于 0", file=sys.stderr)
        return 2
    if args.return_delay < 0:
        print("--return-delay 不能为负", file=sys.stderr)
        return 2
    if args.retry_delay < 0:
        print("--retry-delay 不能为负", file=sys.stderr)
        return 2
    if args.max_sleep < 1:
        print("--max-sleep 必须至少为 1", file=sys.stderr)
        return 2
    if args.max_sends < 0:
        print("--max-sends 不能为负", file=sys.stderr)
        return 2
    if args.execute:
        try:
            allowed = set(_ship_ids(args.allow))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not allowed:
            print("--execute requires at least one --allow ship id", file=sys.stderr)
            return 2
        missing = sorted(set(ships) - allowed)
        if missing:
            print(
                f"舰船组成含未允许的 id：{missing}；请把它们加入 --allow",
                file=sys.stderr,
            )
            return 2
    return None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _make_notifier(args: argparse.Namespace) -> Optional[FeishuNotifier]:
    """Build the opt-in Feishu channel, or ``None`` when it is unavailable.

    ``--notify`` is the first gate; an actual webhook in ``.env`` is the second.
    A missing webhook is only a warning: the farm keeps running without reports.
    """

    if not getattr(args, "notify", False):
        return None
    values = load_values(args)
    webhook = values.get(ENV_WEBHOOK, "").strip()
    if not webhook:
        print(
            f"提示：--notify 需要 {ENV_WEBHOOK}（.env）；未配置，本次不上报",
            file=sys.stderr,
        )
        return None
    return FeishuNotifier(
        webhook,
        values.get(ENV_SECRET, "").strip(),
        values.get(ENV_TIMEOUT, "").strip() or DEFAULT_TIMEOUT,
    )


def _report(notifier: Optional[FeishuNotifier], text: str) -> None:
    """Best-effort report; a delivery problem must never stop the farm."""

    if notifier is None:
        return
    try:
        result = notifier.send_text(f"[{NOTIFY_TITLE}] {text}")
    except Exception as exc:  # noqa: BLE001 - reporting is best-effort
        print(
            f"警告：飞书上报异常：{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return
    if not result.ok and not result.skipped:
        print(f"警告：飞书上报失败：{result.error}", file=sys.stderr, flush=True)


# Only these three events are pushed to the group; the normal result of each
# tick stays in the journal and the status file so a healthy farm is silent.
REPORT_START = "开始"
REPORT_ERROR = "报错"
REPORT_STOP = "停止"

#: Fill statuses that mean "something went wrong" and deserve an error report.
_FILL_ERROR_STATUSES = frozenset({"error", "partial"})


def _fill_failed(fill: ExpeditionFillResult) -> bool:
    return fill.status in _FILL_ERROR_STATUSES


def _blocker_reason(fill: ExpeditionFillResult) -> str:
    """Return the error text for a tick that could not do its job, else ``""``.

    A full fleet table (``free == 0``) and a deliberate stop are normal, so a
    healthy farm reports nothing; free lanes that stayed empty (ship shortage)
    and a missing lane count are problems worth one error report.
    """

    if _fill_failed(fill):
        return f"{fill.status}  {fill.reason}"
    if fill.status != "skipped":
        return ""
    if not fill.reason or fill.reason.startswith("急停"):
        return ""
    if fill.free > 0:
        return f"空闲航道未派出：{fill.reason}"
    if fill.max_slots <= 0:
        return f"没有可用航道：{fill.reason}"
    return ""


def _planet_line(
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
) -> str:
    coordinates = str(planet_coordinates) if planet_coordinates is not None else "-"
    return (
        f"星球      {planet_name or '-'} {coordinates}"
        f"（id {planet_id if planet_id is not None else '-'}）"
    )


def report_start(
    notifier: Optional[FeishuNotifier],
    *,
    mode: str,
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
    plan: ExpeditionPlan,
) -> None:
    _report(
        notifier,
        "\n".join(
            [
                f"{REPORT_START}（{mode}）",
                _planet_line(planet_id, planet_name, planet_coordinates),
                f"目标      {plan.target}",
                f"编队      {format_ships(plan.ships)}  速度 {plan.speed}  "
                f"停留 {plan.stay_time}",
            ]
        ),
    )


def report_stop(
    notifier: Optional[FeishuNotifier],
    *,
    mode: str,
    reason: str,
    timetable_path: str,
) -> None:
    _report(
        notifier,
        f"{REPORT_STOP}（{mode}）：{reason}；时间表已保存到 {timetable_path}",
    )


def report_error(
    notifier: Optional[FeishuNotifier],
    *,
    scope: str,
    reason: str,
) -> None:
    _report(notifier, f"{REPORT_ERROR}（{scope}）：{reason}")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def render_status(
    *,
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
    plan: ExpeditionPlan,
    tech: int,
    max_slots: int,
    slots: Sequence[ScheduledSlot],
    fill: ExpeditionFillResult,
    next_wake: Optional[float],
) -> str:
    coordinates = str(planet_coordinates) if planet_coordinates is not None else "-"
    lines = [
        f"星球      {planet_name or '-'} {coordinates}（id {planet_id if planet_id is not None else '-'}）",
        f"目标      {plan.target}",
        f"编队      {format_ships(plan.ships)}  速度 {plan.speed}  停留 {plan.stay_time}",
        f"探险技术  {tech} → 最大航道 {max_slots}",
        f"当前占用  {len(slots)} / {max_slots}（空闲 {max(0, max_slots - len(slots))}）",
    ]
    if slots:
        lines.append("时间表:")
        for index, slot in enumerate(slots, 1):
            expected = iso_local(slot.expected_return_at) or "未知"
            remaining = f"  剩余 {slot.remaining}" if slot.remaining else ""
            target = slot.target or "-"
            lines.append(f"  {index}. {target}  返航 {expected}{remaining}")
    else:
        lines.append("时间表:   空")
    lines.append(f"结果      {fill.status}  {fill.reason}")
    if fill.preview is not None:
        preview = fill.preview
        lines.append(
            f"预览燃料  {getattr(preview, 'consumption', None) if getattr(preview, 'consumption', None) is not None else '-'}"
        )
        stay_times = list(getattr(preview, "stay_times", []) or [])
        lines.append("停留选项  " + ("、".join(str(item) for item in stay_times) or "-"))
        missions = list(getattr(preview, "available_missions", []) or [])
        lines.append(
            "可用任务  "
            + ("、".join(f"{item.id}={item.name}" for item in missions) or "-")
        )
    if next_wake is not None:
        lines.append(f"下次唤醒  {iso_local(next_wake)}")
    return "\n".join(lines)


def render_json(
    *,
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
    plan: ExpeditionPlan,
    tech: int,
    max_slots: int,
    slots: Sequence[ScheduledSlot],
    fill: ExpeditionFillResult,
    next_wake: Optional[float],
) -> str:
    payload = {
        "planet": {
            "id": planet_id,
            "name": planet_name,
            "coordinates": str(planet_coordinates) if planet_coordinates else None,
        },
        "plan": plan.as_dict(),
        "expedition_tech": tech,
        "max_slots": max_slots,
        "active": len(slots),
        "free": max(0, max_slots - len(slots)),
        "slots": [slot.as_dict() for slot in slots],
        "fill": fill.as_dict(),
        "next_wake_at": next_wake,
        "next_wake_local": iso_local(next_wake),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Ticks
# ---------------------------------------------------------------------------


def run_tick(
    client: Any,
    *,
    plan: ExpeditionPlan,
    timetable: ExpeditionTimetable,
    clock: GameClock,
    planet_id: Optional[int],
    args: argparse.Namespace,
    force_check: bool = False,
    stop_check: Optional[Callable[[], bool]] = None,
) -> Tuple[int, int, List[ScheduledSlot], ExpeditionFillResult, float, GameClock]:
    """Read, reconcile and fill once; returns state for rendering and sleep."""

    now = time.time()
    tech = int(timetable.tech_level)
    previous_max_slots = int(timetable.max_slots)
    max_slots = previous_max_slots

    if (
        force_check
        or max_slots <= 0
        or tech <= 0
        or not timetable.next_check_at
        or timetable.next_check_at <= now
    ):
        overview = client.get_overview()
        current_id, _, _ = _planet_fields(getattr(overview, "planet", None))
        if planet_id is not None and current_id != planet_id:
            client.select_planet(int(planet_id))
            overview = client.get_overview()
        clock = GameClock.from_overview(overview)
        tech, max_slots = read_max_slots(client)
        timetable.tech_level = tech
        timetable.max_slots = max_slots
        timetable.checked_at = now
        timetable.next_check_at = now + float(args.check_hours) * 3600.0
        if not getattr(args, "json", False):
            if max_slots != previous_max_slots:
                print(
                    f"[{_stamp()}] 最大航道 → {max_slots}（探险技术 {tech}）",
                    flush=True,
                )
            else:
                print(
                    f"[{_stamp()}] 检查：探险技术 {tech}，最大航道 {max_slots}",
                    flush=True,
                )

    slots = read_live_slots(client, clock, plan_target=plan.target)
    timetable.record_slots(slots, now)
    fill = fill_expedition_slots(
        client,
        plan=plan,
        clock=clock,
        max_slots=max_slots,
        active=len(slots),
        max_sends=int(args.max_sends),
        check_ships=not args.no_ship_check,
        stop_check=stop_check,
    )
    for slot in fill.sent:
        timetable.record_send(slot, now)
    if any(slot.expected_return_at is None for slot in fill.sent):
        # The confirmation page did not give a parseable ``fleetEndTime``;
        # re-read the fleet table so the timetable still has a wake-up time.
        refreshed = read_live_slots(client, clock, plan_target=plan.target)
        if refreshed and len(refreshed) >= len(timetable.slots):
            timetable.record_slots(refreshed, now)

    if max_slots <= 0:
        next_wake = timetable.next_check_at or (now + float(args.check_hours) * 3600.0)
    else:
        next_wake = timetable.next_wake(
            now,
            return_delay=float(args.return_delay),
            retry_delay=float(args.retry_delay),
            max_sleep=float(args.max_sleep),
        )
    if fill.free > 0:
        next_wake = min(next_wake, now + max(1.0, float(args.retry_delay)))
    return tech, max_slots, list(timetable.slots), fill, next_wake, clock


def journal_tick(
    path: str,
    *,
    planet_id: Optional[int],
    tech: int,
    max_slots: int,
    slots: Sequence[ScheduledSlot],
    fill: ExpeditionFillResult,
    next_wake: float,
) -> None:
    try:
        append_jsonl(
            Path(path),
            {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": "expedition",
                "source": "expedition_farm",
                "planet": planet_id,
                "tech": tech,
                "max_slots": max_slots,
                "active": len(slots),
                "free": max(0, max_slots - len(slots)),
                "status": fill.status,
                "reason": fill.reason,
                "sent": [
                    {
                        "target": slot.target,
                        "ships": slot.ships,
                        "expected_return_at": iso_local(slot.expected_return_at),
                    }
                    for slot in fill.sent
                ],
                "next_wake_at": iso_local(next_wake),
            },
            max_bytes=10 * 1024 * 1024,
        )
    except OSError:
        pass


def publish_status(
    path: str,
    *,
    mode: str,
    planet_id: Optional[int],
    planet_name: str,
    plan: ExpeditionPlan,
    tech: int,
    max_slots: int,
    slots: Sequence[ScheduledSlot],
    fill: Optional[ExpeditionFillResult],
    next_wake: Optional[float],
    state: str,
) -> None:
    payload: Dict[str, Any] = {
        "schema": 1,
        "pid": os.getpid(),
        "mode": mode,
        "state": state,
        "planet": {
            "id": planet_id,
            "name": planet_name,
        },
        "plan": plan.as_dict(),
        "expedition_tech": tech,
        "max_slots": max_slots,
        "active": len(slots),
        "free": max(0, max_slots - len(slots)),
        "last_status": fill.status if fill is not None else None,
        "last_reason": fill.reason if fill is not None else None,
        "sent": len(fill.sent) if fill is not None else 0,
        "next_wake_at": iso_local(next_wake),
        "slots": [slot.as_dict() for slot in slots],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        write_status(Path(path), payload)
    except OSError:
        pass


def sleep_until(
    target: float,
    stop: Dict[str, bool],
    stop_file: Path,
    *,
    chunk: float = 5.0,
    heartbeat: Optional[Callable[[], None]] = None,
) -> None:
    last_beat = time.time()
    while not stop.get("requested"):
        if stop_file.exists():
            stop["requested"] = True
            break
        remaining = float(target) - time.time()
        if remaining <= 0:
            break
        if heartbeat is not None and time.time() - last_beat >= 60.0:
            heartbeat()
            last_beat = time.time()
        time.sleep(min(max(0.2, chunk), remaining))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_preview(
    client: Any,
    *,
    plan: ExpeditionPlan,
    clock: GameClock,
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
    args: argparse.Namespace,
    notifier: Optional[FeishuNotifier] = None,
) -> int:
    tech, max_slots = read_max_slots(client)
    slots = read_live_slots(client, clock, plan_target=plan.target)
    fill = fill_expedition_slots(
        client,
        plan=plan,
        clock=clock,
        max_slots=max_slots,
        active=len(slots),
        dry_run=True,
    )
    next_wake = None
    if max_slots > 0:
        timetable = ExpeditionTimetable(
            max_slots=max_slots,
            tech_level=tech,
            next_check_at=time.time() + float(args.check_hours) * 3600.0,
        )
        timetable.record_slots(slots)
        next_wake = timetable.next_wake(
            time.time(),
            return_delay=float(args.return_delay),
            retry_delay=float(args.retry_delay),
            max_sleep=float(args.max_sleep),
        )
    status_text = render_status(
        planet_id=planet_id,
        planet_name=planet_name,
        planet_coordinates=planet_coordinates,
        plan=plan,
        tech=tech,
        max_slots=max_slots,
        slots=slots,
        fill=fill,
        next_wake=next_wake,
    )
    if args.json:
        print(
            render_json(
                planet_id=planet_id,
                planet_name=planet_name,
                planet_coordinates=planet_coordinates,
                plan=plan,
                tech=tech,
                max_slots=max_slots,
                slots=slots,
                fill=fill,
                next_wake=next_wake,
            )
        )
    else:
        print("模式      预览（不会发送舰队）")
        print(status_text)
        print(
            "提示      真实执行加 --execute --allow "
            + ",".join(str(ship_id) for ship_id in sorted(plan.ships))
        )
    # A successful preview is a manual read-only action: it is printed to the
    # operator, not pushed to the group.  Only a preview error is reported.
    if _fill_failed(fill):
        report_error(notifier, scope="预览", reason=f"{fill.status}  {fill.reason}")
    return 1 if _fill_failed(fill) else 0


def run_once(
    client: Any,
    *,
    plan: ExpeditionPlan,
    timetable: ExpeditionTimetable,
    clock: GameClock,
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
    args: argparse.Namespace,
    notifier: Optional[FeishuNotifier] = None,
) -> int:
    report_start(
        notifier,
        mode="单轮",
        planet_id=planet_id,
        planet_name=planet_name,
        planet_coordinates=planet_coordinates,
        plan=plan,
    )
    tech, max_slots, slots, fill, next_wake, clock = run_tick(
        client,
        plan=plan,
        timetable=timetable,
        clock=clock,
        planet_id=planet_id,
        args=args,
        force_check=True,
        stop_check=lambda: Path(args.stop_file).exists(),
    )
    timetable.save()
    journal_tick(
        args.journal,
        planet_id=planet_id,
        tech=tech,
        max_slots=max_slots,
        slots=slots,
        fill=fill,
        next_wake=next_wake,
    )
    publish_status(
        args.status_file,
        mode="execute",
        planet_id=planet_id,
        planet_name=planet_name,
        plan=plan,
        tech=tech,
        max_slots=max_slots,
        slots=slots,
        fill=fill,
        next_wake=next_wake,
        state="once",
    )
    status_text = render_status(
        planet_id=planet_id,
        planet_name=planet_name,
        planet_coordinates=planet_coordinates,
        plan=plan,
        tech=tech,
        max_slots=max_slots,
        slots=slots,
        fill=fill,
        next_wake=next_wake,
    )
    if args.json:
        print(
            render_json(
                planet_id=planet_id,
                planet_name=planet_name,
                planet_coordinates=planet_coordinates,
                plan=plan,
                tech=tech,
                max_slots=max_slots,
                slots=slots,
                fill=fill,
                next_wake=next_wake,
            )
        )
    else:
        print(status_text)
    if _fill_failed(fill):
        report_error(notifier, scope="单轮", reason=f"{fill.status}  {fill.reason}")
    else:
        report_stop(
            notifier,
            mode="单轮",
            reason=f"{fill.status}  {fill.reason}",
            timetable_path=timetable.path,
        )
    return 1 if _fill_failed(fill) else 0


def run_loop(
    client: Any,
    *,
    plan: ExpeditionPlan,
    timetable: ExpeditionTimetable,
    clock: GameClock,
    planet_id: Optional[int],
    planet_name: str,
    planet_coordinates: Optional[Coordinates],
    args: argparse.Namespace,
    notifier: Optional[FeishuNotifier] = None,
) -> int:
    stop = {"requested": False}
    stop_file = Path(args.stop_file)

    def handle_signal(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    report_start(
        notifier,
        mode="常驻",
        planet_id=planet_id,
        planet_name=planet_name,
        planet_coordinates=planet_coordinates,
        plan=plan,
    )

    failures = 0
    stop_reason = ""
    last_blocker = ""
    while not stop.get("requested"):
        if stop_file.exists():
            print(
                f"[{_stamp()}] 发现急停文件 {stop_file}，退出",
                file=sys.stderr if args.json else sys.stdout,
                flush=True,
            )
            stop_reason = f"发现急停文件 {stop_file}"
            break
        try:
            tech, max_slots, slots, fill, next_wake, clock = run_tick(
                client,
                plan=plan,
                timetable=timetable,
                clock=clock,
                planet_id=planet_id,
                args=args,
                stop_check=lambda: stop.get("requested") or stop_file.exists(),
            )
            failures = 0
            timetable.save()
            journal_tick(
                args.journal,
                planet_id=planet_id,
                tech=tech,
                max_slots=max_slots,
                slots=slots,
                fill=fill,
                next_wake=next_wake,
            )
            publish_status(
                args.status_file,
                mode="execute",
                planet_id=planet_id,
                planet_name=planet_name,
                plan=plan,
                tech=tech,
                max_slots=max_slots,
                slots=slots,
                fill=fill,
                next_wake=next_wake,
                state="running",
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "at": iso_local(time.time()),
                            "tech": tech,
                            "max_slots": max_slots,
                            "active": len(slots),
                            "fill": fill.as_dict(),
                            "next_wake_at": iso_local(next_wake),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            tick_lines = [
                f"[{_stamp()}] 第 {len(timetable.slots)}/{max_slots} 条航道；{fill.reason}"
            ]
            for index, slot in enumerate(fill.sent, 1):
                tick_lines.append(
                    f"  + 第 {index} 队 → {slot.target}；返航 "
                    f"{iso_local(slot.expected_return_at) or '未知'}"
                )
            tick_lines.append(
                f"  下次唤醒 {iso_local(next_wake)}；时间表 {timetable.path}"
            )
            if not args.json:
                for line in tick_lines:
                    print(line, flush=True)
            # One error per distinct blocker; a persistent ship shortage must
            # not repeat the same message every retry.
            blocker = _blocker_reason(fill)
            if blocker and blocker != last_blocker:
                report_error(notifier, scope="常驻", reason=blocker)
            last_blocker = blocker
            def heartbeat(
                _tech=tech,
                _max_slots=max_slots,
                _slots=slots,
                _fill=fill,
                _next_wake=next_wake,
            ) -> None:
                publish_status(
                    args.status_file,
                    mode="execute",
                    planet_id=planet_id,
                    planet_name=planet_name,
                    plan=plan,
                    tech=_tech,
                    max_slots=_max_slots,
                    slots=_slots,
                    fill=_fill,
                    next_wake=_next_wake,
                    state="sleeping",
                )

            sleep_until(next_wake, stop, stop_file, heartbeat=heartbeat)
        except (SessionExpired, AuthenticationError) as exc:
            # Human action is required: retrying with the same cookie can
            # never recover, so say so once instead of backing off forever.
            print(
                f"[{_stamp()}] 会话不可用：{type(exc).__name__}: {exc}",
                file=sys.stderr if args.json else sys.stdout,
                flush=True,
            )
            report_error(
                notifier,
                scope="常驻",
                reason=f"会话不可用：{type(exc).__name__}: {exc}",
            )
            print(SESSION_LOST_HINT, file=sys.stderr, flush=True)
            try:
                append_jsonl(
                    Path(args.journal),
                    {
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "kind": "session_error",
                        "source": "expedition_farm",
                        "planet": planet_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    max_bytes=10 * 1024 * 1024,
                )
            except OSError:
                pass
            publish_status(
                args.status_file,
                mode="execute",
                planet_id=planet_id,
                planet_name=planet_name,
                plan=plan,
                tech=int(timetable.tech_level),
                max_slots=int(timetable.max_slots),
                slots=timetable.slots,
                fill=None,
                next_wake=None,
                state="error",
            )
            return EXIT_SESSION_EXPIRED
        except NewStarError as exc:
            failures += 1
            delay = min(900.0, max(5.0, float(args.retry_delay) * (2 ** min(failures, 6))))
            print(
                f"[{_stamp()}] 请求失败（第 {failures} 次）：{type(exc).__name__}: {exc}；"
                f"{delay:.0f}s 后重试",
                file=sys.stderr if args.json else sys.stdout,
                flush=True,
            )
            report_error(
                notifier,
                scope="常驻",
                reason=(
                    f"请求失败（第 {failures} 次）：{type(exc).__name__}: {exc}；"
                    f"{delay:.0f}s 后重试"
                ),
            )
            try:
                append_jsonl(
                    Path(args.journal),
                    {
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "kind": "expedition_error",
                        "source": "expedition_farm",
                        "planet": planet_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "retry_in": delay,
                    },
                    max_bytes=10 * 1024 * 1024,
                )
            except OSError:
                pass
            publish_status(
                args.status_file,
                mode="execute",
                planet_id=planet_id,
                planet_name=planet_name,
                plan=plan,
                tech=int(timetable.tech_level),
                max_slots=int(timetable.max_slots),
                slots=timetable.slots,
                fill=None,
                next_wake=time.time() + delay,
                state="error",
            )
            sleep_until(time.time() + delay, stop, stop_file)
        except KeyboardInterrupt:
            stop_reason = "收到键盘中断"
            break
        finally:
            timetable.save()
    timetable.save()
    publish_status(
        args.status_file,
        mode="execute",
        planet_id=planet_id,
        planet_name=planet_name,
        plan=plan,
        tech=int(timetable.tech_level),
        max_slots=int(timetable.max_slots),
        slots=timetable.slots,
        fill=None,
        next_wake=None,
        state="stopped",
    )
    if not stop_reason:
        if stop_file.exists():
            stop_reason = f"发现急停文件 {stop_file}"
        elif stop.get("requested"):
            stop_reason = "收到停止信号"
        else:
            stop_reason = "已停止"
    print(
        f"[{_stamp()}] 已停止（{stop_reason}）；时间表已保存到 {timetable.path}",
        file=sys.stderr if args.json else sys.stdout,
        flush=True,
    )
    report_stop(
        notifier,
        mode="常驻",
        reason=stop_reason,
        timetable_path=timetable.path,
    )
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        ships = parse_composition(args.ships)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    problem = _check_args(args, ships)
    if problem is not None:
        return problem
    if args.execute and Path(args.stop_file).exists():
        print(f"{args.stop_file} exists; refusing to dispatch", file=sys.stderr)
        return 0

    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    notifier = _make_notifier(args)

    lock_fd = None
    if args.execute:
        try:
            lock_fd = acquire_lock(Path(args.lock_file))
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return EXIT_BUSY

    client = None
    try:
        client = make_client(config)
        overview = client.get_overview()
        timetable = ExpeditionTimetable.load(args.timetable)
        if timetable.load_error:
            print(f"时间表读取失败（将重建）：{timetable.load_error}", file=sys.stderr)
        saved_id = timetable.planet_id if args.planet is None else None
        try:
            planet = resolve_planet(overview, args.planet, saved_id)
        except ValueError as exc:
            print(f"参数错误：{exc}", file=sys.stderr)
            return 2
        planet_id, planet_name, planet_coordinates = _planet_fields(planet)
        current_id, _, _ = _planet_fields(getattr(overview, "planet", None))
        selected = None
        if planet_id is not None and current_id != planet_id:
            selected = client.select_planet(int(planet_id))
            planet_id, planet_name, planet_coordinates = _planet_fields(selected)
        if planet_coordinates is None and selected is None:
            planet_coordinates = _planet_fields(getattr(overview, "planet", None))[2]

        try:
            target = resolve_target(planet_coordinates, args.target)
        except ValueError as exc:
            print(f"参数错误：{exc}", file=sys.stderr)
            return 2
        plan = ExpeditionPlan(
            target=target,
            ships=ships,
            speed=int(args.speed),
            stay_time=int(args.stay_time),
        )
        clock = GameClock.from_overview(overview)
        if getattr(overview, "server_time", None) is None:
            print(
                "警告：概览页没有 server_time，游戏时间将按主机时区解释；"
                "如主机不是游戏时区，返航判断会偏移",
                file=sys.stderr,
            )

        if timetable.planet_id is not None and timetable.planet_id != planet_id:
            print(
                f"时间表属于星球 {timetable.planet_id}，现切换到 {planet_id}；"
                "清空旧的航道记录",
                file=sys.stderr,
            )
            timetable.slots = []
            timetable.history = []
            timetable.max_slots = 0
            timetable.tech_level = 0
            timetable.next_check_at = 0.0
        timetable.planet_id = planet_id
        timetable.planet_label = planet_name
        timetable.planet_coordinates = (
            str(planet_coordinates) if planet_coordinates is not None else ""
        )
        timetable.target = plan.target
        timetable.ships = dict(plan.ships)
        timetable.speed = plan.speed
        timetable.stay_time = plan.stay_time

        if not args.json:
            print(
                f"[{_stamp()}] 星球 {planet_name} {planet_coordinates or '-'}；"
                f"编队 {format_ships(plan.ships)}；目标 {plan.target}",
                flush=True,
            )
        if not args.execute:
            return run_preview(
                client,
                plan=plan,
                clock=clock,
                planet_id=planet_id,
                planet_name=planet_name,
                planet_coordinates=planet_coordinates,
                args=args,
                notifier=notifier,
            )
        if args.once:
            return run_once(
                client,
                plan=plan,
                timetable=timetable,
                clock=clock,
                planet_id=planet_id,
                planet_name=planet_name,
                planet_coordinates=planet_coordinates,
                args=args,
                notifier=notifier,
            )
        return run_loop(
            client,
            plan=plan,
            timetable=timetable,
            clock=clock,
            planet_id=planet_id,
            planet_name=planet_name,
            planet_coordinates=planet_coordinates,
            args=args,
            notifier=notifier,
        )
    except NewStarError as exc:
        # Startup or a mode that does not handle its own errors (preview,
        # once): report before the traceback so the group still learns why
        # the farm is not running.
        report_error(
            notifier,
            scope="运行",
            reason=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        if notifier is not None:
            notifier.close()
        if client is not None:
            client.close()
        if lock_fd is not None:
            release_lock(lock_fd, Path(args.lock_file))


if __name__ == "__main__":
    sys.exit(main())
