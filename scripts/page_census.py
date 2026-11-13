#!/usr/bin/env python3
"""Capture read-only HTML snapshots from a New-Star game instance.

P0 / Step 0 of the automation project.  The SDK's parsers currently cover the
core 2Moons pages (overview, buildings, research, shipyard, galaxy, fleets).
Before extending them for the pages the SDK does not cover yet (messages,
market, production control, bonuses, ...) we need to see what *this* instance
actually renders -- it is a modded New-Star build, so upstream templates are a
hint, not a contract.

The script logs in, walks a list of read-only ``game.php?page=...`` routes and
stores the raw response bytes plus a manifest (status, size, detected markers,
error-page text).  It only issues GETs, never posts, never clicks an action
button, and does not change account state.

Usage::

    .venv/bin/python scripts/page_census.py --config passwd.txt
    .venv/bin/python scripts/page_census.py --out live_pages/2026-11-13
    .venv/bin/python scripts/page_census.py --only messages,market,resources

Output::

    <out>/000_overview.html
    <out>/001_buildings.html
    ...
    <out>/manifest.json
    <out>/index.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from local_config import Config, load_config

from newstar_sdk import NewStarClient
from newstar_sdk import __version__ as sdk_version
from newstar_sdk.exceptions import NewStarError
from newstar_sdk.parsers import parse_game_error

# ---------------------------------------------------------------------------
# Page catalogue
# ---------------------------------------------------------------------------

#: Pages that are safe to fetch with a plain GET.  Order matters: the first
#: few are the ones the strategy layer will need first.
READ_ONLY_PAGES: Tuple[str, ...] = (
    "overview",
    "buildings",
    "research",
    "shipyard",
    "fleetTable",
    "galaxy",
    "planet",
    "resources",
    "infobonus",
    "messages",
    "market",
    "trader",
    "store",
    "achievements",
    "techtree",
    "statistics",
    "records",
    "alliance",
    "race",
    "party",
    "ideologies",
    "ethics",
    "minerals",
    "arsenal",
    "conteiner",
    "createMoon",
    "battleHall",
    "battleSimulator",
    "board",
    "buddyList",
    "notes",
    "officier",
    "playerCard",
    "premium",
    "questions",
    "search",
    "settings",
    "ticket",
    "auction",
    "artifact",
    "blueprints",
    "bonus",
    "bon",
    "control",
    "details",
    "development",
    "fair",
    "findDebris",
    "information",
    "phalanx",
    "scan",
    "banList",
    "band",
    "formgovernment",
    "fleetDealer",
)

#: Routes that are only reachable through a POST form or that change state.
#: ``--all`` deliberately keeps them out of the crawl.
ACTION_PAGES = frozenset(
    {
        "buyBuild",
        "buyDefense",
        "buyFleet",
        "buyTech",
        "fleetStep1",
        "fleetStep2",
        "fleetStep3",
        "fleetMissile",
        "fleetAjax",
        "reduceFleet",
        "reduceResources",
        "logout",
        "error",
        "raport",
        "delivery",
    }
)

#: Extra captures for pages whose interesting data sits behind a query param.
#:
#: The message inbox is rendered client-side: ``page=messages`` only contains
#: the tab counters, the actual list comes from ``mode=view&messcat=..&ajax=1``.
#: Category ids are the game's own (0 espionage, 3 battle, 15 expedition, ...).
MESSAGE_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("all", "100"),
    ("system", "4"),
    ("espionage", "0"),
    ("battle", "3"),
    ("expedition", "15"),
    ("transport", "5"),
    ("game", "50"),
    ("build", "99"),
    ("outbox", "999"),
)

PAGE_VARIANTS: Tuple[Tuple[str, str, Dict[str, str]], ...] = tuple(
    (f"messages_view_{label}", "messages", {"mode": "view", "messcat": cat, "site": "1", "ajax": "1"})
    for label, cat in MESSAGE_CATEGORIES
)

#: Substrings that tell us which template family a page uses.
MARKERS: Tuple[str, ...] = (
    "DatatList",
    "build_box",
    "element_row",
    "gal_user",
    "resourceTicker",
    "fleetRow",
    "buildlist",
    "message",
    "market",
)

ERROR_URL_RE = re.compile(r"game\.php\?page=([A-Za-z0-9_]+)(?:&|&amp;|$)")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Crawl targets


@dataclass
class Capture:
    label: str
    page: str
    params: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    status: Optional[int] = None
    bytes: int = 0
    sha256: str = ""
    title: str = ""
    error: Optional[str] = None
    markers: Dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0
    saved: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _title(html: str) -> str:
    match = TITLE_RE.search(html)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200]


def _markers(html: str) -> Dict[str, int]:
    return {name: html.count(name) for name in MARKERS if html.count(name)}


def _filename(index: int, capture: Capture) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", capture.label) or capture.page
    return f"{index:03d}_{safe}.html"


def discover_pages(html: str) -> List[str]:
    """Collect ``game.php?page=`` targets from a rendered page's navigation."""

    found: List[str] = []
    seen = set()
    for match in ERROR_URL_RE.finditer(html):
        page = match.group(1)
        if page in seen or page in ACTION_PAGES:
            continue
        seen.add(page)
        found.append(page)
    return found


