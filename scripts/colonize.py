#!/usr/bin/env python3
"""自动殖民：按坐标范围找空位、派殖民船，按面积/温度保留或放弃。

范围（本宇宙是 99 河系 × 999 太阳系 × 15 位置）支持两种写法：

* 两个角点（不用是左上/右下）：``--range '1:2:3 4:5:6'``
  → 河系 1~4、太阳系 2~5、位置 3~6；
* 三个范围：``--range '1-4:2-5:3-6'``，含义相同。

筛选条件可选、可叠加，未指定的不检查：

    --where '面积>260' --where '最高温度<60' --where '最低温度>-50'

``--no-accept`` 是“接受 = false”：不论如何都不保留，脚本直接停止。

数量上限取**较小值**：``min(--max-accept, 游戏还能新增的殖民位)``，
其中后者已经扣掉当前占用、再加回本脚本已保留的球；``--max-accept 0`` 表示只受
游戏限制。只有本次脚本保留的球计数，范围内原本就属于自己的殖民地不算。

默认只预览（扫描 + 报告），真实发船/放弃要 ``--execute --allow 208``。
停止条件：保留数到上限、范围扫完且没有在途殖民船、没有可用殖民船、
没有重氢、急停文件、会话失效。发船用舰船 id 白名单把关，``state/STOP``
始终优先。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from expedition_farm import _planet_fields, resolve_planet
from local_config import (
    EXIT_SESSION_EXPIRED,
    SESSION_LOST_HINT,
    add_session_args,
    load_config,
    load_values,
    make_client,
)

from autoplay.colonize import (
    ColonizeRange,
    ColonizeState,
    InflightColony,
    PlanetFilter,
    PlanetRecord,
    available_colony_lanes,
    classify_system,
    effective_limit,
    game_colony_remaining,
    sort_candidates,
)
from autoplay.expedition_scheduler import (
    GameClock,
    iso_local,
    parse_game_time,
    ship_label,
)
from autoplay.journal import append_jsonl
from autoplay.runtime import EXIT_BUSY, acquire_lock, release_lock, write_status
from newstar_sdk.constants import CATEGORY_SHIP, Mission, resolve_element
from newstar_sdk.exceptions import (
    AuthenticationError,
    NewStarError,
    SessionExpired,
)
from newstar_sdk.models import (
    Coordinates,
    FleetInfo,
    FleetTable,
    Overview,
    PlanetSummary,
)
from utils.notify import (
    DEFAULT_TIMEOUT,
    ENV_SECRET,
    ENV_TIMEOUT,
    ENV_WEBHOOK,
    FeishuNotifier,
)

#: Prefix used for every Feishu report so the group can tell topics apart.
NOTIFY_TITLE = "殖民"

#: The game's colony ship (殖民舰).  ``--ship`` can point at another element.
COLONY_SHIP_ID = 208

DEFAULT_SYSTEMS_PER_TICK = 25
DEFAULT_ARRIVE_GRACE = 20.0
DEFAULT_RECONCILE_TIMEOUT = 600.0
DEFAULT_RETRY_DELAY = 60.0
DEFAULT_LANE_RETRY_DELAY = 120.0
DEFAULT_MAX_SLEEP = 900.0
DEFAULT_COLONY_CHECK_SECONDS = 300.0
DEFAULT_MAX_ERRORS = 5

#: Error text fragments that mean "this position was taken meanwhile".
_TARGET_TAKEN_HINTS = (
    "target planet",
    "fl_target_exists",
    "目标星球",
    "目标行星",
    "目标已有",
    "已被占",
    "已被殖民",
)


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


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
        help="出发星球：id、坐标（3:505:8）或列表序号；默认沿用上次的出发星球",
    )
    parser.add_argument(
        "--range",
        dest="range_spec",
        required=True,
        help="殖民范围：'1:2:3 4:5:6'（两个角点）或 '1-4:2-5:3-6'（三个范围）",
    )
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="条件",
        help=(
            "筛选条件，可重复：面积>260、最高温度<60、最低温度>-50；"
            "逗号分隔多个也可以。未指定的字段不检查"
        ),
    )
    parser.add_argument(
        "--no-accept",
        action="store_true",
        help="接受值设为 false：不论如何都不保留任何星球（脚本随即停止）",
    )
    parser.add_argument(
        "--max-accept",
        type=int,
        default=0,
        help=(
            "本次最多保留多少颗新殖民的星球（0 = 只受游戏殖民位限制）；"
            "实际上限 = min(本值, 游戏还能新增的殖民位)"
        ),
    )
    parser.add_argument(
        "--ship",
        default=str(COLONY_SHIP_ID),
        help=f"殖民船 id/名称（默认 {COLONY_SHIP_ID}=殖民舰）",
    )
    parser.add_argument(
        "--ships-per-target",
        type=int,
        default=1,
        help="每个目标派几艘殖民船（默认 1；多艘时多余的会随舰队返航）",
    )
    parser.add_argument("--speed", type=int, default=10, help="舰队速度 1..10")
    parser.add_argument(
        "--order",
        choices=("nearest", "scan"),
        default="nearest",
        help="候选排序：nearest=按离出发星球的距离优先（默认），scan=扫描顺序",
    )
    parser.add_argument(
        "--systems-per-tick",
        type=int,
        default=DEFAULT_SYSTEMS_PER_TICK,
        help=f"每轮最多扫描多少个太阳系（默认 {DEFAULT_SYSTEMS_PER_TICK}）",
    )
    parser.add_argument(
        "--arrive-grace",
        type=float,
        default=DEFAULT_ARRIVE_GRACE,
        help="预计到达后再等几秒才去检查结果（默认 20）",
    )
    parser.add_argument(
        "--reconcile-timeout",
        type=float,
        default=DEFAULT_RECONCILE_TIMEOUT,
        help="舰队消失后最多等多久出现新星球，超时判定移民失败（默认 600 秒）",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY,
        help="扫描/等待在途结果时的重试间隔（默认 60 秒）",
    )
    parser.add_argument(
        "--lane-retry-delay",
        type=float,
        default=DEFAULT_LANE_RETRY_DELAY,
        help="航道满时的复查间隔（默认 120 秒）",
    )
    parser.add_argument(
        "--max-sleep",
        type=float,
        default=DEFAULT_MAX_SLEEP,
        help="两次检查之间的最长睡眠（默认 900 秒）",
    )
    parser.add_argument(
        "--colony-check-seconds",
        type=float,
        default=DEFAULT_COLONY_CHECK_SECONDS,
        help=f"多久重读一次游戏殖民位（默认 {DEFAULT_COLONY_CHECK_SECONDS:.0f} 秒）",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=DEFAULT_MAX_ERRORS,
        help=f"连续请求失败多少次后停止（默认 {DEFAULT_MAX_ERRORS}）",
    )
    parser.add_argument(
        "--no-abandon",
        action="store_true",
        help="不达标也保留（默认直接放弃；放弃只标记，位置约 24 小时后才释放）",
    )
    parser.add_argument(
        "--no-reserve-expeditions",
        action="store_true",
        help="不预留探险队航道（默认 可用航道 = 舰队航道 - 最大探险队）",
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help="启动时清空扫描进度重新扫范围（已保留/抛弃的记录保留）",
    )
    parser.add_argument(
        "--state-file",
        default="state/colonize_state.json",
        help="扫描进度/保留记录/在途时间表（默认 state/colonize_state.json）",
    )
    parser.add_argument(
        "--status-file",
        default="state/colonize.json",
        help="心跳 JSON，便于看板监控（默认 state/colonize.json）",
    )
    parser.add_argument(
        "--journal",
        default="state/journal.jsonl",
        help="每轮追加一条记录（默认 state/journal.jsonl）",
    )
    parser.add_argument(
        "--lock-file",
        default="state/daemon.lock",
        help="单写者 pid 锁（默认与 run_forever/探险脚本共用 state/daemon.lock）",
    )
    parser.add_argument("--stop-file", default="state/STOP", help="紧急停止文件")
    parser.add_argument("--execute", action="store_true", help="真发船/真放弃（默认只预览）")
    parser.add_argument("--once", action="store_true", help="with --execute: 只跑一轮")
    parser.add_argument(
        "--notify",
        action="store_true",
        help=(
            "在开始/报错/停止时上报飞书（中间过程不报）。"
            f"需要 .env 里的 {ENV_WEBHOOK}；没配置时本次不上报"
        ),
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="ID",
        help="可重复的舰船白名单；--execute 时殖民船必须在里面",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    add_session_args(parser)
    return parser.parse_args(argv)


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


def _check_args(args: argparse.Namespace, ship_id: int) -> Optional[int]:
    if args.speed < 1 or args.speed > 10:
        print("--speed 必须在 1..10", file=sys.stderr)
        return 2
    if args.ships_per_target < 1:
        print("--ships-per-target 必须至少为 1", file=sys.stderr)
        return 2
    if args.max_accept < 0:
        print("--max-accept 不能为负", file=sys.stderr)
        return 2
    if args.systems_per_tick < 1:
        print("--systems-per-tick 必须至少为 1", file=sys.stderr)
        return 2
    if args.arrive_grace < 0:
        print("--arrive-grace 不能为负", file=sys.stderr)
        return 2
    if args.reconcile_timeout <= 0:
        print("--reconcile-timeout 必须大于 0", file=sys.stderr)
        return 2
    if args.retry_delay <= 0 or args.lane_retry_delay <= 0:
        print("--retry-delay / --lane-retry-delay 必须大于 0", file=sys.stderr)
        return 2
    if args.max_sleep < 1:
        print("--max-sleep 必须至少为 1", file=sys.stderr)
        return 2
    if args.colony_check_seconds < 0:
        print("--colony-check-seconds 不能为负", file=sys.stderr)
        return 2
    if args.max_errors < 1:
        print("--max-errors 必须至少为 1", file=sys.stderr)
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
        if int(ship_id) not in allowed:
            print(
                f"殖民船 {ship_label(ship_id)}({ship_id}) 不在 --allow 里",
                file=sys.stderr,
            )
            return 2
    return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _coordinate_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        point = Coordinates.parse(str(value))
    except ValueError:
        return None
    return f"{point.galaxy}:{point.system}:{point.planet}"


def _fleet_arrival(fleet: FleetInfo, clock: GameClock) -> Optional[float]:
    """Expected arrival at the target (fleet-table column 4)."""

    return clock.epoch(parse_game_time(fleet.start_time))


def _fleet_home(fleet: FleetInfo, clock: GameClock) -> Optional[float]:
    """Expected arrival back home (fleet-table column 6)."""

    return clock.epoch(parse_game_time(fleet.target_time))


def _is_target_taken(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return any(hint.casefold() in text for hint in _TARGET_TAKEN_HINTS)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def _make_notifier(args: argparse.Namespace) -> Optional[FeishuNotifier]:
    """Build the opt-in Feishu channel, or ``None`` when it is unavailable."""

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
    """Best-effort report; a delivery problem must never stop the script."""

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


# ---------------------------------------------------------------------------
# Tick result and rendering
# ---------------------------------------------------------------------------


@dataclass
class TickResult:
    """Everything one tick wants to report."""

    stop: bool = False
    code: str = ""
    reason: str = ""
    scanned: int = 0
    sent: List[str] = field(default_factory=list)
    planned: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    lanes: int = 0
    ships: int = 0
    limit: Optional[int] = None
    kept: int = 0
    candidates: int = 0
    inflight: int = 0
    next_wake: float = 0.0


def _print(text: str, args: argparse.Namespace) -> None:
    if not args.json:
        print(text, flush=True)


def render_status(state: ColonizeState, args: argparse.Namespace, result: TickResult) -> str:
    box = state.box
    lines = [
        f"出发星球  {state.departure_name or '-'} "
        f"{state.departure_coordinates or '-'}（id {state.departure_id or '-'}）",
        f"范围      {box.as_text() if box is not None else '-'}"
        f"（{state.scanned_systems}/{state.scan_total} 太阳系"
        f"{'，已扫完' if state.scan_complete else ''}）",
        f"条件      {state.filters.as_text()}",
        f"数量上限  {_limit_text(result, state)}",
        f"航道      可用 {result.lanes}；在途殖民 {result.inflight}",
        f"殖民船    {ship_label(_ship_id(args))} × {result.ships}（在出发星球）",
        f"候选      {result.candidates}",
        f"保留      {state.kept_count}"
        + ("：" + "、".join(item.coordinates for item in state.kept[-5:]) if state.kept else ""),
        f"已抛弃    {len(state.discarded)}；失败 {len(state.failed)}",
    ]
    if state.inflight:
        arrivals = [
            f"{item.target}@{iso_local(item.arrive_at) or '?'}" for item in state.inflight
        ]
        lines.append("在途      " + "、".join(arrivals))
    if result.events:
        lines.append("本轮:")
        lines.extend(f"  {item}" for item in result.events)
    if result.planned:
        lines.append("计划派船  " + "、".join(result.planned))
    lines.append(f"结果      {result.code or 'running'}  {result.reason}")
    return "\n".join(lines)


def _limit_text(result: TickResult, state: ColonizeState) -> str:
    game = game_colony_remaining(state.colony_used, state.colony_max)
    parts = [
        f"用户 {state.user_limit or '不限'}",
        "游戏剩余 " + ("?" if game is None else str(game)),
        "生效 " + ("不限" if result.limit is None else str(result.limit)),
    ]
    return " / ".join(parts)


def render_json(state: ColonizeState, result: TickResult) -> str:
    payload = {
        "tick": {
            "stop": result.stop,
            "code": result.code,
            "reason": result.reason,
            "scanned": result.scanned,
            "planned": list(result.planned),
            "lanes": result.lanes,
            "ships": result.ships,
            "limit": result.limit,
            "kept": result.kept,
            "candidates": result.candidates,
            "inflight": result.inflight,
            "events": list(result.events),
            "next_wake_at": result.next_wake,
            "next_wake_local": iso_local(result.next_wake),
        },
        "state": state.to_dict(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_start(state: ColonizeState, args: argparse.Namespace) -> str:
    """The one-off start report (no requests; tick data is not known yet)."""

    box = state.box
    return "\n".join(
        [
            f"开始：范围 {box.as_text() if box is not None else '-'}"
            f"（{box.system_count if box is not None else 0} 太阳系）",
            f"条件：{state.filters.as_text()}；用户上限 {state.user_limit or '不限'}",
            f"出发星球：{state.departure_name or '-'} "
            f"{state.departure_coordinates or '-'}（id {state.departure_id or '-'}）",
            f"历史：保留 {state.kept_count}；抛弃 {len(state.discarded)}；"
            f"失败 {len(state.failed)}；"
            f"扫描 {state.scanned_systems}/{state.scan_total}；在途 {len(state.inflight)}",
            f"殖民船 {args.ship}；通知只在开始/报错/停止时发送",
        ]
    )


# ---------------------------------------------------------------------------
# Tick pieces
# ---------------------------------------------------------------------------


def _ship_id(args: argparse.Namespace) -> int:
    return resolve_element(args.ship, CATEGORY_SHIP)


def ensure_departure(
    client: Any, state: ColonizeState, args: argparse.Namespace
) -> Overview:
    """Read the overview and make the departure planet the active one."""

    overview = client.get_overview()
    previous = state.departure_id
    if args.planet is None and previous is not None and not any(
        item.id == previous for item in overview.planets
    ):
        print(
            f"警告：上次的出发星球 {previous} 不在星球列表里，重新选择",
            file=sys.stderr,
        )
    planet = resolve_planet(overview, args.planet, previous if args.planet is None else None)
    planet_id, name, coordinates = _planet_fields(planet)
    if planet_id is None:
        raise ValueError("出发星球没有 id，无法作为舰队起点")
    if client.planet_id != planet_id:
        client.select_planet(planet_id)
        overview = client.last_overview or client.get_overview()
        planet_id, name, coordinates = _planet_fields(overview.planet)
    state.departure_id = planet_id
    state.departure_name = name
    state.departure_coordinates = str(coordinates) if coordinates is not None else ""
    return overview


def refresh_colony_slots(
    client: Any,
    state: ColonizeState,
    args: argparse.Namespace,
    now: float,
    *,
    force: bool = False,
) -> None:
    """Refresh the game's colony-slot counter (``page=control``)."""

    if not force and state.colony_checked_at is not None:
        if now - float(state.colony_checked_at) < float(args.colony_check_seconds):
            return
    report = client.get_control()
    if report.colony_slots:
        state.colony_used = int(report.colony_slots[0])
        state.colony_max = int(report.colony_slots[1])
    state.colony_checked_at = now


