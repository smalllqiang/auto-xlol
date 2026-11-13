"""Optional integration tests against a real game instance.

They are skipped unless ``NEWSTAR_BASE_URL``, ``NEWSTAR_USERNAME`` and
``NEWSTAR_PASSWORD`` are set.  The tests only read data; no game action is
performed.
"""

import os

import pytest

from newstar_sdk import NewStarClient


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_client():
    base_url = os.getenv("NEWSTAR_BASE_URL")
    username = os.getenv("NEWSTAR_USERNAME")
    password = os.getenv("NEWSTAR_PASSWORD")
    if not all((base_url, username, password)):
        pytest.skip("NEWSTAR_BASE_URL / NEWSTAR_USERNAME / NEWSTAR_PASSWORD are not set")

    client = NewStarClient(
        base_url,
        username,
        password,
        universe=os.getenv("NEWSTAR_UNIVERSE"),
        verify_ssl=_env_bool("NEWSTAR_VERIFY_SSL", True),
        request_interval=0.5,
    )
    client.login()
    yield client
    client.close()


def test_live_overview(live_client):
    overview = live_client.get_overview()
    assert overview.resources.metal >= 0
    assert overview.resources.crystal >= 0
    assert overview.planet.name
    assert overview.planet.coordinates is not None


def test_live_element_lists(live_client):
    buildings = live_client.get_buildings()
    research = live_client.get_research()
    ships = live_client.get_ships()
    defenses = live_client.get_defenses()
    assert len(buildings) > 0
    assert len(research) > 0
    assert len(ships) > 0
    assert len(defenses) > 0


def test_live_galaxy_and_fleets(live_client):
    overview = live_client.get_overview()
    coords = overview.planet.coordinates
    assert coords is not None
    galaxy = live_client.get_galaxy(coords.galaxy, coords.system)
    assert len(galaxy.slots) > 0
    assert any(slot.is_own for slot in galaxy.slots)
    assert isinstance(live_client.get_fleets(), list)


def test_live_session_roundtrip(live_client, tmp_path):
    path = live_client.save_session(tmp_path / "session.json")
    other = NewStarClient(
        live_client.base_url,
        verify_ssl=live_client.verify_ssl,
        request_interval=0.5,
    )
    assert other.load_session(path) is True
    overview = other.get_overview()
    assert overview.planet.coordinates is not None
    other.close()


def test_live_messages(live_client):
    counters = live_client.get_message_counters()
    assert counters
    assert all(counter.unread >= 0 for counter in counters.values())

    page = live_client.get_messages("all")
    assert page.category == 100
    assert page.page >= 1
    for message in page:
        assert message.id > 0
        assert message.category is not None


def test_live_bonuses(live_client):
    bonuses = live_client.get_bonuses()
    assert len(bonuses) > 0
    assert bonuses.multiplier("全资源生产加成") >= 1.0 or bonuses.percent(
        "全资源生产加成"
    ) < 0


def test_live_snapshot(live_client):
    from newstar_sdk.snapshot import build_snapshot

    snapshot = build_snapshot(live_client)
    assert snapshot["schema"] == 1
    assert snapshot["errors"] == []
    assert snapshot["resources"]["amounts"]["901"] >= 0
    assert snapshot["planet"]["coordinates"]