def build_targets(
    discovered: Sequence[str], only: Optional[Iterable[str]], everything: bool
) -> List[Tuple[str, str, Dict[str, str]]]:
    targets: List[Tuple[str, str, Dict[str, str]]] = []
    seen = set()

    def add(label: str, page: str, params: Dict[str, str]) -> None:
        key = (page, tuple(sorted(params.items())))
        if key in seen:
            return
        seen.add(key)
        targets.append((label, page, params))

    requested = list(only) if only else None
    wanted = set(requested) if requested else None

    if everything:
        base: Iterable[str] = sorted(set(READ_ONLY_PAGES) | set(discovered))
    elif requested:
        base = requested
    else:
        base = list(READ_ONLY_PAGES)

    for page in base:
        if page in ACTION_PAGES:
            continue
        if wanted is not None and page not in wanted and not any(
            variant_page == page for _, variant_page, _ in PAGE_VARIANTS
        ):
            continue
        add(page, page, {})

    if not requested or "messages" in set(requested):
        for label, page, params in PAGE_VARIANTS:
            if wanted is not None and page not in wanted:
                continue
            add(label, page, dict(params))

    if requested:
        # Preserve the caller's order for explicit --only lists.
        order = {name: i for i, name in enumerate(requested)}
        targets.sort(key=lambda item: (order.get(item[1], len(order)), item[0]))
    return targets


