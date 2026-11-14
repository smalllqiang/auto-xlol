"""HTML parsers for New-Star game pages.

The game is a server-rendered PHP application.  Most pages contain enough
stable structure (element ids, CSS classes and the embedded ``DatatList``
object) to parse them without depending on the current language.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

from .constants import (
    BUILDINGS,
    CATEGORY_BUILDING,
    CATEGORY_DEFENSE,
    CATEGORY_SHIP,
    CATEGORY_TECHNOLOGY,
    DEFENSES,
    MISSION_NAME_TO_ID,
    MISSION_NAMES_ZH,
    RESOURCE_ELEMENT_IDS,
    SHIPS,
    TECHNOLOGIES,
    element_name,
    message_category_name,
    resolve_element,
)
from .models import (
    Bonus,
    Bonuses,
    BuildingInfo,
    Coordinates,
    FleetDispatch,
    FleetInfo,
    FleetShips,
    GalaxySlot,
    GalaxyView,
    Message,
    MessageCounter,
    MessagePage,
    MissionOption,
    Overview,
    OverviewStatus,
    PlanetInfo,
    PlanetSummary,
    ProductionReport,
    QueueEntry,
    ResearchInfo,
    ResourceProduction,
    Resources,
    ShipInfo,
)
from .utils import (
    clean_text,
    extract_datatlist,
    first_int,
    int_keys,
    parse_coordinates,
    parse_int,
    parse_number,
    strip_html,
)

_SERVER_TIME_RE = re.compile(
    r"var\s+serverTime\s*=\s*new\s+Date\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _text(node: Optional[Tag]) -> str:
    return clean_text(node.get_text(" ")) if node else ""


def _attr(node: Optional[Tag], name: str) -> str:
    return clean_text(node.get(name, "")) if node else ""


def _float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return parse_number(value)


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return first_int(str(value), default)


# ---------------------------------------------------------------------------
# Account / overview
# ---------------------------------------------------------------------------


def parse_overview(html: str) -> Overview:
    soup = _soup(html)
    raw = html or ""

    overview = Overview()
    title = clean_text(soup.title.string) if soup.title and soup.title.string else ""
    parts = [p.strip() for p in title.split(" - ") if p.strip()]
    if len(parts) >= 3:
        overview.universe = parts[-2]
        overview.game_name = parts[-1]
    elif parts:
        overview.game_name = parts[-1]

    generator = soup.find("meta", attrs={"name": "generator"})
    if generator:
        overview.engine_version = clean_text(generator.get("content", ""))
    if soup.html:
        overview.language = clean_text(soup.html.get("lang", "")) or None

    username_node = soup.select_one(".usernameow")
    if username_node:
        overview.username = _text(username_node)
    if not overview.username:
        # Some themes use a different element.
        for selector in (".username", "#username", ".playername"):
            node = soup.select_one(selector)
            if node and _text(node):
                overview.username = _text(node)
                break

    overview.server_time = parse_server_time(raw)
    overview.resources = parse_resources(raw)
    overview.planets = parse_planets(soup)
    overview.planet = parse_planet_info(soup, overview.planets)
    overview.status = parse_overview_status(soup)
    return overview


def parse_server_time(html: str) -> Optional[datetime]:
    match = _SERVER_TIME_RE.search(html or "")
    if not match:
        return None
    year, month, day, hour, minute, second = (int(x) for x in match.groups())
    try:
        return datetime(year, month + 1, day, hour, minute, second)
    except ValueError:
        return None


def parse_resources(html: str) -> Resources:
    soup = _soup(html)
    resources = Resources()

    for span in soup.select("span[id^='current_']"):
        span_id = span.get("id", "")
        rid = RESOURCE_ELEMENT_IDS.get(span_id)
        if not rid:
            continue
        real = span.get("data-real")
        amount = parse_number(real) if real is not None else parse_number(_text(span))
        resources.amounts[rid] = amount

    # The resource ticker contains the exact available / limit / production
    # values for the three main resources.
    ticker_re = re.compile(
        r"resourceTicker\s*\(\s*\{(?P<body>.*?)\}\s*,\s*true\s*\)", re.S
    )
    for match in ticker_re.finditer(html or ""):
        body = match.group("body")
        elem_match = re.search(r'valueElem\s*:\s*"([^"]+)"', body)
        if not elem_match:
            continue
        rid = RESOURCE_ELEMENT_IDS.get(elem_match.group(1))
        if not rid:
            continue
        available = re.search(r'available\s*:\s*"([^"]+)"', body)
        limit = re.search(r'limit\s*:\s*\[\s*[^,]+,\s*"([^"]+)"\s*\]', body)
        production = re.search(r"production\s*:\s*([-+0-9.eE]+)", body)
        if available:
            resources.amounts[rid] = parse_number(available.group(1))
        if limit:
            resources.capacities[rid] = parse_number(limit.group(1))
        if production:
            try:
                resources.production[rid] = float(production.group(1))
            except ValueError:
                resources.production[rid] = parse_number(production.group(1))

    # Capacity fallback for resources whose capacity is printed next to the
    # amount (e.g. energy, build points, ...).
    for block in soup.select("div.bloc_res"):
        span = block.select_one("span[id^='current_']")
        if not span:
            continue
        rid = RESOURCE_ELEMENT_IDS.get(span.get("id", ""))
        if not rid or rid in resources.capacities:
            continue
        stock = block.select_one(".stock_text")
        text = _text(stock)
        capacity = _capacity_from_text(text)
        if capacity is None:
            capacity = _capacity_from_text(_attr(block, "data-tooltip-content"))
        if capacity is not None:
            resources.capacities[rid] = capacity

    # Production fallback (older templates): parse the tooltip text of the
    # resource block, e.g. "每小时产能: 2.468".
    for block in soup.select("div[id^='res_block_']"):
        span = block.select_one("span[id^='current_']")
        if not span:
            continue
        rid = RESOURCE_ELEMENT_IDS.get(span.get("id", ""))
        if not rid or rid in resources.production:
            continue
        tooltip = _attr(block, "data-tooltip-content")
        prod = re.search(r"(?:每小时产能|production per hour|per hour)\s*:?\s*([\d.,]+)", tooltip, re.I)
        if prod:
            resources.production[rid] = parse_number(prod.group(1))

    return resources


def _capacity_from_text(text: str) -> Optional[float]:
    if not text or "/" not in text:
        return None
    plain = strip_html(text)
    match = re.search(
        r"([\d.,]+(?:\s*[KkMmBb])?)\s*/\s*([\d.,]+(?:\s*[KkMmBb])?)", plain
    )
    if not match:
        return None
    value = parse_number(match.group(2))
    return value if value > 0 else None


def parse_planets(soup: BeautifulSoup) -> List[PlanetSummary]:
    planets: List[PlanetSummary] = []
    options = soup.select("#lstPlaneta option")
    if not options:
        options = soup.select("select[name='lstPlaneta'] option")
    for option in options:
        value = option.get("value", "")
        match = re.search(r"cp=(\d+)", value)
        planet_id = int(match.group(1)) if match else 0
        label = _text(option)
        coords = parse_coordinates(label)
        name = label
        if "[" in name:
            name = name.split("[", 1)[0].strip()
        planets.append(
            PlanetSummary(
                id=planet_id,
                name=name,
                coordinates=coords,
                is_current=option.has_attr("selected"),
            )
        )
    return planets


def parse_planet_info(soup: BeautifulSoup, planets: Optional[List[PlanetSummary]] = None) -> PlanetInfo:
    info = PlanetInfo()
    current = next((p for p in (planets or []) if p.is_current), None)
    if current:
        info.id = current.id or None
        info.name = current.name
        info.coordinates = current.coordinates

    active = soup.select_one("#planet_select .active_panet") or soup.select_one(".active_panet")
    if active:
        name_node = active.select_one(".name_palnet")
        if name_node:
            info.name = _text(name_node)
        coord_node = active.select_one(".coordinates_palnet")
        if coord_node:
            info.coordinates = parse_coordinates(_text(coord_node)) or info.coordinates

    block = soup.select_one("#big_panet") or soup.select_one(".palnet_block_info")
    text = _text(block) if block else ""

    diameter = re.search(r"直径\s*([\d.,]+)\s*km", text)
    if not diameter:
        diameter = re.search(r"Diameter\s*([\d.,]+)\s*km", text, re.I)
    if diameter:
        info.diameter_km = parse_number(diameter.group(1))

    fields = re.search(r"\(\s*(\d+)\s*/\s*(\d+)\s*(?:面积|fields?)\s*\)", text, re.I)
    if not fields:
        fields = re.search(r"(\d+)\s*/\s*(\d+)\s*(?:面积|fields?)", text, re.I)
    if fields:
        info.fields_used = int(fields.group(1))
        info.fields_max = int(fields.group(2))

    temps = re.search(r"(-?\d+)\s*°C\s*(?:到|to|~|-)\s*(-?\d+)\s*°C", text, re.I)
    if temps:
        info.temp_min = int(temps.group(1))
        info.temp_max = int(temps.group(2))

    score = re.search(
        r"(\d+)\s*\(\s*(?:排名第|rank)\s*(\d+)\s*(?:总排名|of)\s*(\d+)\s*\)", text, re.I
    )
    if score:
        info.points = int(score.group(1))
        info.rank = int(score.group(2))
        info.total_rank = int(score.group(3))
    return info


def parse_overview_status(soup: BeautifulSoup) -> OverviewStatus:
    return OverviewStatus(
        research=_text(soup.select_one(".ricerche a")),
        building=_text(soup.select_one(".costruzioni a")),
        shipyard=_text(soup.select_one(".flotte a")),
    )


# ---------------------------------------------------------------------------
# Buildings / research / shipyard
# ---------------------------------------------------------------------------


def _element_name_from_box(box: Tag) -> str:
    node = box.select_one(".head a.title")
    if node:
        return _text(node)
    anchors = box.select(".head a")
    for anchor in reversed(anchors):
        name = _text(anchor)
        if name and name not in {"?", "??"}:
            return name
    return ""


def _requirements(box: Tag) -> List[str]:
    result: List[str] = []
    for anchor in box.select(".required_block a"):
        text = strip_html(anchor.get("data-tooltip-content", "")) or _text(anchor)
        if text and text not in result:
            result.append(text)
    return result


def _prices(box: Tag) -> Dict[int, float]:
    costs: Dict[int, float] = {}
    for price in box.select(".price"):
        classes = price.get("class", [])
        rid = None
        for cls in classes:
            match = re.fullmatch(r"res(\d+)", cls)
            if match:
                rid = int(match.group(1))
                break
        if rid is None:
            continue
        text_node = price.select_one(".text")
        value = parse_number(_text(text_node) if text_node else _text(price))
        costs[rid] = value
    return costs


def _form_values(box: Tag) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for field in box.select("form.build_form input[type='hidden']"):
        name = field.get("name")
        if name:
            values[name] = clean_text(field.get("value", ""))
    return values


def parse_buildings(html: str) -> List[BuildingInfo]:
    soup = _soup(html)
    datatlist = extract_datatlist(html)
    result: List[BuildingInfo] = []

    for box in soup.select("div.build_box[id^='build_']"):
        match = re.search(r"build_(\d+)", box.get("id", ""))
        if not match:
            continue
        element_id = int(match.group(1))
        data: Dict[str, Any] = datatlist.get(str(element_id), {})
        form = _form_values(box)

        level = _int(data.get("level"))
        if level is None:
            level = _int(form.get("lvlup1"), 0) or 0
        level_to_build = _int(form.get("levelToBuildInFo"), level) or level

        if level is None:
            header = _text(box.select_one(".head"))
            level = first_int(header, 0) or 0

        cost = int_keys(data.get("costResources"))
        if not cost:
            cost = _prices(box)
        destroy_cost = int_keys(data.get("destroyResources"))

        build_seconds = _float(data.get("elementTime"))
        if build_seconds is None:
            build_seconds = _parse_time_text(_text(box.select_one(".time_build_edit")))

        classes = box.get("class", [])
        result.append(
            BuildingInfo(
                id=element_id,
                name=_element_name_from_box(box) or element_name(element_id, CATEGORY_BUILDING),
                level=level,
                level_to_build=level_to_build,
                max_level=_int(data.get("maxLevel"), 255) or 255,
                factor=_float(data.get("factor")),
                cost=cost,
                cost_overflow=int_keys(data.get("costOverflow")),
                build_seconds=build_seconds,
                destroy_cost=destroy_cost,
                destroy_seconds=_float(data.get("destroyTime")),
                requirements_met="required" not in classes,
                requirements=_requirements(box),
                buyable=bool(data.get("buyable", True)),
            )
        )
    return result


def parse_research(html: str) -> List[ResearchInfo]:
    soup = _soup(html)
    datatlist = extract_datatlist(html)
    result: List[ResearchInfo] = []

    for box in soup.select("div.build_box[id^='research_']"):
        match = re.search(r"research_(\d+)", box.get("id", ""))
        if not match:
            continue
        element_id = int(match.group(1))
        data: Dict[str, Any] = datatlist.get(str(element_id), {})
        form = _form_values(box)

        level = _int(data.get("level"))
        if level is None:
            level = _int(form.get("lvlup1"), 0) or 0
        level_to_build = _int(form.get("levelToBuildInFo"), level) or level

        cost = int_keys(data.get("costRessources"))
        if not cost:
            cost = int_keys(data.get("costResources"))
        if not cost:
            cost = _prices(box)

        research_seconds = _float(data.get("elementTime"))
        if research_seconds is None:
            research_seconds = _parse_time_text(_text(box.select_one(".time_build_edit")))

        classes = box.get("class", [])
        result.append(
            ResearchInfo(
                id=element_id,
                name=_element_name_from_box(box) or element_name(element_id, CATEGORY_TECHNOLOGY),
                level=level,
                level_to_build=level_to_build,
                max_level=_int(data.get("maxLevel"), 255) or 255,
                factor=_float(data.get("factor")),
                cost=cost,
                cost_overflow=int_keys(data.get("costOverflow")),
                research_seconds=research_seconds,
                requirements_met="required" not in classes,
                requirements=_requirements(box),
                buyable=bool(data.get("buyable", True)),
            )
        )
    return result


def parse_shipyard(html: str, mode: str = "fleet") -> List[ShipInfo]:
    soup = _soup(html)
    datatlist = extract_datatlist(html)
    result: List[ShipInfo] = []
    category = CATEGORY_DEFENSE if mode == "defense" else CATEGORY_SHIP
    fallback_category = "defense" if mode == "defense" else "fleet"

    for box in soup.select("div.build_box[id^='s_']"):
        match = re.search(r"s_(\d+)", box.get("id", ""))
        if not match:
            continue
        element_id = int(match.group(1))
        data: Dict[str, Any] = datatlist.get(str(element_id), {})

        available = _int(data.get("available"))
        if available is None:
            available = _int(_text(box.select_one(f"#val_{element_id}")), 0) or 0

        cost = int_keys(data.get("costResources"))
        if not cost:
            cost = _prices(box)

        build_seconds = _float(data.get("elementTime"))
        if build_seconds is None:
            build_seconds = _parse_time_text(_text(box.select_one(".time_build_edit")))

        classes = box.get("class", [])
        result.append(
            ShipInfo(
                id=element_id,
                name=_element_name_from_box(box) or element_name(element_id, category),
                category=fallback_category,
                available=available,
                max_buildable=_int(data.get("maxBuildable"), 0) or 0,
                cost=cost,
                build_seconds=build_seconds,
                requirements_met="required" not in classes,
                requirements=_requirements(box),
            )
        )
    return result


def _parse_time_text(text: str) -> Optional[float]:
    """Parse localized "1 小时 2 分钟 3 秒" strings as seconds."""

    if not text:
        return None
    total = 0.0
    found = False
    patterns = (
        (r"(\d+)\s*(?:小时|hours?|h)", 3600.0),
        (r"(\d+)\s*(?:分钟|minutes?|min|m)", 60.0),
        (r"(\d+)\s*(?:秒|seconds?|sec|s)", 1.0),
    )
    for pattern, multiplier in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            total += int(match.group(1)) * multiplier
            found = True
    return total if found else None


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


def parse_building_queue(html: str) -> List[QueueEntry]:
    return _parse_construction_queue(html, CATEGORY_BUILDING)


def parse_research_queue(html: str) -> List[QueueEntry]:
    return _parse_construction_queue(html, CATEGORY_TECHNOLOGY)


def _parse_construction_queue(html: str, category: str) -> List[QueueEntry]:
    soup = _soup(html)
    entries: List[QueueEntry] = []
    for row in soup.select("#buildlist .element_row"):
        right = row.select_one(".right_hand")
        if not right:
            continue
        style = row.get("style", "")
        id_match = re.search(r"gebaeude/(\d+)\.gif", style)
        element_id = int(id_match.group(1)) if id_match else None
        name = _text(row.select_one(".onlistretit"))
        if not name and element_id is not None:
            name = element_name(element_id, category)

        time_node = row.select_one("#time[data-time]") or row.select_one("[data-time]")
        remaining = _int(time_node.get("data-time")) if time_node else None
        end_time = _text(row.select_one(".data_scad"))
        target = first_int(_text(row.select_one(".onlistremov")))
        active = "active_row" in row.get("class", [])

        entries.append(
            QueueEntry(
                index=len(entries),
                element_id=element_id,
                name=name,
                target_level=target,
                remaining_seconds=remaining,
                end_time=end_time or None,
                active=active,
            )
        )
    return entries


def parse_ship_queue(html: str) -> List[QueueEntry]:
    soup = _soup(html)
    entries: List[QueueEntry] = []
    for row in soup.select("#buildlist .element_row"):
        right = row.select_one(".right_hand")
        if not right:
            continue
        style = row.get("style", "")
        id_match = re.search(r"gebaeude/(\d+)\.gif", style)
        element_id = int(id_match.group(1)) if id_match else None
        name = _text(row.select_one(".onlistretit")) or _element_any_name(element_id)

        time_node = row.select_one("#time[data-time]")
        remaining = _int(time_node.get("data-time")) if time_node else None
        amount_node = row.select_one("#auftr0") or row.select_one(".onlistremov")
        amount = first_int(_text(amount_node))
        position = first_int(_text(row.select_one(".onlistrenum")))
        index = (position - 1) if position and position > 0 else len(entries)
        active = "active_row" in row.get("class", [])

        entries.append(
            QueueEntry(
                index=index,
                element_id=element_id,
                name=name,
                amount=amount,
                remaining_seconds=remaining,
                end_time=_text(row.select_one(".data_scad")) or None,
                active=active,
            )
        )
    return entries


def _element_any_name(element_id: Optional[int]) -> str:
    if element_id is None:
        return ""
    for category in (CATEGORY_BUILDING, CATEGORY_TECHNOLOGY, CATEGORY_SHIP, CATEGORY_DEFENSE):
        if element_id in {CATEGORY_BUILDING: BUILDINGS, CATEGORY_TECHNOLOGY: TECHNOLOGIES,
                          CATEGORY_SHIP: SHIPS, CATEGORY_DEFENSE: DEFENSES}[category]:
            return element_name(element_id, category)
    return f"Element {element_id}"


# ---------------------------------------------------------------------------
# Galaxy
# ---------------------------------------------------------------------------


def parse_galaxy(
    html: str,
    *,
    current_planet_id: Optional[int] = None,
    username: Optional[str] = None,
) -> GalaxyView:
    soup = _soup(html)
    view = GalaxyView()

    galaxy_input = soup.select_one("form input[name='galaxy']")
    system_input = soup.select_one("form input[name='system']")
    if galaxy_input:
        view.galaxy = _int(galaxy_input.get("value"))
    if system_input:
        view.system = _int(system_input.get("value"))

    for row in soup.select("div.gal_user"):
        number_node = row.select_one(".gal_number")
        position = first_int(_text(number_node))
        if not position:
            continue

        planet_id = None
        planet_marker = row.select_one("span[id^='p_']")
        if planet_marker:
            planet_id = first_int(planet_marker.get("id", "").replace("p_", ""))

        planet_name = _text(row.select_one(".gal_planet_name"))
        player_name = _text(row.select_one(".gal_player_name"))

        coords = None
        if view.galaxy and view.system:
            coords = Coordinates(view.galaxy, view.system, position, 1)

        actions: Dict[int, str] = {}
        for link in row.select("a[href*='target_mission=']"):
            mission_match = re.search(r"target_mission=(\d+)", link.get("href", ""))
            if not mission_match:
                continue
            mission_id = int(mission_match.group(1))
            label = strip_html(link.get("data-tooltip-content", "")) or _attr(link, "title")
            actions.setdefault(mission_id, label or MISSION_NAMES_ZH.get(mission_id, ""))

        area = temperature = None
        tooltip_link = row.select_one(".gal_number a[data-tooltip-content]")
        if tooltip_link:
            tooltip = tooltip_link.get("data-tooltip-content", "")
            area_match = re.search(r"面积\s*(?:<[^>]*>\s*)*([\d.,]+)", tooltip, re.I)
            temp_match = re.search(r"温度\s*(?:<[^>]*>\s*)*(-?[\d.,]+)", tooltip, re.I)
            if not area_match:
                area_match = re.search(r"Area\s*(?:<[^>]*>\s*)*([\d.,]+)", tooltip, re.I)
            if not temp_match:
                temp_match = re.search(r"Temperature\s*(?:<[^>]*>\s*)*(-?[\d.,]+)", tooltip, re.I)
            if area_match:
                area = parse_int(area_match.group(1))
            if temp_match:
                temperature = parse_int(temp_match.group(1))

        moon_node = row.select_one(".gal_ico_moon")
        debris_node = row.select_one(".gal_ico_trash")
        has_moon = bool(moon_node and (moon_node.select_one("a") or moon_node.select_one("img")))
        has_debris = bool(
            debris_node and (debris_node.select_one("a") or debris_node.select_one("img"))
        )

        abandoned = planet_name in {"被遗弃的殖民地", "Abandoned colony", "Abandoned Colony"}
        is_own = bool(
            (current_planet_id is not None and planet_id == current_planet_id)
            or (
                username
                and player_name
                and player_name.casefold() == username.casefold()
                and planet_name
            )
        )

        view.slots.append(
            GalaxySlot(
                position=position,
                planet_id=planet_id,
                planet_name=planet_name,
                player_name=player_name,
                coordinates=coords,
                is_own=is_own,
                has_moon=has_moon,
                has_debris=has_debris,
                abandoned=abandoned,
                area=area,
                temperature=temperature,
                actions=actions,
            )
        )
    return view


# ---------------------------------------------------------------------------
# Fleets
# ---------------------------------------------------------------------------


def parse_fleets(html: str) -> List[FleetInfo]:
    soup = _soup(html)

    # The page contains a ship-selection table and the "flying fleets" table.
    # The latter has nine direct <td> cells per row; returning fleets have no
    # recall form, so forms alone cannot be used to find the rows.
    table = None
    for candidate in soup.select("table.tablesorter.ally_ranks"):
        first_row = candidate.find("tr")
        if first_row and len(first_row.find_all("td", recursive=False)) >= 8:
            table = candidate
    if table is None:
        return []

    fleets: List[FleetInfo] = []
    rows = table.find_all("tr")[1:]
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 8:
            continue
        cell_texts = [_text(cell) for cell in cells]
        if all(text in {"", "-"} for text in cell_texts):
            continue

        action_form = row.find("form", action=lambda value: value and "sendfleetback" in value)
        fleet_id = None
        if action_form:
            fleet_id = first_int(_attr(action_form.select_one("input[name='fleetID']"), "value"))

        mission_text = cell_texts[1]
        mission_name = mission_text
        mission_id = None
        folded_mission = mission_text.casefold()
        for candidate_name, candidate_id in sorted(
            MISSION_NAME_TO_ID.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if candidate_name and candidate_name in folded_mission:
                mission_id = candidate_id
                break
        if mission_id is None:
            mission_name = re.split(r"\s+", mission_text)[0] if mission_text else ""

        returning_link = cells[1].select_one("a")
        if returning_link and mission_name:
            link_text = _text(returning_link)
            if link_text:
                mission_name = clean_text(mission_name.replace(link_text, ""))
        returning = False
        if returning_link:
            title = (returning_link.get("title") or "").casefold()
            link_text = _text(returning_link).casefold()
            returning = any(
                token in title or token in link_text
                for token in ("fl_returning", "returning", "返回", "fl_r")
            )

        ships: Dict[str, int] = {}
        amount_link = cells[2].select_one("a[data-tooltip-content]")
        if amount_link:
            tooltip_soup = _soup(amount_link.get("data-tooltip-content", ""))
            for ship_row in tooltip_soup.select("tr"):
                tds = ship_row.find_all("td")
                if len(tds) >= 2:
                    ship_name = _text(tds[-2]).rstrip(":")
                    ship_count = first_int(_text(tds[-1]))
                    if ship_name and ship_count is not None:
                        ships[ship_name] = ship_count

        fleets.append(
            FleetInfo(
                id=fleet_id,
                number=first_int(cell_texts[0]),
                mission=mission_name,
                mission_id=mission_id,
                amount=first_int(cell_texts[2]),
                start_coordinates=parse_coordinates(cell_texts[3]),
                start_time=cell_texts[4],
                target_coordinates=parse_coordinates(cell_texts[5]),
                target_time=cell_texts[6],
                arrival_time=cell_texts[7],
                returning=returning,
                can_recall=action_form is not None,
                ships=ships,
            )
        )
    return fleets


def parse_fleet_selection(html: str) -> Dict[int, FleetShips]:
    soup = _soup(html)
    ships: Dict[int, FleetShips] = {}
    for box in soup.select("div.build_box.flethan"):
        field = box.select_one("input[name^='ship']")
        element_id = None
        if field:
            match = re.search(r"ship(\d+)", field.get("name", ""))
            if match:
                element_id = int(match.group(1))
        if element_id is None:
            img = box.select_one("img")
            if img:
                match = re.search(r"gebaeude/(\d+)\.gif", img.get("src", ""))
                if match:
                    element_id = int(match.group(1))
        if element_id is None:
            continue
        count_node = soup.select_one(f"#ship{element_id}_value")
        ships[element_id] = FleetShips(
            id=element_id,
            name=_text(box.select_one(".hang3"))
            or element_name(element_id, CATEGORY_SHIP)
            or f"Element {element_id}",
            count=_int(_text(count_node), 0) or 0,
        )
    return ships


def parse_fleet_step1(html: str) -> Tuple[Optional[str], Optional[float]]:
    soup = _soup(html)
    token = _attr(soup.select_one("input[name='token']"), "value") or None
    max_speed = None
    node = soup.select_one("#maxspeed")
    if node:
        max_speed = parse_number(_text(node))
    return token, max_speed


def parse_fleet_step2(html: str) -> Tuple[Optional[str], List[MissionOption]]:
    token, missions, _consumption, _fleet_room, _stay_times = parse_fleet_step2_details(html)
    return token, missions


def parse_fleet_step2_details(
    html: str,
) -> Tuple[Optional[str], List[MissionOption], Optional[float], Optional[float], List[int]]:
    """Parse token, missions, fuel, fleet room and stay-time options."""

    soup = _soup(html)
    token = _attr(soup.select_one("input[name='token']"), "value") or None
    missions: List[MissionOption] = []
    for radio in soup.select("input[name='mission'][type='radio']"):
        value = radio.get("value", "")
        if not value.isdigit():
            continue
        mission_id = int(value)
        label = ""
        radio_id = radio.get("id", "")
        if radio_id:
            label_node = soup.select_one(f"label[for='{radio_id}']")
            if label_node:
                label = _text(label_node)
        if not label:
            parent = radio.find_parent("div")
            if parent:
                label = _text(parent).replace(value, "", 1).strip()
        missions.append(
            MissionOption(
                id=mission_id,
                name=label or MISSION_NAMES_ZH.get(mission_id, f"Mission {mission_id}"),
            )
        )

    consumption_node = soup.select_one("#consumption")
    consumption = parse_number(_text(consumption_node)) if consumption_node else None
    fleet_room_match = re.search(r'fleetroom\s*:\s*"([^"]+)"', html or "")
    fleet_room = (
        parse_number(fleet_room_match.group(1)) if fleet_room_match else None
    )
    stay_times: List[int] = []
    stay_select = soup.select_one("select[name='staytime']")
    if stay_select is not None:
        for option in stay_select.select("option"):
            value = option.get("value", "")
            if value.isdigit():
                stay_times.append(int(value))
    return token, missions, consumption, fleet_room, stay_times


def parse_fleet_dispatch_result(html: str) -> Optional[FleetDispatch]:
    soup = _soup(html)
    if not soup.select_one(".fleettab16"):
        return None

    def first_value(selector: str) -> Optional[str]:
        node = soup.select_one(selector)
        return _text(node) if node else None

    mission_name = first_value(".fleetstep1-3 .fleetstep1-10") or ""
    start_time = first_value(".fleetstep1-4 .fleetstep1-10")
    consumption = first_value(".fleetstep1-5 .fleetstep1-10")
    from_coords = None
    for node in soup.select(".fleetstep1-5"):
        value_node = node.select_one(".fleetstep1-10")
        if value_node:
            from_coords = parse_coordinates(_text(value_node))
            if from_coords:
                break
    distance = first_value(".fleetstep1-9 .fleetstep1-11")
    destination = None
    for node in soup.select(".fleetstep1-9"):
        value_node = node.select_one(".fleetstep1-11")
        if value_node:
            destination = parse_coordinates(_text(value_node))
            if destination:
                break
    arrival = first_value(".fleetstep1-7 .fleetstep1-11")

    ships: Dict[int, int] = {}
    for box in soup.select(".alleanza1"):
        img = box.select_one("img")
        element_id = None
        if img:
            match = re.search(r"gebaeude/(\d+)\.gif", img.get("src", ""))
            if match:
                element_id = int(match.group(1))
        count = first_int(_text(box.select_one(".fleettab15")))
        if element_id is not None and count is not None:
            ships[element_id] = count

    mission_id = MISSION_NAME_TO_ID.get(mission_name.casefold())
    return FleetDispatch(
        mission=mission_id or 0,
        mission_name=mission_name,
        from_coordinates=from_coords,
        target_coordinates=destination,
        distance=parse_number(distance) if distance else None,
        consumption=parse_number(consumption) if consumption else None,
        start_time=start_time,
        arrival_time=arrival,
        ships=ships,
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


_ERROR_TITLES = frozenset({"信息", "information", "info", "информация", "情報"})


def _leading_title(soup: BeautifulSoup) -> str:
    """The page heading, without the ``- universe - game`` suffix."""

    title = soup.title.get_text() if soup.title else ""
    return title.split(" - ")[0].strip().casefold()


def parse_game_error(html: str) -> Optional[str]:
    soup = _soup(html)

    # The stock error template wraps the message in a titled container:
    #   <div id="ally_content" class="conteiner">
    #     <div class="gray_stripe">信息</div>
    #     <div class="ally_contents">NFCoosen</div>
    # ``.ally_contents`` alone is not enough: several normal pages (settings,
    # notifications, ...) reuse the class inside their own cards.
    for node in soup.select("#ally_content .ally_contents"):
        header = node.find_previous(class_="gray_stripe")
        if header is not None and _text(header).strip().casefold() in _ERROR_TITLES:
            message = _text(node)
            return message or None

    # Themes without the stripe: only trust a body card when the page heading
    # is the localised "information" one.
    if _leading_title(soup) in _ERROR_TITLES:
        for selector in (".ally_contents", ".card-text"):
            node = soup.select_one(selector)
            if node:
                message = _text(node)
                return message or None
    return None


def parse_login_universes(html: str) -> List[Tuple[str, str]]:
    soup = _soup(html)
    result: List[Tuple[str, str]] = []
    for option in soup.select("select[name='uni'] option"):
        result.append((clean_text(option.get("value", "")), _text(option)))
    return result


def parse_username(html: str) -> Optional[str]:
    soup = _soup(html)
    node = soup.select_one(".usernameow")
    return _text(node) or None


# ---------------------------------------------------------------------------
# Messages and bonuses
# ---------------------------------------------------------------------------

_MESSAGE_ID_RE = re.compile(r"message_(\d+)")
_MESSAGE_CATEGORY_RE = re.compile(r"msg(?:Del|Archive)\(\s*\d+\s*,\s*(\d+)\s*\)")
_MESSAGE_PAGE_RE = re.compile(r"Message\.getMessages\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_MESSAGE_TIME_FORMATS = (
    "%Y年%m月%d日, %H:%M:%S",
    "%Y年%m月%d日 %H:%M:%S",
    "%d.%m.%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def _parse_message_time(text: str) -> Optional[datetime]:
    value = clean_text(text)
    if not value:
        return None
    for fmt in _MESSAGE_TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _message_coordinates(body: Tag) -> List[Coordinates]:
    found: List[Coordinates] = []
    seen = set()
    for anchor in body.find_all("a"):
        text = clean_text(anchor.get_text(" "))
        try:
            coords = Coordinates.parse(text)
        except ValueError:
            continue
        key = (coords.galaxy, coords.system, coords.planet)
        if key not in seen:
            seen.add(key)
            found.append(coords)
    if found:
        return found
    for match in re.finditer(r"\[\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*\]", _text(body)):
        key = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if key not in seen:
            seen.add(key)
            found.append(Coordinates(*key))
    return found


def parse_messages(html: str) -> MessagePage:
    """Parse a ``game.php?page=messages&mode=view`` fragment."""

    soup = _soup(html)
    result = MessagePage()

    for block in soup.select(".head_row_msg"):
        head = block.select_one(".message_head")
        if head is None:
            continue
        match = _MESSAGE_ID_RE.search(head.get("id", ""))
        if not match:
            continue
        message = Message(id=int(match.group(1)))

        time_node = head.select_one(".message_time")
        if time_node is not None:
            message.time_text = clean_text(time_node.get_text(" "))
            message.time = _parse_message_time(message.time_text)

        title_node = head.select_one(".message_recipient_name") or head.select_one(
            ".message_title"
        )
        if title_node is not None:
            message.sender = clean_text(title_node.get_text(" "))
        subject_node = head.select_one(".message_subject")
        if subject_node is not None:
            message.subject = clean_text(subject_node.get_text(" "))

        message.unread = "mes_unread" in (head.get("class") or [])

        category_match = _MESSAGE_CATEGORY_RE.search(str(block))
        if category_match:
            message.category = int(category_match.group(1))
            message.category_name = message_category_name(message.category)

        body = block.select_one(".message_text")
        if body is not None:
            message.body = clean_text(body.get_text(" "))
            message.coordinates = _message_coordinates(body)

        result.messages.append(message)

    nav = soup.select_one(".message_page_navigation")
    if nav is not None:
        for match in _MESSAGE_PAGE_RE.finditer(str(nav)):
            page_number = int(match.group(2))
            if page_number not in result.pages:
                result.pages.append(page_number)
        active = nav.select_one(".active_page")
        if active is not None:
            try:
                result.page = int(clean_text(active.get_text(" ")))
            except ValueError:
                pass
    if not result.pages:
        result.pages = [result.page]
    return result


def parse_message_counters(html: str) -> Dict[int, MessageCounter]:
    """Parse the per-category unread/total counters from ``page=messages``."""

    soup = _soup(html)
    counters: Dict[int, MessageCounter] = {}
    for anchor in soup.select("a[id^='mes_']"):
        raw = (anchor.get("id") or "")[4:]
        if not raw.isdigit():
            continue
        category = int(raw)
        unread_node = anchor.select_one(f"#unread_{category}")
        total_node = anchor.select_one(f"#total_{category}")
        counters[category] = MessageCounter(
            category=category,
            unread=parse_int(unread_node.get_text()) if unread_node else 0,
            total=parse_int(total_node.get_text()) if total_node else 0,
        )
    return counters


def parse_bonuses(html: str) -> Bonuses:
    """Parse the ``infobonus`` (bonus summary) table.

    Each row renders as ``icon | percent | multiplier | label``; the label is
    localised, so it is used as the key and never translated by the SDK.
    """

    soup = _soup(html)
    items: List[Bonus] = []
    for row in soup.select("tr.classificabarra"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        texts = [clean_text(cell.get_text(" ")) for cell in cells]
        label = next((text for text in reversed(texts) if text and "%" not in text), "")
        if not label:
            continue
        raw_percent = next((text for text in texts if "%" in text), "")
        percent = parse_number(raw_percent) if raw_percent else 0.0
        items.append(
            Bonus(
                name=label,
                percent=percent,
                multiplier=1.0 + percent / 100.0,
                raw_percent=raw_percent,
            )
        )
    return Bonuses(items=items)


# ---------------------------------------------------------------------------
# Production control (page=resources)
# ---------------------------------------------------------------------------

_PRODUCTION_LABELS = {
    "base": ("基础产能", "base production"),
    "bonus": ("加成", "bonus"),
    "capacity": ("仓库容量", "storage capacity"),
    "total": ("总计", "total"),
    "daily": ("每日产量", "daily production", "daily"),
    "weekly": ("每周产量", "weekly production", "weekly"),
}
_PRODUCTION_RESOURCE_RE = re.compile(r"rexx(\d{3})")
_PRODUCTION_LEVEL_RE = re.compile(r"^(?P<name>.+?)\s+(?P<level>\d+)$")
_PRODUCTION_RATIO_RE = re.compile(r"prod\[(\d+)\]")


def _production_label(text: str) -> Optional[str]:
    cleaned = clean_text(text).rstrip(":").rstrip("：").strip()
    folded = cleaned.casefold()
    # The bonus row reads "加成 (军官/加成):" -- match its prefix.
    if folded.startswith(("加成", "bonus")):
        return "bonus"
    for key, names in _PRODUCTION_LABELS.items():
        if any(folded == name.casefold() for name in names):
            return key
    return None


def parse_production(html: str) -> ProductionReport:
    """Parse the production control grid (``page=resources``).

    The page is a CSS grid, not a table: the first column holds the labels and
    every following ``.rexx1.rexx9XX`` column holds one resource's values in the
    same row order.  Alignment is therefore positional.
    """

    soup = _soup(html)
    form = soup.select_one("#ally_content form") or soup.find("form")
    report = ProductionReport()
    if form is None:
        return report

    planet = form.select_one(".rexx10")
    if planet is not None:
        report.planet = clean_text(planet.get_text(" "))

    columns = form.find_all("div", class_="rexx1", recursive=False)
    if not columns:
        return report

    labels = [
        clean_text(node.get_text(" "))
        for node in columns[0].find_all("div", class_="rexx2", recursive=False)
    ]

    for column in columns[1:]:
        match = _PRODUCTION_RESOURCE_RE.search(" ".join(column.get("class", [])))
        if not match:
            continue
        resource_id = int(match.group(1))
        values = [
            clean_text(node.get_text(" "))
            for node in column.find_all("div", class_="rexx2", recursive=False)
        ]

        production = ResourceProduction(resource_id=resource_id)
        for label, raw in zip(labels, values):
            key = _production_label(label)
            if key == "base":
                production.base = parse_number(raw)
            elif key == "bonus":
                production.bonus = parse_number(raw)
            elif key == "capacity":
                production.capacity = None if raw in {"", "-", "—"} else parse_number(raw)
            elif key == "total":
                production.total = parse_number(raw)
            elif key == "daily":
                production.daily = parse_number(raw)
            elif key == "weekly":
                production.weekly = parse_number(raw)
            else:
                level_match = _PRODUCTION_LEVEL_RE.match(label)
                if not level_match:
                    continue
                try:
                    element_id = resolve_element(level_match.group("name"), "building")
                except ValueError:
                    continue
                production.buildings[element_id] = parse_number(raw)
                report.levels[element_id] = int(level_match.group("level"))

        report.resources[resource_id] = production

    for select in form.select("select[name^='prod[']"):
        name_match = _PRODUCTION_RATIO_RE.search(select.get("name", ""))
        if not name_match:
            continue
        option = select.select_one("option[selected]") or select.find("option")
        if option is None:
            continue
        try:
            # The option value is 0..10; 10 means 100%.
            report.ratios[int(name_match.group(1))] = int(option.get("value", "0")) * 10
        except (TypeError, ValueError):
            continue

    return report