def sync_inflight(
    state: ColonizeState,
    fleets: Sequence[FleetInfo],
    clock: GameClock,
    now: float,
) -> None:
    """Align the timetable with the fleet table.

    Outbound colony fleets are authoritative; a fleet that disappeared means
    the arrival was processed (or the user recalled it), so the result can be
    checked after ``--reconcile-timeout``.
    """

    fresh: Dict[str, FleetInfo] = {}
    for fleet in fleets:
        if fleet.mission_id != int(Mission.COLONIZE) or fleet.returning:
            continue
        key = _coordinate_key(fleet.target_coordinates)
        if key:
            fresh[key] = fleet

    for entry in state.inflight:
        fleet = fresh.get(entry.target)
        if fleet is not None:
            entry.fleet_id = fleet.id or entry.fleet_id
            entry.arrive_at = _fleet_arrival(fleet, clock) or entry.arrive_at
            entry.home_at = _fleet_home(fleet, clock) or entry.home_at
            entry.missing_since = None
        elif entry.missing_since is None:
            entry.missing_since = now

    known = {entry.target for entry in state.inflight}
    for target, fleet in fresh.items():
        if target in known:
            continue
        state.record_send(
            InflightColony(
                target=target,
                fleet_id=fleet.id,
                arrive_at=_fleet_arrival(fleet, clock),
                home_at=_fleet_home(fleet, clock),
                missing_since=None,
                managed=False,
            )
        )