def run_census(client: NewStarClient, out_dir: Path, targets: Sequence[Tuple[str, str, Dict[str, str]]]) -> List[Capture]:
    captures: List[Capture] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, (label, page, params) in enumerate(targets):
        capture = Capture(label=label, page=page, params=dict(params))
        started = time.monotonic()
        try:
            response = client.request(
                "GET", "game.php", params={"page": page, **params}, check_error=False
            )
            raw = response.content
            html = _decode(raw)
            capture.url = response.url
            capture.status = response.status_code
            capture.bytes = len(raw)
            capture.sha256 = hashlib.sha256(raw).hexdigest()
            capture.title = _title(html)
            capture.error = parse_game_error(html)
            capture.markers = _markers(html)
            path = out_dir / _filename(index, capture)
            path.write_bytes(raw)
            capture.saved = path.name
        except NewStarError as exc:  # network / auth / parse problems
            capture.error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - census must keep going
            capture.error = f"{type(exc).__name__}: {exc}"
        capture.elapsed_ms = int((time.monotonic() - started) * 1000)
        captures.append(capture)

        flags = []
        if capture.error:
            flags.append(f"error={capture.error[:60]}")
        if capture.status and capture.status >= 400:
            flags.append(f"http={capture.status}")
        print(
            f"[{index + 1:02d}/{len(targets)}] {label:<22} "
            f"{capture.bytes:>7} B  {capture.elapsed_ms:>5} ms  "
            + " ".join(flags)
        )
    return captures


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def write_manifest(
    out_dir: Path,
    config: Config,
    overview: Optional[dict],
    captures: Sequence[Capture],
    discovered: Sequence[str],
) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sdk_version": sdk_version,
        "base_url": config.base_url,
        "universe": config.universe,
        "account": overview,
        "discovered_pages": list(discovered),
        "captures": [capture.__dict__ for capture in captures],
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_index(out_dir: Path, captures: Sequence[Capture]) -> Path:
    lines = [
        "# Page census",
        "",
        "Read-only GET captures of every route this instance exposes.",
        "Generated by `scripts/page_census.py`; safe to delete/regenerate.",
        "",
        "| # | page | status | bytes | title | markers | note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, capture in enumerate(captures):
        markers = ", ".join(f"{k}:{v}" for k, v in capture.markers.items()) or "-"
        note = capture.error or capture.note or ""
        title = capture.title.replace("|", "\\|")
        lines.append(
            f"| {index:03d} | `{capture.label}` | {capture.status or '-'} | "
            f"{capture.bytes} | {title} | {markers} | {note[:80]} |"
        )
    lines.append("")
    path = out_dir / "index.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to .env or passwd.txt (default: autodetect)")
    parser.add_argument("--base-url", help="override game base url")
    parser.add_argument("--username", help="override username")
    parser.add_argument("--password", help="override password")
    parser.add_argument("--universe", help="override universe name/id")
    parser.add_argument("--no-verify-ssl", action="store_true", help="skip TLS verification")
    parser.add_argument("--out", default=None, help="output directory (default: live_pages/census-<timestamp>)")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between requests (default: 0.5)")
    parser.add_argument("--only", help="comma separated page names to capture")
    parser.add_argument("--all", action="store_true", help="capture every known read-only page")
    parser.add_argument("--discover", action="store_true", help="also follow nav links found on the overview page")
    parser.add_argument("--save-session", metavar="PATH", help="persist the session cookie to PATH")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out or f"live_pages/census-{stamp}")

    only = [item.strip() for item in args.only.split(",") if item.strip()] if args.only else None

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
        overview = client.get_overview()
        print(
            f"logged in as {overview.username} @ {overview.universe} "
            f"({overview.game_name}, {overview.engine_version}); "
            f"planet {overview.planet.name} {overview.planet.coordinates}"
        )

        discovered: List[str] = []
        if args.discover or args.all:
            home_html = client.get_page("overview")
            discovered = discover_pages(home_html)
            print(f"discovered {len(discovered)} nav routes: {', '.join(discovered)}")

        targets = build_targets(discovered, only, args.all)
        if not targets:
            print("nothing to capture (check --only)", file=sys.stderr)
            return 2

        captures = run_census(client, out_dir, targets)
        meta = {
            "username": overview.username,
            "universe": overview.universe,
            "game_name": overview.game_name,
            "engine_version": overview.engine_version,
            "planet": overview.planet.name,
            "coordinates": str(overview.planet.coordinates),
            "server_time": overview.server_time.isoformat() if overview.server_time else None,
            "planets": len(overview.planets),
        }
        manifest = write_manifest(out_dir, config, meta, captures, discovered)
        index = write_index(out_dir, captures)

        ok = sum(1 for capture in captures if capture.saved and not capture.error)
        failed = [capture for capture in captures if capture.error]
        print(f"\ncaptured {ok}/{len(captures)} pages into {out_dir}")
        print(f"manifest: {manifest}")
        print(f"index:    {index}")
        if failed:
            print(f"{len(failed)} route(s) returned an error page or failed:")
            for capture in failed:
                print(f"  - {capture.label}: {capture.error}")

        if args.save_session:
            client.save_session(args.save_session)
            print(f"session saved to {args.save_session}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
