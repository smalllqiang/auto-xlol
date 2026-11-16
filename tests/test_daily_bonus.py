from datetime import datetime

import pytest

from autoplay.daily import run_daily_bonus
from newstar_sdk.client import NewStarClient
from newstar_sdk.exceptions import ActionRejected
from newstar_sdk.models import (
    DailyBonus,
    DailyBonusItem,
    DailyBonusStatus,
    Overview,
)
from newstar_sdk.parsers import (
    parse_daily_bonus,
    parse_daily_bonus_status,
    parse_overview,
)

SERVER_TIME = datetime(2026, 11, 16, 22, 20, 25)
NEXT_CLAIM = datetime(2026, 11, 17, 22, 20, 25)


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/game.php"):
        self.text = text
        self.url = url
        self.status_code = 200


def _make_client() -> NewStarClient:
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    return client


def _overview(status: DailyBonusStatus) -> Overview:
    return Overview(server_time=SERVER_TIME, daily_bonus=status)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_parse_daily_bonus_items(fixture_html):
    items = parse_daily_bonus(fixture_html("bonus_claim.html"))

    assert [(item.element_id, item.name, item.amount) for item in items] == [
        (921, "暗物质", 160.0),
        (922, "反幽能", 95.0),
        (924, "宝箱", 3.0),
    ]


def test_parse_daily_bonus_ignores_other_cards(fixture_html):
    # The claimed page also contains building/ship cards nowhere, but a page
    # without the reward cards must parse to an empty list, not to noise.
    assert parse_daily_bonus(fixture_html("overview.html")) == []


def test_parse_daily_bonus_status_available():
    html = (
        '<div id="left_menu">'
        '<a class="big_btn blue btn_menu btn_menu_big"> <div class="servertime"></div> </a>'
        '<a class="big_btn blue btn_menu btn_menu_big" href="game.php?page=bonus">每日签到</a>'
        "</div>"
    )
    status = parse_daily_bonus_status(html)

    assert status.available is True
    assert status.available_at is None


def test_parse_daily_bonus_status_cooldown(fixture_html):
    overview = parse_overview(fixture_html("overview.html"))

    assert overview.daily_bonus.available is False
    assert overview.daily_bonus.available_at == datetime(2026, 11, 14, 11, 37, 51)


def test_parse_daily_bonus_status_unknown():
    status = parse_daily_bonus_status("<html><body><p>login</p></body></html>")

    assert status.available is None
    assert status.available_at is None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def test_get_daily_bonus_status_reads_overview(monkeypatch):
    client = _make_client()
    status = DailyBonusStatus(available=True)
    monkeypatch.setattr(client, "get_overview", lambda: _overview(status))

    assert client.get_daily_bonus_status() is status


def test_claim_daily_bonus_claims(fixture_html, monkeypatch):
    client = _make_client()
    states = [
        _overview(DailyBonusStatus(available=True)),
        _overview(
            DailyBonusStatus(available=False, available_at=NEXT_CLAIM)
        ),
    ]
    monkeypatch.setattr(client, "get_overview", lambda: states.pop(0))
    calls = []

    def fake_game_page(page, **kwargs):
        calls.append((page, kwargs))
        return FakeResponse(fixture_html("bonus_claim.html"))

    monkeypatch.setattr(client, "_game_page", fake_game_page)

    result = client.claim_daily_bonus()

    assert result.claimed is True
    assert result.amounts == {921: 160.0, 922: 95.0, 924: 3.0}
    assert result.status.available is False
    assert result.status.available_at == NEXT_CLAIM
    assert result.server_time == SERVER_TIME
    # The page is the only evidence of what was granted: never retry it.
    assert calls == [("bonus", {"max_retries": 0})]


def test_claim_daily_bonus_does_not_touch_page_during_cooldown(monkeypatch):
    client = _make_client()
    unavailable = DailyBonusStatus(available=False, available_at=NEXT_CLAIM)
    monkeypatch.setattr(client, "get_overview", lambda: _overview(unavailable))

    def forbidden(*args, **kwargs):
        raise AssertionError("page=bonus must not be requested during cooldown")

    monkeypatch.setattr(client, "_game_page", forbidden)

    result = client.claim_daily_bonus()

    assert result.claimed is False
    assert result.items == []
    assert result.status is unavailable
    assert result.server_time == SERVER_TIME