def _return_to_departure(client: Any, state: ColonizeState) -> None:
    """Leave the client on the departure planet after a planet-level action."""

    if state.departure_id is None:
        return
    if client.planet_id != int(state.departure_id):
        client.select_planet(int(state.departure_id))


def evaluate_colony(
    client: Any,
    state: ColonizeState,
    planet: PlanetSummary,
    args: argparse.Namespace,
    now: float,
    *,
    dry_run: bool,
) -> str:
    """Read a newly colonized planet, keep it or abandon it."""

    coordinates = _coordinate_key(planet.coordinates) or ""
    if dry_run:
        return f"待检查 {coordinates}（预览不读取、不放弃）"
    info = client.select_planet(int(planet.id))
    area = info.fields_max
    temp_max = info.temp_max
    temp_min = info.temp_min
    ok, reason = state.filters.evaluate(
        area=area, temp_max=temp_max, temp_min=temp_min
    )
    record = PlanetRecord(
        coordinates=coordinates,
        planet_id=int(planet.id),
        area=area,
        temp_max=temp_max,
        temp_min=temp_min,
        at=now,
        note=reason,
    )
    if ok:
        state.record_keep(record)
        return f"保留 {coordinates}：面积 {area}，温度 {temp_min}~{temp_max}"
    if getattr(args, "no_abandon", False):
        record.note = f"{reason}；--no-abandon，保留未放弃"
        state.record_discard(record)
        return f"不达标但保留 {coordinates}：{reason}（占用殖民位）"
    try:
        client.abandon_planet(int(planet.id))
    except NewStarError as exc:
        record.note = f"{reason}；放弃失败：{type(exc).__name__}: {exc}"
        state.record_discard(record)
        _return_to_departure(client, state)
        return f"不达标但放弃失败 {coordinates}：{exc}"
    state.record_discard(record)
    _return_to_departure(client, state)
    return f"抛弃 {coordinates}：{reason}"


