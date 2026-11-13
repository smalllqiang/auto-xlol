"""Command line interface for the New-Star SDK.

The CLI is intentionally a thin layer over :class:`newstar_sdk.NewStarClient`;
anything it can do can also be done from Python.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .client import NewStarClient
from .constants import (
    MISSION_NAMES_ZH,
    RESOURCE_NAMES_ZH,
)
from .exceptions import NewStarError
from .models import (
    Coordinates,
    FleetDispatch,
    QueueEntry,
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newstar",
        description="Python SDK CLI for New-Star / 2Moons browser games",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NEWSTAR_BASE_URL"),
        help="game root URL, e.g. https://ol.lstyxl.com/ (env NEWSTAR_BASE_URL)",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("NEWSTAR_USERNAME"),
        help="game account (env NEWSTAR_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("NEWSTAR_PASSWORD"),
        help="game password (env NEWSTAR_PASSWORD; prompt when omitted)",
    )
    parser.add_argument(
        "--universe",
        default=os.getenv("NEWSTAR_UNIVERSE"),
        help="universe id or visible name, e.g. CQBX8573 (env NEWSTAR_UNIVERSE)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("NEWSTAR_TIMEOUT", "20")),
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=float(os.getenv("NEWSTAR_REQUEST_INTERVAL", "0.25")),
        help="minimum seconds between two requests",
    )
    parser.add_argument(
        "--verify-ssl",
        dest="verify_ssl",
        action="store_true",
        default=_env_bool("NEWSTAR_VERIFY_SSL", True),
        help="verify TLS certificates (env NEWSTAR_VERIFY_SSL=1)",
    )
    parser.add_argument(
        "--no-verify-ssl",
        dest="verify_ssl",
        action="store_false",
        help="disable TLS verification (needed for instances with expired certificates)",
    )
    parser.add_argument(
        "--session-file",
        default=os.getenv("NEWSTAR_SESSION_FILE"),
        help="JSON cookie file used by save-session/load-session",
    )
    parser.add_argument("--json", action="store_true", help="machine readable JSON output")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="increase log level")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("overview", help="show account, resources, planet and activity cards")

    p = sub.add_parser("buildings", help="list buildings")
    p.set_defaults(func=_cmd_buildings)

    p = sub.add_parser("build", help="upgrade a building")
    p.add_argument("element", help="building id or name (e.g. 1 or 合金冶炼厂)")
    p.add_argument("--levels", type=int, default=1, help="number of levels to queue")
    p.add_argument("--target-level", type=int, default=None, help="absolute target level")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("destroy", help="destroy one building level (destructive)")
    p.add_argument("element", help="building id or name")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_destroy)

    p = sub.add_parser("research", help="list technologies, or start researching one")
    p.add_argument("tech", nargs="?", help="technology id or name")
    p.add_argument("--levels", type=int, default=1, help="number of levels to queue")
    p.add_argument("--target-level", type=int, default=None, help="absolute target level")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_research)

    p = sub.add_parser("ships", help="list ships (and defences with --defense)")
    p.add_argument("--defense", action="store_true", help="list defences instead of ships")
    p.set_defaults(func=_cmd_ships)

    p = sub.add_parser("build-ship", help="build ships or defences")
    p.add_argument("element", help="ship/defence id or name")
    p.add_argument("count", type=int, help="amount to build")
    p.add_argument("--defense", action="store_true", help="build defences instead of ships")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_build_ship)

    p = sub.add_parser("queue", help="show a construction/research/shipyard queue")
    p.add_argument("kind", choices=["build", "research", "ship"], help="queue kind")
    p.add_argument("--defense", action="store_true", help="use the defence shipyard")
    p.set_defaults(func=_cmd_queue)

    p = sub.add_parser("cancel", help="cancel a queue item")
    p.add_argument("kind", choices=["build", "research", "ship"], help="queue kind")
    p.add_argument("position", nargs="?", type=int, default=0, help="0-based queue position")
    p.add_argument("--defense", action="store_true", help="use the defence shipyard")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_cancel)

    p = sub.add_parser("galaxy", help="show a galaxy system")
    p.add_argument("--galaxy", type=int, default=None)
    p.add_argument("--system", type=int, default=None)
    p.set_defaults(func=_cmd_galaxy)

    p = sub.add_parser("fleets", help="list your flying fleets")
    p.set_defaults(func=_cmd_fleets)

    p = sub.add_parser("available-ships", help="list ships available for dispatch")
    p.set_defaults(func=_cmd_available_ships)

    p = sub.add_parser("send-fleet", help="dispatch a fleet")
    p.add_argument("--target", help="G:S:P (alternative to --galaxy/--system/--planet)")
    p.add_argument("--galaxy", type=int)
    p.add_argument("--system", type=int)
    p.add_argument("--planet", type=int)
    p.add_argument("--type", type=int, choices=[1, 2, 3], default=1, help="1 planet, 2 debris, 3 moon")
    p.add_argument(
        "--mission",
        default="3",
        help="mission id or name (3/transport, 4/deploy, 6/spy, 8/recycle, 15/expedition, ...)",
    )
    p.add_argument("--ship", action="append", default=[], metavar="ID=COUNT", help="repeatable")
    p.add_argument("--speed", type=int, default=10, help="fleet speed 1..10")
    p.add_argument("--metal", type=float, default=0)
    p.add_argument("--crystal", type=float, default=0)
    p.add_argument("--deuterium", type=float, default=0)
    p.add_argument("--staytime", default="0")
    p.add_argument("--fleet-group", type=int, default=0)
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_send_fleet)

    p = sub.add_parser("recall", help="recall a flying fleet")
    p.add_argument("fleet_id", type=int)
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=_cmd_recall)

    p = sub.add_parser("messages", help="list messages (AJAX inbox fragments)")
    p.add_argument(
        "--category",
        default="all",
        help="category id/name/alias: all, system, battle, expedition, outbox, ...",
    )
    p.add_argument("--page", type=int, default=1, help="1-based message page")
    p.add_argument("--limit", type=int, default=0, help="max messages to print (0 = all)")
    p.set_defaults(func=_cmd_messages)

    p = sub.add_parser("bonuses", help="show the bonus summary (multiplicative modifiers)")
    p.set_defaults(func=_cmd_bonuses)

    p = sub.add_parser("page", help="print raw HTML of an ingame page")
    p.add_argument("name", help="page name, e.g. messages or overview")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p.set_defaults(func=_cmd_page)

    p = sub.add_parser("raw", help="perform an arbitrary HTTP request (escape hatch)")
    p.add_argument("path", help="path relative to the game root, e.g. game.php?page=overview")
    p.add_argument("--method", default="GET")
    p.add_argument("--data", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--check-error", action="store_true", help="raise on the game's error page")
    p.add_argument("--allow-redirects", action="store_true", default=True)
    p.set_defaults(func=_cmd_raw)

    p = sub.add_parser("session-check", help="check the stored/login session")
    p.set_defaults(func=_cmd_session_check)

    p = sub.add_parser("save-session", help="login and save cookies to --session-file")
    p.add_argument("path", nargs="?", default=None)
    p.set_defaults(func=_cmd_save_session)

    sub.add_parser("version", help="print the SDK version")
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value - round(value)) < 0.05:
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def _format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "-"
    seconds = int(value)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _format_cost(cost: Dict[int, float]) -> str:
    if not cost:
        return "-"
    parts = []
    for rid, amount in sorted(cost.items()):
        name = RESOURCE_NAMES_ZH.get(rid, str(rid))
        parts.append(f"{name} {_format_number(amount)}")
    return " / ".join(parts)


def _format_coords(coords: Optional[Coordinates]) -> str:
    return str(coords) if coords else "-"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2))


def _confirm(args: argparse.Namespace, prompt: str) -> None:
    if getattr(args, "yes", False):
        return
    if not sys.stdin.isatty():
        raise SystemExit(f"{prompt}\nrefusing to run non-interactively without --yes")
    answer = input(f"{prompt} [y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise SystemExit("aborted")


def _parse_pairs(values: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"expected KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _make_client(args: argparse.Namespace) -> NewStarClient:
    if not args.base_url:
        raise SystemExit(
            "missing --base-url (or NEWSTAR_BASE_URL), e.g. https://ol.lstyxl.com/"
        )
    password = args.password
    client = NewStarClient(
        args.base_url,
        args.username,
        password,
        universe=args.universe,
        timeout=args.timeout,
        verify_ssl=args.verify_ssl,
        request_interval=args.request_interval,
    )
    if args.session_file and Path(args.session_file).exists():
        if client.load_session(args.session_file):
            return client

    if not client.username:
        raise SystemExit(
            "missing --username (or NEWSTAR_USERNAME) and no valid --session-file"
        )
    if not client.has_password():
        client.set_credentials(
            client.username, getpass.getpass(f"Password for {client.username}: ")
        )
    client.ensure_login()
    return client


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_overview(client: NewStarClient, args: argparse.Namespace) -> None:
    overview = client.get_overview()
    if args.json:
        _print_json(overview)
        return
    resources = overview.resources
    print(
        f"{overview.game_name or 'New-Star'} | universe {overview.universe or '-'} | "
        f"player {overview.username or '-'} | engine {overview.engine_version or '-'}"
    )
    planet = overview.planet
    print(
        f"Planet: {planet.name or '-'} {_format_coords(planet.coordinates)} | "
        f"fields {planet.fields_used or 0}/{planet.fields_max or 0} | "
        f"temp {planet.temp_min if planet.temp_min is not None else '-'}.."
        f"{planet.temp_max if planet.temp_max is not None else '-'}°C | "
        f"points {planet.points if planet.points is not None else '-'}"
    )
    print(
        "Resources: "
        f"metal {_format_number(resources.metal)} ({_format_number(resources.metal_per_hour)}/h), "
        f"crystal {_format_number(resources.crystal)} ({_format_number(resources.crystal_per_hour)}/h), "
        f"deuterium {_format_number(resources.deuterium)} ({_format_number(resources.deuterium_per_hour)}/h), "
        f"energy {_format_number(resources.energy)}, "
        f"dark matter {_format_number(resources.dark_matter)}"
    )
    print(
        f"Activity: research={overview.status.research or '-'} | "
        f"building={overview.status.building or '-'} | shipyard={overview.status.shipyard or '-'}"
    )
    if overview.server_time:
        print(f"Server time: {overview.server_time.isoformat(sep=' ')}")


def _cmd_buildings(client: NewStarClient, args: argparse.Namespace) -> None:
    items = client.get_buildings()
    if args.json:
        _print_json(items)
        return
    print(f"{'ID':>4}  {'name':<24} {'lvl':>4} {'next':>4}  {'cost':<36} {'time':>7}  status")
    for item in items:
        status = "ok" if item.requirements_met else "requirements"
        if not item.requirements_met and item.requirements:
            status += ": " + "; ".join(item.requirements)
        print(
            f"{item.id:>4}  {item.name:<24} {item.level:>4} {item.next_level:>4}  "
            f"{_format_cost(item.cost):<36} {_format_seconds(item.build_seconds):>7}  {status}"
        )


def _cmd_build(client: NewStarClient, args: argparse.Namespace) -> None:
    _confirm(
        args,
        f"Upgrade building {args.element} to "
        + (f"level {args.target_level}" if args.target_level else f"+{args.levels} level(s)") + "?",
    )
    queue = client.upgrade_building(
        args.element, levels=args.levels, target_level=args.target_level
    )
    if args.json:
        _print_json(queue)
    else:
        print("Building queued.")
        _print_queue(queue)


def _cmd_destroy(client: NewStarClient, args: argparse.Namespace) -> None:
    _confirm(args, f"Destroy one level of building {args.element}? This cannot be undone.")
    queue = client.destroy_building(args.element)
    if args.json:
        _print_json(queue)
    else:
        print("Destruction queued.")
        _print_queue(queue)


def _cmd_research(client: NewStarClient, args: argparse.Namespace) -> None:
    if not args.tech:
        items = client.get_research()
        if args.json:
            _print_json(items)
            return
        print(f"{'ID':>4}  {'name':<28} {'lvl':>4} {'next':>4}  {'cost':<40} {'time':>9}  status")
        for item in items:
            status = "ok" if item.requirements_met else "requirements"
            if not item.requirements_met and item.requirements:
                status += ": " + "; ".join(item.requirements)
            print(
                f"{item.id:>4}  {item.name:<28} {item.level:>4} {item.next_level:>4}  "
                f"{_format_cost(item.cost):<40} {_format_seconds(item.research_seconds):>9}  {status}"
            )
        return
    _confirm(args, f"Start research {args.tech}?")
    queue = client.research(args.tech, levels=args.levels, target_level=args.target_level)
    if args.json:
        _print_json(queue)
    else:
        print("Research queued.")
        _print_queue(queue)


def _cmd_ships(client: NewStarClient, args: argparse.Namespace) -> None:
    mode = "defense" if args.defense else "fleet"
    items = client.get_shipyard(mode)
    if args.json:
        _print_json(items)
        return
    print(f"{'ID':>4}  {'name':<26} {'have':>6} {'max':>6}  {'cost':<38} {'time':>8}  status")
    for item in items:
        status = "ok" if item.can_build else "unavailable"
        if not item.requirements_met and item.requirements:
            status += ": " + "; ".join(item.requirements)
        print(
            f"{item.id:>4}  {item.name:<26} {item.available:>6} {item.max_buildable:>6}  "
            f"{_format_cost(item.cost):<38} {_format_seconds(item.build_seconds):>8}  {status}"
        )


def _cmd_build_ship(client: NewStarClient, args: argparse.Namespace) -> None:
    mode = "defense" if args.defense else "fleet"
    _confirm(args, f"Build {args.count} x {args.element} ({mode})?")
    queue = client.build_ships({args.element: args.count}, mode=mode)
    if args.json:
        _print_json(queue)
    else:
        print("Shipyard order queued.")
        _print_queue(queue)


def _get_queue(client: NewStarClient, kind: str, defense: bool = False) -> List[QueueEntry]:
    if kind == "build":
        return client.get_building_queue()
    if kind == "research":
        return client.get_research_queue()
    return client.get_ship_queue("defense" if defense else "fleet")


def _cmd_queue(client: NewStarClient, args: argparse.Namespace) -> None:
    queue = _get_queue(client, args.kind, args.defense)
    if args.json:
        _print_json(queue)
    else:
        _print_queue(queue)


def _cmd_cancel(client: NewStarClient, args: argparse.Namespace) -> None:
    _confirm(args, f"Cancel {args.kind} queue position {args.position}?")
    if args.kind == "build":
        queue = client.cancel_building_queue(args.position)
    elif args.kind == "research":
        queue = client.cancel_research_queue(args.position)
    else:
        queue = client.cancel_ship_queue(
            args.position, mode="defense" if args.defense else "fleet"
        )
    if args.json:
        _print_json(queue)
    else:
        print("Queue updated.")
        _print_queue(queue)


def _cmd_galaxy(client: NewStarClient, args: argparse.Namespace) -> None:
    view = client.get_galaxy(args.galaxy, args.system)
    if args.json:
        _print_json(view)
        return
    print(f"Galaxy {view.galaxy or '-'} system {view.system or '-'}")
    for slot in view.slots:
        flags = []
        if slot.is_own:
            flags.append("own")
        if slot.abandoned:
            flags.append("abandoned")
        if slot.has_moon:
            flags.append("moon")
        if slot.has_debris:
            flags.append("debris")
        actions = ", ".join(f"{mid}:{name}" for mid, name in sorted(slot.actions.items()))
        print(
            f"{slot.position:>2}  {slot.planet_name or '-':<24} {slot.player_name or '-':<18} "
            f"{'[' + str(view.galaxy) + ':' + str(view.system) + ':' + str(slot.position) + ']':<12} "
            f"{' '.join(flags):<22} {actions}"
        )


def _cmd_fleets(client: NewStarClient, args: argparse.Namespace) -> None:
    fleets = client.get_fleets()
    if args.json:
        _print_json(fleets)
        return
    if not fleets:
        print("No flying fleets.")
        return
    for fleet in fleets:
        state = "returning" if fleet.returning else "outbound"
        print(
            f"#{fleet.id} {fleet.mission} ({state}) {fleet.amount or 0} ships "
            f"{_format_coords(fleet.start_coordinates)} -> {_format_coords(fleet.target_coordinates)} "
            f"arrival={fleet.arrival_time or '-'}"
        )


def _cmd_messages(client: NewStarClient, args: argparse.Namespace) -> None:
    page = client.get_messages(args.category, page=args.page)
    if args.json:
        _print_json(page)
        return
    print(f"{page.category_name}: {len(page.messages)} message(s), page {page.page}")
    if not page.messages:
        return
    limit = args.limit if args.limit > 0 else len(page.messages)
    for message in page.messages[:limit]:
        marker = "*" if message.unread else " "
        when = message.time_text or (
            message.time.isoformat(sep=" ") if message.time else "-"
        )
        print(f"{marker} #{message.id} [{message.category_name}] {when} {message.sender}")
        body = message.body
        if len(body) > 160:
            body = body[:157] + "..."
        print(f"    {body}")
    if page.has_next_page:
        print(f"(next: --page {page.page + 1})")


def _cmd_bonuses(client: NewStarClient, args: argparse.Namespace) -> None:
    bonuses = client.get_bonuses()
    if args.json:
        _print_json(bonuses)
        return
    for item in bonuses:
        print(f"{item.name}: {item.percent:g}% (x{item.multiplier:g})")


def _cmd_available_ships(client: NewStarClient, args: argparse.Namespace) -> None:
    ships = client.get_available_ships()
    if args.json:
        _print_json(ships)
        return
    if not ships:
        print("No ships available for dispatch.")
        return
    for element_id, ship in ships.items():
        print(f"{element_id:>4}  {ship.name:<26} {ship.count:>8}")


def _cmd_send_fleet(client: NewStarClient, args: argparse.Namespace) -> None:
    if args.target:
        target = Coordinates.parse(args.target, type=args.type)
    else:
        if args.galaxy is None or args.system is None or args.planet is None:
            raise SystemExit("provide --target G:S:P or all of --galaxy/--system/--planet")
        target = Coordinates(args.galaxy, args.system, args.planet, args.type)

    ships: Dict[str, int] = {}
    for item in args.ship:
        if "=" not in item:
            raise SystemExit(f"expected --ship ID=COUNT, got {item!r}")
        key, value = item.split("=", 1)
        ships[key] = int(value)
    if not ships:
        raise SystemExit("at least one --ship ID=COUNT is required")

    mission = int(args.mission) if str(args.mission).isdigit() else args.mission
    _confirm(
        args,
        f"Send {ships} to {target} with mission {mission}?",
    )
    result = client.send_fleet(
        target,
        ships,
        mission,  # type: ignore[arg-type]
        speed=args.speed,
        resources={
            "metal": args.metal,
            "crystal": args.crystal,
            "deuterium": args.deuterium,
        },
        stay_time=args.staytime,
        fleet_group=args.fleet_group,
    )
    if args.json:
        _print_json(result)
    else:
        _print_dispatch(result)


def _cmd_recall(client: NewStarClient, args: argparse.Namespace) -> None:
    _confirm(args, f"Recall fleet {args.fleet_id}?")
    fleets = client.recall_fleet(args.fleet_id)
    if args.json:
        _print_json(fleets)
    else:
        print("Recall command sent. Remaining fleets:", len(fleets))


def _cmd_page(client: NewStarClient, args: argparse.Namespace) -> None:
    params = _parse_pairs(args.param)
    html = client.get_page(args.name, **params)
    print(html)


def _cmd_raw(client: NewStarClient, args: argparse.Namespace) -> None:
    data = _parse_pairs(args.data) or None
    params = _parse_pairs(args.param) or None
    response = client.request(
        args.method,
        args.path,
        params=params,
        data=data,
        check_error=args.check_error,
        allow_redirects=args.allow_redirects,
    )
    if args.json:
        _print_json(
            {
                "status": response.status_code,
                "url": response.url,
                "headers": dict(response.headers),
                "text": response.text,
            }
        )
    else:
        print(f"HTTP {response.status_code} {response.url}")
        print(response.text)


def _cmd_session_check(client: NewStarClient, args: argparse.Namespace) -> None:
    overview = client.get_overview()
    if args.json:
        _print_json(
            {
                "logged_in": True,
                "username": overview.username,
                "universe": overview.universe,
                "planet": overview.planet,
            }
        )
    else:
        print(
            f"logged in as {overview.username or client.username or '-'} "
            f"on {overview.universe or client.universe or '-'} "
            f"({client.base_url})"
        )


def _cmd_save_session(client: NewStarClient, args: argparse.Namespace) -> None:
    target = args.path or args.session_file
    if not target:
        raise SystemExit("provide a path or set --session-file")
    client.ensure_login()
    path = client.save_session(target)
    print(f"session saved to {path}")


# ---------------------------------------------------------------------------
# Pretty printers
# ---------------------------------------------------------------------------


def _print_queue(queue: List[QueueEntry]) -> None:
    if not queue:
        print("Queue is empty.")
        return
    for entry in queue:
        extra = []
        if entry.target_level is not None:
            extra.append(f"target level {entry.target_level}")
        if entry.amount is not None:
            extra.append(f"amount {entry.amount}")
        if entry.remaining_seconds is not None:
            extra.append(f"remaining {_format_seconds(entry.remaining_seconds)}")
        if entry.end_time:
            extra.append(f"ends {entry.end_time}")
        print(f"[{entry.index}] {entry.name} " + ", ".join(extra))


def _print_dispatch(result: FleetDispatch) -> None:
    print(
        f"Fleet sent: {result.mission_name or MISSION_NAMES_ZH.get(result.mission, result.mission)} "
        f"{_format_coords(result.from_coordinates)} -> {_format_coords(result.target_coordinates)}"
    )
    print(
        f"distance={_format_number(result.distance)} "
        f"fuel={_format_number(result.consumption)} "
        f"arrival={result.arrival_time or '-'}"
    )
    if result.ships:
        print("ships: " + ", ".join(f"{sid}x{count}" for sid, count in result.ships.items()))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"newstar-sdk {__version__}")
        return 0

    if not args.verify_ssl:
        import warnings

        from urllib3.exceptions import InsecureRequestWarning

        warnings.simplefilter("ignore", InsecureRequestWarning)

    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    # Register the remaining commands after the helper definitions.
    dispatch = {
        "overview": _cmd_overview,
        "buildings": _cmd_buildings,
        "build": _cmd_build,
        "destroy": _cmd_destroy,
        "research": _cmd_research,
        "ships": _cmd_ships,
        "build-ship": _cmd_build_ship,
        "queue": _cmd_queue,
        "cancel": _cmd_cancel,
        "galaxy": _cmd_galaxy,
        "fleets": _cmd_fleets,
        "messages": _cmd_messages,
        "bonuses": _cmd_bonuses,
        "available-ships": _cmd_available_ships,
        "send-fleet": _cmd_send_fleet,
        "recall": _cmd_recall,
        "page": _cmd_page,
        "raw": _cmd_raw,
        "session-check": _cmd_session_check,
        "save-session": _cmd_save_session,
    }

    try:
        client = _make_client(args)
        try:
            dispatch[args.command](client, args)
        finally:
            client.close()
        return 0
    except (NewStarError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