def test_claim_daily_bonus_does_not_gamble_when_status_unknown(monkeypatch):
    client = _make_client()
    unknown = DailyBonusStatus()
    monkeypatch.setattr(client, "get_overview", lambda: _overview(unknown))

    def forbidden(*args, **kwargs):
        raise AssertionError("page=bonus must not be requested when the nav is unknown")

    monkeypatch.setattr(client, "_game_page", forbidden)

    result = client.claim_daily_bonus()

    assert result.claimed is False
    assert result.status.available is None


def test_claim_daily_bonus_rejects_when_page_shows_no_rewards(
    fixture_html, monkeypatch
):
    client = _make_client()
    monkeypatch.setattr(
        client, "get_overview", lambda: _overview(DailyBonusStatus(available=True))
    )
    monkeypatch.setattr(
        client,
        "_game_page",
        lambda *args, **kwargs: FakeResponse(fixture_html("overview.html")),
    )

    with pytest.raises(ActionRejected, match="没有返回奖励"):
        client.claim_daily_bonus()


def test_claim_daily_bonus_rejects_when_cooldown_did_not_start(
    fixture_html, monkeypatch
):
    client = _make_client()
    monkeypatch.setattr(
        client, "get_overview", lambda: _overview(DailyBonusStatus(available=True))
    )
    monkeypatch.setattr(
        client,
        "_game_page",
        lambda *args, **kwargs: FakeResponse(fixture_html("bonus_claim.html")),
    )

    with pytest.raises(ActionRejected, match="仍显示可领取"):
        client.claim_daily_bonus()


# ---------------------------------------------------------------------------
# Strategy layer
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(
        self,
        status: DailyBonusStatus,
        bonus: DailyBonus | None = None,
    ) -> None:
        self.status = status
        self.bonus = bonus
        self.calls = []

    def get_daily_bonus_status(self) -> DailyBonusStatus:
        self.calls.append("status")
        return self.status

    def claim_daily_bonus(self) -> DailyBonus:
        self.calls.append("claim")
        assert self.bonus is not None
        return self.bonus


def test_run_daily_bonus_dry_run_reports_available():
    client = FakeClient(DailyBonusStatus(available=True))

    outcome = run_daily_bonus(client, dry_run=True)

    assert outcome.status == "available"
    assert outcome.interesting is True
    assert client.calls == ["status"]


def test_run_daily_bonus_dry_run_reports_cooldown():
    client = FakeClient(DailyBonusStatus(available=False, available_at=NEXT_CLAIM))

    outcome = run_daily_bonus(client, dry_run=True)

    assert outcome.status == "cooldown"
    assert outcome.available_at == "2026-11-17 22:20:25"
    assert outcome.interesting is False
    assert client.calls == ["status"]


def test_run_daily_bonus_claims():
    bonus = DailyBonus(
        claimed=True,
        items=[DailyBonusItem(element_id=921, name="暗物质", amount=160.0)],
        status=DailyBonusStatus(available=False, available_at=NEXT_CLAIM),
        server_time=SERVER_TIME,
    )
    client = FakeClient(DailyBonusStatus(available=True), bonus)

    outcome = run_daily_bonus(client, dry_run=False)

    assert outcome.status == "claimed"
    assert outcome.items == [{"element_id": 921, "name": "暗物质", "amount": 160.0}]
    assert outcome.available_at == "2026-11-17 22:20:25"
    assert outcome.evidence["amounts"] == {"921": 160.0}
    assert outcome.as_dict()["status"] == "claimed"
    assert client.calls == ["claim"]


def test_run_daily_bonus_execute_reports_cooldown():
    bonus = DailyBonus(
        claimed=False,
        status=DailyBonusStatus(available=False, available_at=NEXT_CLAIM),
        server_time=SERVER_TIME,
    )
    client = FakeClient(DailyBonusStatus(available=False), bonus)

    outcome = run_daily_bonus(client, dry_run=False)

    assert outcome.status == "cooldown"
    assert outcome.interesting is False
    assert client.calls == ["claim"]


def test_run_daily_bonus_unknown_status():
    client = FakeClient(DailyBonusStatus())

    outcome = run_daily_bonus(client, dry_run=True)

    assert outcome.status == "unknown"
    assert outcome.interesting is True