def reconcile(
    client: Any,
    state: ColonizeState,
    overview: Overview,
    args: argparse.Namespace,
    now: float,
    *,
    dry_run: bool,
) -> List[str]:
    """Resolve in-flight colony fleets whose target now exists (or failed)."""

    planets = {
        _coordinate_key(item.coordinates): item
        for item in overview.planets
        if item.coordinates is not None and item.id is not None
    }
    events: List[str] = []
    for entry in list(state.inflight):
        if not entry.managed:
            # Not sent by this run: keep it out of "explored" while it flies,
            # but let the owner decide what to do with the new colony.
            waited = (
                now - float(entry.missing_since)
                if entry.missing_since is not None
                else 0.0
            )
            if entry.missing_since is not None and waited >= float(args.arrive_grace):
                state.drop_inflight(entry.target)
            continue
        planet = planets.get(entry.target)
        if planet is not None:
            events.append(
                evaluate_colony(client, state, planet, args, now, dry_run=dry_run)
            )
            continue
        if entry.missing_since is None:
            entry.missing_since = now
            continue
        waited = now - float(entry.missing_since)
        if waited >= float(args.reconcile_timeout):
            state.record_failure(
                PlanetRecord(
                    coordinates=entry.target,
                    at=now,
                    note=f"殖民未成功：舰队已消失 {waited:.0f}s 仍无新星球",
                )
            )
            events.append(f"失败 {entry.target}：未见新星球（目标可能已被占）")
    return events


def recall_inflight(
    client: Any,
    state: ColonizeState,
    args: argparse.Namespace,
    *,
    dry_run: bool,
) -> List[str]:
    """Send every in-flight colony fleet back home (cap reached)."""

    events: List[str] = []
    for entry in list(state.inflight):
        if not entry.managed:
            continue
        if entry.fleet_id is None:
            events.append(f"{entry.target} 没有舰队 id，无法撤回")
            continue
        if dry_run:
            events.append(f"将撤回 {entry.target}（舰队 {entry.fleet_id}）")
            continue
        try:
            client.recall_fleet(int(entry.fleet_id))
        except NewStarError as exc:
            events.append(
                f"撤回 {entry.target} 失败：{type(exc).__name__}: {exc}"
            )
            continue
        state.drop_inflight(entry.target)
        events.append(f"已撤回 {entry.target}（舰队 {entry.fleet_id}）")
    return events


def scan_systems(
    client: Any,
    state: ColonizeState,
    overview: Overview,
    args: argparse.Namespace,
    *,
    budget: int,
    wanted: int,
    stop_file: Optional[Path] = None,
) -> int:
    """Read galaxy pages from the scan cursor, queueing free positions."""

    if state.box is None:
        raise ValueError("没有殖民范围")
    own = [
        _coordinate_key(item.coordinates)
        for item in overview.planets
        if item.coordinates is not None
    ]
    inflight = state.inflight_targets()
    scanned = 0
    while scanned < int(budget) and state.scan_next < state.box.system_count:
        if stop_file is not None and stop_file.exists():
            break
        galaxy, system = state.box.system_at(state.scan_next)
        view = client.get_galaxy(galaxy, system)
        state.scan_next += 1
        scanned += 1
        for decision in classify_system(
            view,
            state.box,
            own_coordinates=[item for item in own if item],
            inflight_targets=inflight,
            username=client.username,
        ):
            if decision.is_candidate:
                state.add_candidate(decision.coordinates)
        if wanted > 0 and len(state.candidates) >= wanted:
            break
    if state.scan_next >= state.box.system_count:
        state.scan_complete = True
    return scanned


