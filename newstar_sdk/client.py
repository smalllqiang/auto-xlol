"""High level Python client for New-Star / 2Moons browser games."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import urljoin, urlparse

import requests
from requests.cookies import create_cookie

from .constants import (
    CATEGORY_BUILDING,
    CATEGORY_DEFENSE,
    CATEGORY_SHIP,
    CATEGORY_TECHNOLOGY,
    MISSION_NAME_TO_ID,
    RESOURCE_NAMES_ZH,
    Mission,
    PlanetType,
    message_category_name,
    resolve_element,
    resolve_message_category,
)
from .exceptions import (
    ActionRejected,
    AuthenticationError,
    ConfigurationError,
    GameError,
    ParseError,
    SessionExpired,
    TransportError,
)
from .models import (
    Bonuses,
    BuildingInfo,
    Coordinates,
    FleetDispatch,
    FleetInfo,
    FleetShips,
    GalaxyView,
    MessageCounter,
    MessagePage,
    Overview,
    PlanetInfo,
    PlanetSummary,
    ProductionReport,
    QueueEntry,
    ResearchInfo,
    Resources,
    ServerInfo,
    ShipInfo,
)
from .parsers import (
    parse_bonuses,
    parse_building_queue,
    parse_buildings,
    parse_fleet_dispatch_result,
    parse_fleet_selection,
    parse_fleet_step1,
    parse_fleet_step2,
    parse_fleets,
    parse_galaxy,
    parse_game_error,
    parse_login_universes,
    parse_message_counters,
    parse_messages,
    parse_overview,
    parse_production,
    parse_research,
    parse_research_queue,
    parse_ship_queue,
    parse_shipyard,
)
from .utils import clean_text, normalize_base_url

logger = logging.getLogger("newstar_sdk")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NewStarSDK/0.1"
)

TargetLike = Union[Coordinates, Sequence[int], str]
ElementLike = Union[int, str]
ShipsLike = Mapping[ElementLike, int]
ResourceLike = Union[Resources, Mapping[ElementLike, float], Sequence[float]]


class NewStarClient:
    """A synchronous client for the HTML interface of a New-Star game.

    Parameters
    ----------
    base_url:
        Game root, e.g. ``https://ol.lstyxl.com/``.
    username / password:
        Credentials.  They are optional when an already authenticated
        ``requests.Session`` or a saved session file is used.
    universe:
        Universe id or visible universe name (e.g. ``"CQBX8573"``).  When
        omitted the first universe on the login page is used.
    verify_ssl:
        Set to ``False`` for instances with an expired/self-signed certificate.
    planet_id:
        Select this planet before the first request.  Most methods also
        accept a planet id via :meth:`select_planet`.
    """

    def __init__(
        self,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        universe: Optional[Union[str, int]] = None,
        timeout: float = 20.0,
        verify_ssl: bool = True,
        user_agent: str = DEFAULT_USER_AGENT,
        language: Optional[str] = None,
        session: Optional[requests.Session] = None,
        planet_id: Optional[int] = None,
        auto_relogin: bool = True,
        max_retries: int = 2,
        retry_backoff: float = 1.5,
        request_interval: float = 0.25,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.username = username
        self._password = password
        self._universe = str(universe) if universe is not None else None
        self.timeout = float(timeout)
        self.verify_ssl = bool(verify_ssl)
        self.auto_relogin = bool(auto_relogin)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.request_interval = max(0.0, float(request_interval))
        self.user_agent = user_agent
        self.language = language

        self._session = session or requests.Session()
        self._session.verify = self.verify_ssl
        self._session.headers.setdefault("User-Agent", self.user_agent)
        accept_language = language or "zh-CN,zh;q=0.9,en;q=0.8"
        self._session.headers.setdefault("Accept-Language", accept_language)

        self._logged_in = False
        self._current_planet_id = int(planet_id) if planet_id is not None else None
        self._lock = threading.RLock()
        self._last_request_at = 0.0
        self._last_response: Optional[requests.Response] = None
        self._last_overview: Optional[Overview] = None

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        if self.request_interval <= 0:
            return
        now = time.monotonic()
        wait = self.request_interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        data: Any = None,
        allow_redirects: bool = True,
        max_retries: Optional[int] = None,
    ) -> requests.Response:
        """Perform an HTTP request with throttling and transient retries."""

        with self._lock:
            url = path if path.startswith(("http://", "https://")) else urljoin(self.base_url, path)
            if max_retries is None:
                # Only retry idempotent requests automatically.  Retrying a
                # POST after a lost response could queue the same game action
                # twice (or invalidate a fleet token).
                retries = self.max_retries if method.upper() == "GET" else 0
            else:
                retries = max_retries
            last_error: Optional[Exception] = None
            for attempt in range(retries + 1):
                self._throttle()
                self._last_request_at = time.monotonic()
                try:
                    response = self._session.request(
                        method.upper(),
                        url,
                        params=params,
                        data=data,
                        timeout=self.timeout,
                        allow_redirects=allow_redirects,
                    )
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt < retries:
                        time.sleep(self.retry_backoff * (attempt + 1))
                        continue
                    raise TransportError(
                        f"HTTP request failed: {exc}", url=url
                    ) from exc

                self._last_response = response
                if response.status_code >= 500 and attempt < retries:
                    time.sleep(self.retry_backoff * (attempt + 1))
                    continue
                if response.status_code >= 400:
                    raise TransportError(
                        f"HTTP {response.status_code} for {response.url}",
                        url=response.url,
                        status_code=response.status_code,
                    )
                return response
            raise TransportError(str(last_error or "HTTP request failed"), url=url)

    @staticmethod
    def _is_login_page(response: requests.Response) -> bool:
        path = urlparse(response.url).path.rstrip("/")
        if path.endswith("index.php"):
            return True
        text = response.text or ""
        return 'id="login"' in text and 'name="uni"' in text

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        data: Any = None,
        allow_redirects: bool = True,
        check_error: bool = True,
        retry_auth: bool = True,
    ) -> requests.Response:
        query: Dict[str, Any] = dict(params or {})
        if path.startswith("game.php") and self._current_planet_id is not None:
            query.setdefault("cp", str(self._current_planet_id))

        response = self._raw_request(
            method, path, params=query, data=data, allow_redirects=allow_redirects
        )

        if self._is_login_page(response):
            if retry_auth and self.auto_relogin and self._password and self.username:
                logger.info("session expired, logging in again")
                self._logged_in = False
                self.login(force=True)
                return self._request(
                    method,
                    path,
                    params=params,
                    data=data,
                    allow_redirects=allow_redirects,
                    check_error=check_error,
                    retry_auth=False,
                )
            raise SessionExpired(
                "the game session is no longer valid; call login() with valid credentials"
            )

        if check_error:
            message = parse_game_error(response.text)
            if message:
                raise GameError(clean_text(message), url=response.url)
        return response

    def _game_page(
        self,
        page: str,
        *,
        mode: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        method: str = "GET",
        data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        query: Dict[str, Any] = dict(params or {})
        query["page"] = page
        if mode:
            query["mode"] = mode
        return self._request(method, "game.php", params=query, data=data, **kwargs)

    # ------------------------------------------------------------------
    # Authentication / session
    # ------------------------------------------------------------------

    def has_password(self) -> bool:
        """Whether a password is available for login/relogin."""

        return bool(self._password)

    def set_credentials(self, username: str, password: str) -> "NewStarClient":
        """Set or replace the credentials used by :meth:`login`."""

        self.username = username
        self._password = password
        self._logged_in = False
        self._session.cookies.clear()
        self._current_planet_id = None
        return self

    def login(self, force: bool = False) -> "NewStarClient":
        """Log in and keep the PHP session cookie for subsequent requests."""

        if self._logged_in and not force:
            return self
        if not self.username or not self._password:
            raise ConfigurationError("username and password are required for login()")

        logger.debug("logging in as %s", self.username)
        index_page = self._raw_request("GET", "index.php")
        universes = parse_login_universes(index_page.text)
        universe = self._resolve_universe(universes)

        # A fresh login must not reuse a possibly expired session cookie.
        self._session.cookies.clear()
        response = self._raw_request(
            "POST",
            "index.php?page=login",
            data={
                "uni": universe,
                "username": self.username,
                "password": self._password,
            },
        )
        if self._is_login_page(response) or "game.php" not in urlparse(response.url).path:
            raise AuthenticationError(
                "login was rejected; check the username, password and universe "
                f"(universe={universe})"
            )
        self._universe = universe
        self._logged_in = True
        logger.info("logged in to %s (universe=%s)", self.base_url, universe)
        return self

    def _resolve_universe(self, universes: List[Tuple[str, str]]) -> str:
        if not universes:
            return self._universe or "1"
        if self._universe is None:
            return universes[0][0] or "1"
        wanted = str(self._universe).strip()
        for value, label in universes:
            if wanted == value or wanted.casefold() == label.casefold():
                return value
        for value, label in universes:
            if wanted.casefold() in label.casefold() or wanted in value:
                return value
        available = ", ".join(f"{value}={label}" for value, label in universes)
        raise ConfigurationError(
            f"unknown universe {self._universe!r}; available universes: {available}"
        )

    def ensure_login(self) -> "NewStarClient":
        """Make sure the client has a usable session, logging in if needed."""

        if self._logged_in:
            return self
        if self._session.cookies:
            try:
                response = self._request(
                    "GET", "game.php", params={"page": "overview"}, retry_auth=False
                )
                overview = parse_overview(response.text)
                self._accept_overview(overview)
                self._logged_in = True
                return self
            except SessionExpired:
                if not (self.username and self._password):
                    raise
        if self.username and self._password:
            return self.login()
        raise ConfigurationError(
            "not logged in: provide username/password or load a valid session file"
        )

    def is_logged_in(self) -> bool:
        """Check the current session with a lightweight page request."""

        if not self._session.cookies and not (self.username and self._password):
            return False
        try:
            self._request("GET", "game.php", params={"page": "overview"}, retry_auth=False)
            return True
        except SessionExpired:
            return False
        except GameError:
            # A game error (e.g. a disabled module) means the session itself is valid.
            return True

    def logout(self) -> None:
        """Log out and drop all cookies."""

        try:
            if self._session.cookies:
                self._raw_request("GET", "game.php?page=logout")
        finally:
            self._session.cookies.clear()
            self._logged_in = False
            self._last_overview = None

    def save_session(self, path: Union[str, Path]) -> Path:
        """Persist cookies and non-secret client state to a JSON file."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cookies = []
        for cookie in self._session.cookies:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": bool(cookie.secure),
                    "expires": cookie.expires,
                }
            )
        payload = {
            "base_url": self.base_url,
            "username": self.username,
            "universe": self._universe,
            "cookies": cookies,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover - best effort on exotic filesystems
            pass
        return target

    def load_session(self, path: Union[str, Path], *, validate: bool = True) -> bool:
        """Load cookies from :meth:`save_session`.

        Returns ``True`` when the stored session is still valid.  The method
        never stores the password; if the session expired, call :meth:`login`
        (or :meth:`ensure_login`) with credentials.
        """

        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        stored_base = payload.get("base_url")
        if stored_base and normalize_base_url(stored_base) != self.base_url:
            logger.warning("session file belongs to %s, not %s", stored_base, self.base_url)
        self._session.cookies.clear()
        for item in payload.get("cookies", []):
            if not item.get("name"):
                continue
            cookie = create_cookie(
                name=item["name"],
                value=item.get("value", ""),
                domain=item.get("domain") or None,
                path=item.get("path") or "/",
                secure=bool(item.get("secure")),
                expires=item.get("expires"),
            )
            self._session.cookies.set_cookie(cookie)
        if not self._universe and payload.get("universe"):
            self._universe = str(payload["universe"])
        if not self.username and payload.get("username"):
            self.username = payload["username"]
        self._logged_in = False
        if not validate:
            self._logged_in = True
            return True
        try:
            response = self._request(
                "GET", "game.php", params={"page": "overview"}, retry_auth=False
            )
        except SessionExpired:
            self._session.cookies.clear()
            return False
        overview = parse_overview(response.text)
        self._accept_overview(overview)
        self._logged_in = True
        return True

    def _accept_overview(self, overview: Overview) -> None:
        self._last_overview = overview
        if overview.planet and overview.planet.id:
            self._current_planet_id = overview.planet.id
        if overview.username and not self.username:
            self.username = overview.username
        if overview.universe and not self._universe:
            self._universe = overview.universe

    # ------------------------------------------------------------------
    # Overview / planets
    # ------------------------------------------------------------------

    def get_overview(self) -> Overview:
        """Read resources, the selected planet and the overview activity cards."""

        self.ensure_login()
        response = self._game_page("overview")
        overview = parse_overview(response.text)
        self._accept_overview(overview)
        return overview

    def get_planets(self) -> List[PlanetSummary]:
        return self.get_overview().planets

    def get_current_planet(self) -> PlanetInfo:
        return self.get_overview().planet

    def get_resources(self) -> Resources:
        return self.get_overview().resources

    def get_server_info(self) -> ServerInfo:
        overview = self.get_overview()
        return ServerInfo(
            username=overview.username,
            universe=overview.universe,
            game_name=overview.game_name,
            engine_version=overview.engine_version,
            language=overview.language,
            server_time=overview.server_time,
        )

    # ------------------------------------------------------------------
    # Messages and bonuses
    # ------------------------------------------------------------------

    def get_message_counters(self) -> Dict[int, MessageCounter]:
        """Unread/total counters per category, from the inbox shell page.

        One request is enough to answer "is there anything new?", which makes
        this the cheapest event trigger for a long running automation loop.
        """

        return parse_message_counters(self.get_page("messages"))

    def get_messages(
        self,
        category: Union[int, str] = "all",
        page: int = 1,
    ) -> MessagePage:
        """Read one page of messages.

        ``category`` accepts an id, a Chinese/English label or a short alias
        such as ``"expedition"`` / ``"battle"`` / ``"outbox"``.  The message
        list is rendered by AJAX, so this uses
        ``page=messages&mode=view&messcat=..&site=..&ajax=1``.
        """

        category_id = resolve_message_category(category)
        html = self.get_page(
            "messages",
            mode="view",
            messcat=category_id,
            site=max(1, int(page)),
            ajax=1,
        )
        result = parse_messages(html)
        result.category = category_id
        result.category_name = message_category_name(category_id)
        if page > 1:
            result.page = int(page)
        return result

    def get_production(self) -> ProductionReport:
        """Read the production control table (``page=resources``).

        Contains the per-planet base production, building contributions, bonus,
        storage capacity, totals and the current production percentages.
        """

        return parse_production(self.get_page("resources"))

    def get_bonuses(self) -> Bonuses:
        """Read the bonus summary (``page=infobonus``).

        The returned labels are the ones the game renders; use
        :meth:`Bonuses.percent` / :meth:`Bonuses.multiplier` to look values up,
        e.g. ``client.get_bonuses().percent("全资源生产加成")``.
        """

        return parse_bonuses(self.get_page("infobonus"))

    def select_planet(self, planet_id: int) -> PlanetInfo:
        """Switch the active planet (the game session stores it server-side)."""

        self.ensure_login()
        previous = self._current_planet_id
        self._current_planet_id = int(planet_id)
        try:
            response = self._game_page("overview")
            overview = parse_overview(response.text)
            self._accept_overview(overview)
            if overview.planet.id and int(overview.planet.id) != int(planet_id):
                raise GameError(
                    f"planet {planet_id} was not selected "
                    f"(current planet is {overview.planet.id})"
                )
            return overview.planet
        except Exception:
            self._current_planet_id = previous
            raise

    @property
    def universe(self) -> Optional[str]:
        return self._universe

    @property
    def planet_id(self) -> Optional[int]:
        return self._current_planet_id

    @property
    def last_response(self) -> Optional[requests.Response]:
        """The most recent HTTP response, useful for debugging parsers."""

        return self._last_response

    @property
    def last_overview(self) -> Optional[Overview]:
        """The most recently parsed overview."""

        return self._last_overview

    # ------------------------------------------------------------------
    # Buildings
    # ------------------------------------------------------------------

    def get_buildings(self) -> List[BuildingInfo]:
        self.ensure_login()
        response = self._game_page("buildings")
        return parse_buildings(response.text)

    def get_building_queue(self) -> List[QueueEntry]:
        self.ensure_login()
        response = self._game_page("buildings")
        return parse_building_queue(response.text)

    def upgrade_building(
        self,
        building: ElementLike,
        levels: int = 1,
        *,
        target_level: Optional[int] = None,
        verify: bool = True,
    ) -> List[QueueEntry]:
        """Queue one or more levels of a building.

        ``levels`` is added to the currently queued level (not necessarily the
        finished level).  ``target_level`` overrides that calculation.
        """

        element_id = resolve_element(building, CATEGORY_BUILDING)
        items = {item.id: item for item in self.get_buildings()}
        item = items.get(element_id)
        if item is None:
            raise GameError(f"building {element_id} is not available on this planet")
        if not item.requirements_met:
            raise ActionRejected(
                f"{item.name} requirements are not met: {', '.join(item.requirements)}",
                before_post=True,
            )
        if item.missing_resources:
            raise ActionRejected(
                f"not enough resources for {item.name}: "
                + ", ".join(
                    f"{RESOURCE_NAMES_ZH.get(rid, rid)} {amount:g}"
                    for rid, amount in sorted(item.missing_resources.items())
                ),
                before_post=True,
            )

        if target_level is None and int(levels) < 1:
            raise ValueError("levels must be at least 1")
        base_level = item.level_to_build
        target = int(target_level) if target_level is not None else base_level + int(levels)
        if target <= base_level:
            raise ValueError(
                f"target level {target} must be greater than current/queued level {base_level}"
            )
        if target > item.max_level:
            raise ValueError(f"target level {target} exceeds the maximum level {item.max_level}")

        response = self._game_page(
            "buildings",
            method="POST",
            data={
                "cmd": "insert",
                "building": str(element_id),
                "lvlup": str(target),
                "lvlup1": str(item.level),
                "levelToBuildInFo": str(base_level),
            },
        )
        queue = parse_building_queue(response.text)
        if verify and not self._building_action_applied(response.text, element_id, target):
            raise ActionRejected(
                f"upgrade of {item.name} to level {target} was not accepted; "
                "check resources, field space, queue slots and requirements"
            )
        return queue

    def _building_action_applied(self, html: str, element_id: int, target: int) -> bool:
        for entry in parse_building_queue(html):
            if entry.element_id == element_id and (entry.target_level or 0) >= target:
                return True
        for item in parse_buildings(html):
            if item.id == element_id and item.level >= target:
                return True
        return False

    def cancel_building_queue(self, position: int = 0) -> List[QueueEntry]:
        """Cancel a building queue item (0 = the active item)."""

        before = self.get_building_queue()
        if not before:
            raise GameError("the building queue is empty")
        position = int(position)
        if position < 0 or position >= len(before):
            raise ValueError(f"queue position {position} is out of range (0..{len(before) - 1})")
        if position == 0:
            data = {"cmd": "cancel"}
        else:
            data = {"cmd": "remove", "listid": str(position + 1)}
        response = self._game_page("buildings", method="POST", data=data)
        return parse_building_queue(response.text)

    def destroy_building(self, building: ElementLike, *, verify: bool = True) -> List[QueueEntry]:
        """Queue the destruction of one building level (destructive!)."""

        element_id = resolve_element(building, CATEGORY_BUILDING)
        items = {item.id: item for item in self.get_buildings()}
        item = items.get(element_id)
        if item is None:
            raise GameError(f"building {element_id} is not available on this planet")
        if item.level <= 0:
            raise GameError(f"{item.name} has no level to destroy")
        response = self._game_page(
            "buildings", method="POST", data={"cmd": "destroy", "building": str(element_id)}
        )
        queue = parse_building_queue(response.text)
        if verify:
            accepted = any(
                entry.element_id == element_id
                and (entry.target_level is None or entry.target_level <= item.level)
                for entry in queue
            )
            if not accepted:
                for updated in parse_buildings(response.text):
                    if updated.id == element_id and updated.level < item.level:
                        accepted = True
                        break
            if not accepted:
                raise ActionRejected(f"destruction of {item.name} was not accepted")
        return queue

    def wait_for_building_queue(
        self, timeout: float = 300.0, poll_interval: float = 2.0
    ) -> List[QueueEntry]:
        return self._wait_for_queue(self.get_building_queue, timeout, poll_interval, "building")

    def _wait_for_queue(
        self, getter, timeout: float, poll_interval: float, kind: str
    ) -> List[QueueEntry]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        last: List[QueueEntry] = []
        while True:
            last = getter()
            if not last:
                return last
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{kind} queue did not become empty within {timeout}s")
            time.sleep(max(0.2, float(poll_interval)))

    # ------------------------------------------------------------------
    # Research
    # ------------------------------------------------------------------

    def get_research(self) -> List[ResearchInfo]:
        self.ensure_login()
        response = self._game_page("research")
        return parse_research(response.text)

    def get_research_queue(self) -> List[QueueEntry]:
        self.ensure_login()
        response = self._game_page("research")
        return parse_research_queue(response.text)

    def research(
        self,
        technology: ElementLike,
        levels: int = 1,
        *,
        target_level: Optional[int] = None,
        verify: bool = True,
    ) -> List[QueueEntry]:
        """Queue one or more levels of a technology."""

        element_id = resolve_element(technology, CATEGORY_TECHNOLOGY)
        items = {item.id: item for item in self.get_research()}
        item = items.get(element_id)
        if item is None:
            raise GameError(f"technology {element_id} is not available")
        if not item.requirements_met:
            raise ActionRejected(
                f"{item.name} requirements are not met: {', '.join(item.requirements)}",
                before_post=True,
            )
        if item.missing_resources:
            raise ActionRejected(
                f"not enough resources for {item.name}: "
                + ", ".join(
                    f"{RESOURCE_NAMES_ZH.get(rid, rid)} {amount:g}"
                    for rid, amount in sorted(item.missing_resources.items())
                ),
                before_post=True,
            )

        if target_level is None and int(levels) < 1:
            raise ValueError("levels must be at least 1")
        base_level = item.level_to_build
        target = int(target_level) if target_level is not None else base_level + int(levels)
        if target <= base_level:
            raise ValueError(
                f"target level {target} must be greater than current/queued level {base_level}"
            )
        if target > item.max_level:
            raise ValueError(f"target level {target} exceeds the maximum level {item.max_level}")

        response = self._game_page(
            "research",
            method="POST",
            data={
                "cmd": "insert",
                "tech": str(element_id),
                "lvlup": str(target),
                "lvlup1": str(item.level),
                "levelToBuildInFo": str(base_level),
            },
        )
        queue = parse_research_queue(response.text)
        if verify and not self._research_action_applied(response.text, element_id, target):
            raise ActionRejected(
                f"research of {item.name} to level {target} was not accepted; "
                "check resources and laboratory requirements"
            )
        return queue

    def _research_action_applied(self, html: str, element_id: int, target: int) -> bool:
        for entry in parse_research_queue(html):
            if entry.element_id == element_id and (entry.target_level or 0) >= target:
                return True
        for item in parse_research(html):
            if item.id == element_id and item.level >= target:
                return True
        return False

    def cancel_research_queue(self, position: int = 0) -> List[QueueEntry]:
        before = self.get_research_queue()
        if not before:
            raise GameError("the research queue is empty")
        position = int(position)
        if position < 0 or position >= len(before):
            raise ValueError(f"queue position {position} is out of range (0..{len(before) - 1})")
        if position == 0:
            data = {"cmd": "cancel"}
        else:
            data = {"cmd": "remove", "listid": str(position + 1)}
        response = self._game_page("research", method="POST", data=data)
        return parse_research_queue(response.text)

    def wait_for_research_queue(
        self, timeout: float = 300.0, poll_interval: float = 2.0
    ) -> List[QueueEntry]:
        return self._wait_for_queue(self.get_research_queue, timeout, poll_interval, "research")

    # ------------------------------------------------------------------
    # Shipyard / defences
    # ------------------------------------------------------------------

    def get_shipyard(self, mode: str = "fleet") -> List[ShipInfo]:
        mode = self._normalise_shipyard_mode(mode)
        self.ensure_login()
        response = self._game_page("shipyard", mode=mode)
        return parse_shipyard(response.text, mode)

    def get_ships(self) -> List[ShipInfo]:
        return self.get_shipyard("fleet")

    def get_defenses(self) -> List[ShipInfo]:
        return self.get_shipyard("defense")

    def get_ship_queue(self, mode: str = "fleet") -> List[QueueEntry]:
        mode = self._normalise_shipyard_mode(mode)
        self.ensure_login()
        response = self._game_page("shipyard", mode=mode)
        return parse_ship_queue(response.text)

    @staticmethod
    def _normalise_shipyard_mode(mode: str) -> str:
        text = str(mode).strip().casefold()
        if text in {"fleet", "ships", "ship", "船坞", "舰船"}:
            return "fleet"
        if text in {"defense", "defence", "defenses", "防御"}:
            return "defense"
        raise ValueError("mode must be 'fleet' or 'defense'")

    def build_ships(
        self,
        ships: ShipsLike,
        *,
        mode: str = "fleet",
        verify: bool = True,
    ) -> List[QueueEntry]:
        """Build ships or defences.

        ``ships`` maps element ids/names to amounts, e.g. ``{202: 10}``.
        """

        mode = self._normalise_shipyard_mode(mode)
        category = CATEGORY_DEFENSE if mode == "defense" else CATEGORY_SHIP
        requested: Dict[int, int] = {}
        for key, value in ships.items():
            element_id = resolve_element(key, category)
            amount = int(value)
            if amount <= 0:
                raise ValueError(f"amount for element {element_id} must be positive")
            requested[element_id] = requested.get(element_id, 0) + amount
        if not requested:
            raise ValueError("at least one ship/defence must be requested")

        items = {item.id: item for item in self.get_shipyard(mode)}
        for element_id, amount in requested.items():
            item = items.get(element_id)
            if item is None:
                raise GameError(f"element {element_id} is not available in this shipyard")
            if not item.requirements_met:
                raise ActionRejected(
                    f"{item.name} requirements are not met: {', '.join(item.requirements)}",
                    before_post=True,
                )
            if item.max_buildable <= 0:
                raise ActionRejected(
                    f"{item.name} cannot be built now (resources or prerequisites)",
                    before_post=True,
                )
            if amount > item.max_buildable:
                raise ValueError(
                    f"requested {amount} x {item.name}, but at most {item.max_buildable} "
                    "can be built with the current resources"
                )

        data = {f"fmenge[{element_id}]": str(amount) for element_id, amount in requested.items()}
        response = self._game_page("shipyard", mode=mode, method="POST", data=data)
        queue = parse_ship_queue(response.text)
        if verify and not self._ship_action_applied(response.text, mode, requested, items):
            raise ActionRejected(
                "the shipyard request was not accepted; check resources, requirements "
                "and shipyard queue slots"
            )
        return queue

    def _ship_action_applied(
        self,
        html: str,
        mode: str,
        requested: Mapping[int, int],
        before: Mapping[int, ShipInfo],
    ) -> bool:
        after = {item.id: item for item in parse_shipyard(html, mode)}
        queued: Dict[int, int] = {}
        for entry in parse_ship_queue(html):
            if entry.element_id is not None and entry.amount:
                queued[entry.element_id] = queued.get(entry.element_id, 0) + entry.amount
        for element_id, amount in requested.items():
            previous = before.get(element_id)
            previous_count = previous.available if previous else 0
            current_count = after.get(element_id).available if element_id in after else previous_count
            if queued.get(element_id, 0) < amount and current_count < previous_count + amount:
                return False
        return True

    def cancel_ship_queue(self, position: int = 0, *, mode: str = "fleet") -> List[QueueEntry]:
        mode = self._normalise_shipyard_mode(mode)
        before = self.get_ship_queue(mode)
        if not before:
            raise GameError("the shipyard queue is empty")
        position = int(position)
        if position < 0 or position >= len(before):
            raise ValueError(f"queue position {position} is out of range (0..{len(before) - 1})")
        response = self._game_page(
            "shipyard",
            mode=mode,
            method="POST",
            data={"action": "delete", "auftr[]": str(position)},
        )
        return parse_ship_queue(response.text)

    def wait_for_ship_queue(
        self,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        *,
        mode: str = "fleet",
    ) -> List[QueueEntry]:
        return self._wait_for_queue(
            lambda: self.get_ship_queue(mode), timeout, poll_interval, "shipyard"
        )

    # ------------------------------------------------------------------
    # Galaxy
    # ------------------------------------------------------------------

    def get_galaxy(
        self, galaxy: Optional[int] = None, system: Optional[int] = None
    ) -> GalaxyView:
        """Read one galaxy system page."""

        self.ensure_login()
        params: Dict[str, Any] = {}
        if galaxy is None or system is None or not self.username:
            overview = self.get_overview()
            if overview.planet.coordinates:
                galaxy = overview.planet.coordinates.galaxy if galaxy is None else galaxy
                system = overview.planet.coordinates.system if system is None else system
        if galaxy is not None:
            params["galaxy"] = str(int(galaxy))
        if system is not None:
            params["system"] = str(int(system))
        response = self._game_page("galaxy", params=params)
        return parse_galaxy(
            response.text,
            current_planet_id=self._current_planet_id,
            username=self.username,
        )

    # ------------------------------------------------------------------
    # Fleets
    # ------------------------------------------------------------------

    def get_fleets(self) -> List[FleetInfo]:
        self.ensure_login()
        response = self._game_page("fleetTable")
        return parse_fleets(response.text)

    def get_available_ships(self) -> Dict[int, FleetShips]:
        """Ships that can be selected on the fleet dispatch page."""

        self.ensure_login()
        response = self._game_page("fleetTable")
        return parse_fleet_selection(response.text)

    def send_fleet(
        self,
        target: TargetLike,
        ships: ShipsLike,
        mission: Union[int, Mission, str] = Mission.TRANSPORT,
        *,
        speed: int = 10,
        resources: Optional[ResourceLike] = None,
        stay_time: Union[int, str] = 0,
        fleet_group: int = 0,
        target_type: Optional[Union[int, PlanetType]] = None,
        max_wave: int = 1,
        sectors: int = 3,
        verify: bool = True,
    ) -> FleetDispatch:
        """Send a fleet through the game's three-step dispatch flow."""

        self.ensure_login()
        resolved_type = (
            int(target_type)
            if target_type is not None
            else (target.type if isinstance(target, Coordinates) else int(PlanetType.PLANET))
        )
        coordinates = self._coerce_target(target, resolved_type)
        mission_id = self._coerce_mission(mission)
        if not ships:
            raise ValueError("at least one ship is required")
        speed = int(speed)
        if not 1 <= speed <= 10:
            raise ValueError("speed must be between 1 and 10")
        ship_payload: Dict[str, str] = {}
        for key, value in ships.items():
            element_id = resolve_element(key, CATEGORY_SHIP)
            amount = int(value)
            if amount <= 0:
                raise ValueError(f"amount for ship {element_id} must be positive")
            ship_payload[f"ship{element_id}"] = str(amount)
        resource_payload = self._coerce_resources(resources)
        if mission_id == int(Mission.TRANSPORT) and sum(resource_payload.values()) < 1:
            raise ValueError("a transport mission requires at least one resource")

        common = {
            "galaxy": str(coordinates.galaxy),
            "system": str(coordinates.system),
            "planet": str(coordinates.planet),
            "type": str(coordinates.type),
        }

        step1_data = dict(common)
        step1_data["target_mission"] = str(mission_id)
        step1_data.update(ship_payload)
        response = self._game_page("fleetStep1", method="POST", data=step1_data)
        token, _max_speed = parse_fleet_step1(response.text)
        if not token:
            raise ParseError("fleetStep1 did not return a dispatch token")

        step2_data = dict(common)
        step2_data.update(
            {
                "token": token,
                "target_mission": str(mission_id),
                "speed": str(int(speed)),
                "fleet_group": str(int(fleet_group)),
            }
        )
        response = self._game_page("fleetStep2", method="POST", data=step2_data)
        token, missions = parse_fleet_step2(response.text)
        if not token:
            raise ParseError("fleetStep2 did not return a dispatch token")
        available = {option.id for option in missions}
        if available and mission_id not in available:
            names = ", ".join(f"{option.id}={option.name}" for option in missions)
            raise ActionRejected(
                f"mission {mission_id} is not available for this target; available: {names}"
            )

        step3_data = {
            "token": token,
            "mission": str(mission_id),
            "metal": str(int(resource_payload[901])),
            "crystal": str(int(resource_payload[902])),
            "deuterium": str(int(resource_payload[903])),
            "staytime": str(stay_time),
            "maxwave": str(int(max_wave)),
            "sectors": str(int(sectors)),
        }
        response = self._game_page("fleetStep3", method="POST", data=step3_data)
        result = parse_fleet_dispatch_result(response.text)
        if result is None:
            if verify:
                raise ActionRejected(
                    "the fleet dispatch did not reach the confirmation page; "
                    "check fuel, fleet slots, target and mission rules"
                )
            return FleetDispatch(mission=mission_id)
        return result

    def recall_fleet(self, fleet_id: int) -> List[FleetInfo]:
        """Recall one of your own fleets."""

        fleets = self.get_fleets()
        if not any(fleet.id == int(fleet_id) for fleet in fleets):
            raise GameError(f"fleet {fleet_id} was not found in the fleet table")
        response = self._game_page(
            "fleetTable",
            params={"action": "sendfleetback"},
            method="POST",
            data={"fleetID": str(int(fleet_id))},
        )
        return parse_fleets(response.text)

    def _coerce_target(self, target: TargetLike, target_type: int) -> Coordinates:
        if isinstance(target, Coordinates):
            return Coordinates(target.galaxy, target.system, target.planet, target_type)
        if isinstance(target, str):
            parsed = Coordinates.parse(target, type=target_type)
            return parsed
        if isinstance(target, (tuple, list)) and len(target) >= 3:
            return Coordinates(int(target[0]), int(target[1]), int(target[2]), target_type)
        raise ValueError("target must be Coordinates, a (galaxy, system, planet) tuple or a string")

    @staticmethod
    def _coerce_mission(mission: Union[int, Mission, str]) -> int:
        if isinstance(mission, Mission):
            return int(mission)
        if isinstance(mission, int):
            return mission
        text = str(mission).strip()
        if text.isdigit():
            return int(text)
        mission_id = MISSION_NAME_TO_ID.get(text.casefold())
        if mission_id is None:
            raise ValueError(f"unknown mission: {mission!r}")
        return mission_id

    @staticmethod
    def _coerce_resources(resources: Optional[ResourceLike]) -> Dict[int, float]:
        result = {901: 0.0, 902: 0.0, 903: 0.0}
        if resources is None:
            return result
        if isinstance(resources, Resources):
            for rid in result:
                result[rid] = float(resources.amounts.get(rid, 0.0))
            return result
        if isinstance(resources, Mapping):
            aliases = {
                "metal": 901,
                "m": 901,
                "901": 901,
                "crystal": 902,
                "c": 902,
                "902": 902,
                "deuterium": 903,
                "deut": 903,
                "d": 903,
                "903": 903,
            }
            for key, value in resources.items():
                text = str(key).strip().casefold()
                rid = aliases.get(text)
                if rid is None:
                    raise ValueError(f"unknown transport resource: {key!r}")
                result[rid] = max(0.0, float(value))
            return result
        if isinstance(resources, (tuple, list)):
            if len(resources) < 3:
                raise ValueError("resources sequence must contain (metal, crystal, deuterium)")
            result[901], result[902], result[903] = (max(0.0, float(x)) for x in resources[:3])
            return result
        raise ValueError("resources must be a Resources object, mapping or 3-item sequence")

    # ------------------------------------------------------------------
    # Raw access
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        data: Any = None,
        check_error: bool = False,
        allow_redirects: bool = True,
    ) -> requests.Response:
        """Escape hatch for endpoints not covered by the high level API."""

        self.ensure_login()
        return self._request(
            method,
            path,
            params=params,
            data=data,
            check_error=check_error,
            allow_redirects=allow_redirects,
        )

    def get_page(self, page: str, **params: Any) -> str:
        """Return the HTML of an arbitrary ingame page (e.g. ``"messages"``)."""

        self.ensure_login()
        response = self._game_page(page, params={k: str(v) for k, v in params.items()})
        return response.text

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "NewStarClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(base_url={self.base_url!r}, "
            f"username={self.username!r}, universe={self._universe!r})"
        )