def preview_tick(
    client: Any,
    state: ColonizeState,
    overview: Overview,
    table: FleetTable,
    args: argparse.Namespace,
    limit: Optional[int],
    now: float,
) -> TickResult:
    """Read-only planning pass: scan the range and show what would be sent."""

    ships = table.ships_here(_ship_id(args))
    lanes = available_colony_lanes(
        table.slots, reserve_expeditions=not args.no_reserve_expeditions
    )
    fuel = float(overview.resources.amount(903))
    result = TickResult(
        lanes=lanes,
        ships=ships,
        limit=limit,
        kept=state.kept_count,
        inflight=len(state.inflight),
        candidates=len(state.candidates),
    )
    if limit is not None and result.kept >= limit:
        result.code = "limit"
        result.reason = f"已保留 {result.kept} 颗，达到上限 {limit}"
        result.next_wake = now
        return result
    if not state.candidates and not state.scan_complete:
        result.scanned = scan_systems(
            client,
            state,
            overview,
            args,
            budget=args.systems_per_tick,
            wanted=0,
            stop_file=None,
        )
    result.candidates = len(state.candidates)
    departure_point = (
        Coordinates.parse(state.departure_coordinates)
        if state.departure_coordinates
        else None
    )
    candidates = sort_candidates(
        state.candidates, departure_point, strategy=args.order
    )
    wanted = min(lanes, ships // max(1, int(args.ships_per_target)))
    if limit is not None:
        wanted = max(0, min(wanted, limit - result.kept - result.inflight))
    result.planned = candidates[: max(0, wanted)]

    blocked: List[str] = []
    if ships < int(args.ships_per_target):
        blocked.append("出发星球殖民船不足")
    if fuel <= 0:
        blocked.append("出发星球没有重氢")
    if lanes <= 0:
        blocked.append("预留探险队后没有空闲航道")
    parts = [f"候选 {result.candidates} 个"]
    parts.append("、".join(blocked) if blocked else f"可派 {len(result.planned)} 队")
    if state.scan_complete:
        parts.append("范围已扫完")
    result.code = "preview"
    result.reason = "；".join(parts)
    result.next_wake = state.next_wake(
        now,
        arrive_grace=float(args.arrive_grace),
        retry_delay=float(args.retry_delay),
        max_sleep=float(args.max_sleep),
    )
    return result


def run_tick(
    client: Any,
    state: ColonizeState,
    args: argparse.Namespace,
    *,
    dry_run: bool,
) -> TickResult:
    """One read/decide/send pass; never raises on a normal stop."""

    now = time.time()
    overview = ensure_departure(client, state, args)
    clock = GameClock.from_overview(overview)
    if getattr(overview, "server_time", None) is None and not getattr(
        args, "_warned_clock", False
    ):
        print(
            "警告：概览页没有 server_time，游戏时间按主机时区解释；"
            "如果主机不在游戏时区，预计到达时间会偏移（不影响按舰队表对账）",
            file=sys.stderr,
        )
        args._warned_clock = True
    table: FleetTable = client.get_fleet_table()
    sync_inflight(state, table.fleets, clock, now)
    refresh_colony_slots(client, state, args, now)
    if state.colony_max is None and int(state.user_limit) <= 0:
        return TickResult(
            stop=True,
            code="no_limit",
            reason=(
                "读不到游戏殖民位上限（page=control），且没有 --max-accept；"
                "为免无限殖民，停止。请给 --max-accept 或检查 control 页解析"
            ),
            next_wake=now,
        )
    limit = effective_limit(
        state.user_limit, state.colony_used, state.colony_max, kept=state.kept_count
    )
    result = TickResult(
        lanes=available_colony_lanes(
            table.slots, reserve_expeditions=not args.no_reserve_expeditions
        ),
        ships=table.ships_here(_ship_id(args)),
        limit=limit,
        kept=state.kept_count,
        inflight=len(state.inflight),
        candidates=len(state.candidates),
    )

    # 1) Resolve arrivals (may change the kept/discarded ledger).
    ledger_before = (len(state.kept), len(state.discarded), len(state.failed))
    result.events.extend(reconcile(client, state, overview, args, now, dry_run=dry_run))
    result.kept = state.kept_count
    result.inflight = len(state.inflight)
    if (
        len(state.kept),
        len(state.discarded),
        len(state.failed),
    ) != ledger_before:
        # A new (or abandoned) colony changes the game's colony-slot counter;
        # re-read it so the remaining allowance is exact for this tick.
        refresh_colony_slots(client, state, args, now, force=True)
        limit = effective_limit(
            state.user_limit, state.colony_used, state.colony_max, kept=result.kept
        )
        result.limit = limit

    if dry_run:
        return preview_tick(client, state, overview, table, args, limit, now)

    # 2) Cap reached: stop (recalling anything still flying, as allowed).
    if limit is not None and result.kept >= limit:
        if state.inflight:
            result.events.extend(recall_inflight(client, state, args, dry_run=dry_run))
            result.inflight = len(state.inflight)
        result.stop = True
        result.code = "limit"
        result.reason = f"已保留 {result.kept} 颗，达到上限 {limit}"
        result.next_wake = now
        return result

    # 3) Nothing left to explore.
    if state.explored:
        result.stop = True
        result.code = "range_explored"
        result.reason = (
            f"范围 {state.box.as_text() if state.box else '-'} 已全部探索完，"
            f"保留 {result.kept} 颗"
        )
        result.next_wake = now
        return result

    # 4) Cannot send at all.
    if result.ships < int(args.ships_per_target):
        if state.inflight:
            result.code = "waiting_ships"
            result.reason = "出发星球没有足够的殖民船，等待在途舰队返回"
            result.next_wake = state.next_wake(
                now,
                arrive_grace=float(args.arrive_grace),
                retry_delay=float(args.retry_delay),
                max_sleep=float(args.max_sleep),
            )
            return result
        result.stop = True
        result.code = "no_ships"
        result.reason = (
            f"出发星球没有可用殖民船（{ship_label(_ship_id(args))} "
            f"{result.ships}/{args.ships_per_target}）"
        )
        result.next_wake = now
        return result

    fuel = float(overview.resources.amount(903))
    if fuel <= 0:
        if state.inflight:
            result.code = "waiting_fuel"
            result.reason = "出发星球没有重氢，等待在途舰队返回"
            result.next_wake = state.next_wake(
                now,
                arrive_grace=float(args.arrive_grace),
                retry_delay=float(args.retry_delay),
                max_sleep=float(args.max_sleep),
            )
            return result
        result.stop = True
        result.code = "no_fuel"
        result.reason = "出发星球没有重氢，无法发船"
        result.next_wake = now
        return result

    # 5) How many more colonies do we want this round?
    available_sends = result.lanes
    by_ships = result.ships // int(args.ships_per_target)
    wanted = min(available_sends, by_ships)
    if limit is not None:
        wanted = max(0, min(wanted, limit - result.kept - result.inflight))
    if wanted <= 0:
        result.code = "waiting_lanes" if result.lanes <= 0 else "waiting_arrivals"
        result.reason = (
            "预留探险队后没有空闲航道，等待舰队返航"
            if result.lanes <= 0
            else "等待在途殖民船到达后再决定"
        )
        target_time = state.next_wake(
            now,
            arrive_grace=float(args.arrive_grace),
            retry_delay=float(args.retry_delay),
            max_sleep=float(args.max_sleep),
        )
        if result.lanes <= 0:
            target_time = min(target_time, now + float(args.lane_retry_delay))
            if not getattr(args, "_warned_lanes", False):
                print(
                    "警告：预留探险队后没有空闲航道；先跑探险填满预留航道，"
                    "或加 --no-reserve-expeditions 不预留",
                    file=sys.stderr,
                    flush=True,
                )
                args._warned_lanes = True
        result.next_wake = target_time
        return result

    # 6) Make sure the client is back on the departure planet before sending.
    if state.departure_id is not None and client.planet_id != state.departure_id:
        client.select_planet(int(state.departure_id))

    departure = state.departure_coordinates
    departure_point = Coordinates.parse(departure) if departure else None
    ships_left = result.ships
    lanes_left = result.lanes
    while wanted > 0:
        if args.stop_file and Path(args.stop_file).exists():
            result.stop = True
            result.code = "stop_file"
            result.reason = f"发现急停文件 {args.stop_file}"
            break
        if not state.candidates:
            if state.scan_complete:
                break
            result.scanned += scan_systems(
                client,
                state,
                overview,
                args,
                budget=args.systems_per_tick,
                wanted=wanted,
                stop_file=Path(args.stop_file),
            )
            result.candidates = len(state.candidates)
            if not state.candidates:
                break
        if args.order == "nearest" and departure_point is not None:
            state.candidates = sort_candidates(
                state.candidates, departure_point, strategy="nearest"
            )
        target = state.pop_candidate()
        if target is None:
            break
        try:
            dispatch = client.send_fleet(
                target,
                {_ship_id(args): int(args.ships_per_target)},
                mission=Mission.COLONIZE,
                speed=int(args.speed),
            )
        except NewStarError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if _is_target_taken(exc):
                state.record_failure(
                    PlanetRecord(coordinates=target, at=now, note=f"目标已被占：{detail}")
                )
                result.events.append(f"跳过 {target}：{detail}")
                continue
            state.add_candidate(target)
            result.stop = True
            result.code = "send_failed"
            result.reason = f"派船失败：{detail}"
            break
        arrive_at = clock.epoch(parse_game_time(dispatch.start_time))
        home_at = clock.epoch(parse_game_time(dispatch.arrival_time))
        state.record_send(
            InflightColony(
                target=target,
                fleet_id=None,
                sent_at=now,
                arrive_at=arrive_at,
                home_at=home_at,
                managed=True,
            )
        )
        result.sent.append(target)
        result.events.append(
            f"派船 {target}，预计到达 {iso_local(arrive_at) or '未知'}"
        )
        ships_left -= int(args.ships_per_target)
        lanes_left -= 1
        wanted -= 1

    result.ships = max(0, ships_left)
    result.lanes = max(0, lanes_left)
    result.kept = state.kept_count
    result.inflight = len(state.inflight)
    result.candidates = len(state.candidates)
    if result.stop:
        result.next_wake = now
        return result

    # 7) Everything scanned and nothing pending -> done.
    if state.explored:
        result.stop = True
        result.code = "range_explored"
        result.reason = (
            f"范围 {state.box.as_text() if state.box else '-'} 已全部探索完，"
            f"保留 {result.kept} 颗"
        )
        result.next_wake = now
        return result

    result.code = "running"
    if result.planned:
        result.reason = f"预览：将派 {len(result.planned)} 队殖民船"
    elif result.events:
        result.reason = result.events[-1]
    else:
        result.reason = "本轮没有可派的目标"
    result.next_wake = state.next_wake(
        now,
        arrive_grace=float(args.arrive_grace),
        retry_delay=float(args.retry_delay),
        max_sleep=float(args.max_sleep),
    )
    if result.lanes <= 0:
        result.next_wake = min(result.next_wake, now + float(args.lane_retry_delay))
    return result


# ---------------------------------------------------------------------------
# Status / journal
# ---------------------------------------------------------------------------


def publish_status(
    path: str,
    *,
    mode: str,
    state: ColonizeState,
    result: Optional[TickResult],
    internal_state: str,
) -> None:
    payload: Dict[str, Any] = {
        "schema": 1,
        "pid": os.getpid(),
        "mode": mode,
        "state": internal_state,
        "departure": {
            "id": state.departure_id,
            "name": state.departure_name,
            "coordinates": state.departure_coordinates,
        },
        "range": state.box.as_dict() if state.box is not None else None,
        "filters": state.filters.as_dict(),
        "limit": {
            "user": state.user_limit,
            "game_remaining": game_colony_remaining(state.colony_used, state.colony_max),
            "effective": result.limit if result is not None else None,
        },
        "colony_slots": {
            "used": state.colony_used,
            "max": state.colony_max,
        },
        "scan": {
            "next": state.scan_next,
            "total": state.scan_total,
            "complete": state.scan_complete,
            "candidates": len(state.candidates),
        },
        "kept": [item.as_dict() for item in state.kept],
        "inflight": [item.as_dict() for item in state.inflight],
        "lanes": result.lanes if result is not None else 0,
        "ships": result.ships if result is not None else 0,
        "last_code": result.code if result is not None else None,
        "last_reason": result.reason if result is not None else None,
        "next_wake_at": iso_local(result.next_wake) if result is not None else None,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        write_status(Path(path), payload)
    except OSError:
        pass


def journal_tick(
    path: str,
    *,
    state: ColonizeState,
    result: TickResult,
    dry_run: bool,
) -> None:
    try:
        append_jsonl(
            Path(path),
            {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": "colonize",
                "source": "colonize",
                "dry_run": bool(dry_run),
                "planet": state.departure_id,
                "range": state.box.as_text() if state.box is not None else None,
                "scan_next": state.scan_next,
                "scan_total": state.scan_total,
                "scan_complete": state.scan_complete,
                "kept": state.kept_count,
                "candidates": len(state.candidates),
                "inflight": len(state.inflight),
                "lanes": result.lanes,
                "ships": result.ships,
                "limit": result.limit,
                "status": result.code,
                "reason": result.reason,
                "sent": list(result.sent),
                "planned": list(result.planned),
                "events": list(result.events),
                "next_wake_at": iso_local(result.next_wake),
            },
            max_bytes=10 * 1024 * 1024,
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_preview(
    client: Any, state: ColonizeState, args: argparse.Namespace
) -> int:
    result = run_tick(client, state, args, dry_run=True)
    if args.json:
        print(render_json(state, result))
    else:
        print("模式      预览（不会发船、不会放弃）")
        print(render_status(state, args, result))
        print(
            "提示      真实执行加 --execute --allow "
            + args.ship
            + "；先看一遍计划再动手"
        )
    return 1 if result.code in {"send_failed"} else 0


def run_once(
    client: Any, state: ColonizeState, args: argparse.Namespace, notifier: Any
) -> int:
    _report(notifier, "开始（单轮）\n" + render_start(state, args))
    result = run_tick(client, state, args, dry_run=False)
    state.save()
    journal_tick(args.journal, state=state, result=result, dry_run=False)
    publish_status(
        args.status_file,
        mode="execute",
        state=state,
        result=result,
        internal_state="once",
    )
    if args.json:
        print(render_json(state, result))
    else:
        print(render_status(state, args, result))
    _report(
        notifier,
        f"停止（单轮）：{result.code} {result.reason}\n保留 {state.kept_count} 颗；"
        f"抛弃 {len(state.discarded)}；失败 {len(state.failed)}",
    )
    return 1 if result.code in {"send_failed", "no_limit"} else 0


def sleep_until(
    target: float,
    stop: Dict[str, bool],
    stop_file: Path,
    *,
    chunk: float = 5.0,
    heartbeat: Optional[Any] = None,
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


def run_loop(
    client: Any, state: ColonizeState, args: argparse.Namespace, notifier: Any
) -> int:
    stop = {"requested": False}
    stop_file = Path(args.stop_file)

    def handle_signal(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    _report(notifier, render_start(state, args))

    failures = 0
    last_result: Optional[TickResult] = None
    stop_note = ""
    while not stop.get("requested"):
        if stop_file.exists():
            stop_note = f"发现急停文件 {stop_file}，退出"
            break
        try:
            result = run_tick(client, state, args, dry_run=False)
            last_result = result
            failures = 0
            state.save()
            journal_tick(args.journal, state=state, result=result, dry_run=False)
            publish_status(
                args.status_file,
                mode="execute",
                state=state,
                result=result,
                internal_state="running",
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "at": iso_local(time.time()),
                            "kept": result.kept,
                            "limit": result.limit,
                            "candidates": result.candidates,
                            "inflight": result.inflight,
                            "status": result.code,
                            "reason": result.reason,
                            "events": result.events,
                            "next_wake_at": iso_local(result.next_wake),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                print(
                    f"[{_stamp()}] 保留 {result.kept}"
                    f"{'/' + str(result.limit) if result.limit is not None else ''}；"
                    f"候选 {result.candidates}；在途 {result.inflight}；"
                    f"{result.reason}",
                    flush=True,
                )
                for event in result.events:
                    print(f"  {event}", flush=True)
                if result.planned:
                    print(f"  计划 {len(result.planned)} 队", flush=True)
            if result.stop:
                stop_note = result.reason
                break

            def heartbeat(_result=result) -> None:
                publish_status(
                    args.status_file,
                    mode="execute",
                    state=state,
                    result=_result,
                    internal_state="sleeping",
                )

            sleep_until(result.next_wake, stop, stop_file, heartbeat=heartbeat)
        except (SessionExpired, AuthenticationError) as exc:
            print(
                f"[{_stamp()}] 会话不可用：{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            print(SESSION_LOST_HINT, file=sys.stderr, flush=True)
            _report(notifier, f"报错：会话不可用：{type(exc).__name__}: {exc}")
            publish_status(
                args.status_file,
                mode="execute",
                state=state,
                result=None,
                internal_state="error",
            )
            return EXIT_SESSION_EXPIRED
        except NewStarError as exc:
            failures += 1
            delay = min(900.0, max(5.0, float(args.retry_delay) * (2 ** min(failures, 6))))
            print(
                f"[{_stamp()}] 请求失败（第 {failures} 次）：{type(exc).__name__}: {exc}；"
                f"{delay:.0f}s 后重试",
                file=sys.stderr,
                flush=True,
            )
            if failures >= int(args.max_errors):
                _report(
                    notifier,
                    f"报错（连续 {failures} 次请求失败）："
                    f"{type(exc).__name__}: {exc}",
                )
                publish_status(
                    args.status_file,
                    mode="execute",
                    state=state,
                    result=None,
                    internal_state="error",
                )
                return 1
            sleep_until(time.time() + delay, stop, stop_file)
        except Exception as exc:  # noqa: BLE001 - notify, then surface it
            print(
                f"[{_stamp()}] 异常：{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            _report(notifier, f"报错：{type(exc).__name__}: {exc}")
            publish_status(
                args.status_file,
                mode="execute",
                state=state,
                result=None,
                internal_state="error",
            )
            return 1
        finally:
            state.save()

    state.save()
    publish_status(
        args.status_file,
        mode="execute",
        state=state,
        result=last_result,
        internal_state="stopped",
    )
    summary = (
        f"停止：{stop_note or '收到停止信号'}\n"
        f"保留 {state.kept_count} 颗；抛弃 {len(state.discarded)}；"
        f"失败 {len(state.failed)}；扫描 {state.scanned_systems}/{state.scan_total}"
    )
    _print(f"[{_stamp()}] {summary}", args)
    _report(notifier, summary)
    if last_result is not None and last_result.code == "send_failed":
        return 1
    if last_result is not None and last_result.code == "no_limit":
        return 2
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        ship_id = resolve_element(args.ship, CATEGORY_SHIP)
    except (TypeError, ValueError) as exc:
        print(f"未知殖民船：{args.ship!r}（{exc}）", file=sys.stderr)
        return 2
    problem = _check_args(args, ship_id)
    if problem is not None:
        return problem
    try:
        box = ColonizeRange.parse(args.range_spec)
        filters = PlanetFilter.parse(args.where, accept=not args.no_accept)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    if args.execute and Path(args.stop_file).exists():
        print(f"{args.stop_file} exists; refusing to dispatch", file=sys.stderr)
        return 0

    notifier = _make_notifier(args)
    if not filters.accept:
        print(
            "--no-accept：接受值=false，任何星球都不符合条件；未启动",
            file=sys.stderr,
        )
        _report(notifier, "停止：接受值=false（--no-accept），不殖民任何星球")
        if notifier is not None:
            notifier.close()
        return 0
    config = load_config(args)
    if args.execute and not args.no_abandon and not config.password:
        print(
            "--execute 需要放弃不达标的殖民地，但缺少账号密码"
            "（--password 或 .env 的 NEWSTAR_PASSWORD）；"
            "如果只想保留不放弃，加 --no-abandon",
            file=sys.stderr,
        )
        if notifier is not None:
            notifier.close()
        return 2
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    state = ColonizeState.load(args.state_file)
    if state.load_error:
        print(f"状态文件读取失败（将重建扫描）：{state.load_error}", file=sys.stderr)
    if state.box is not None and state.box != box:
        print(
            f"范围从 {state.box.as_text()} 改成 {box.as_text()}，清空扫描进度和候选",
            file=sys.stderr,
        )
        state.reset_scan()
        state.scan_complete = False
    elif args.rescan:
        print("--rescan：清空扫描进度，重新扫描范围", file=sys.stderr)
        state.reset_scan()
        state.scan_complete = False
    if state.filters != filters:
        if state.box is not None or state.kept:
            print(
                f"提示：筛选条件从「{state.filters.as_text()}」改为"
                f"「{filters.as_text()}」；已保留的球不重新判定",
                file=sys.stderr,
            )
        state.filters = filters
    state.box = box
    state.user_limit = int(args.max_accept)
    if state.scan_total and state.scan_next > state.scan_total:
        state.scan_next = state.scan_total

    lock_fd = None
    if args.execute:
        try:
            lock_fd = acquire_lock(Path(args.lock_file))
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return EXIT_BUSY

    client = None
    try:
        if not args.json:
            print(
                f"[{_stamp()}] 殖民：范围 {box.as_text()}（{box.system_count} 太阳系 / "
                f"{box.slot_count} 个位置）；条件 {filters.as_text()}；"
                f"上限 {args.max_accept or '仅游戏限制'}",
                flush=True,
            )
        client = make_client(config)
        if not args.execute:
            return run_preview(client, state, args)
        if args.once:
            return run_once(client, state, args, notifier)
        return run_loop(client, state, args, notifier)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    except SessionExpired as exc:
        print(f"会话不可用：{exc}", file=sys.stderr)
        print(SESSION_LOST_HINT, file=sys.stderr)
        return EXIT_SESSION_EXPIRED
    except NewStarError as exc:
        print(f"请求失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        _report(notifier, f"报错：{type(exc).__name__}: {exc}")
        return 1
    finally:
        if notifier is not None:
            notifier.close()
        if client is not None:
            client.close()
        if lock_fd is not None:
            release_lock(lock_fd, Path(args.lock_file))


if __name__ == "__main__":
    sys.exit(main())
