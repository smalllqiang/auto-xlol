import pytest

from newstar_sdk.client import NewStarClient
from newstar_sdk.constants import resolve_message_category
from newstar_sdk.parsers import (
    parse_bonuses,
    parse_message_counters,
    parse_messages,
)


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/game.php"):
        self.text = text
        self.url = url
        self.status_code = 200


def _make_client() -> NewStarClient:
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    return client


def test_parse_messages(fixture_html):
    page = parse_messages(fixture_html("messages_view.html"))

    assert len(page) == 4
    assert page.page == 1
    assert page.pages == [1]
    assert page.has_next_page is False

    transport = page.messages[0]
    assert transport.id == 3067802
    assert transport.category == 5
    assert transport.category_name == "运输报告"
    assert transport.sender == "舰队司令部"
    assert transport.time is not None
    assert transport.time.year == 2026
    assert transport.time.hour == 15
    assert [str(coords) for coords in transport.coordinates] == ["[3:454:8]", "[3:505:8]"]
    assert "星际合金" in transport.body

    admin = page.messages[-1]
    assert admin.category == 1
    assert admin.category_name == "玩家消息"
    assert admin.sender == "管理员"


def test_parse_messages_unread_flag():
    html = (
        '<div class="head_row_msg">'
        '<div id="message_1" class="message_head mes_unread">'
        '<div class="message_time">2026年11月13日, 15:25:59</div>'
        '<div class="message_title"><span class="message_recipient_name">系统</span></div>'
        "</div>"
        '<div class="messages_body"><div class="message_text">hello</div></div>'
        "</div>"
    )
    page = parse_messages(html)
    assert page.messages[0].unread is True


def test_parse_messages_empty_fragment():
    page = parse_messages("<div class='messagestable'></div>")
    assert page.messages == []
    assert page.pages == [1]


def test_parse_message_counters():
    html = (
        '<div class="ach_menu"><ul>'
        '<li><a href="#" id="mes_0">间谍报告 <span id="unread_0">2</span>/'
        '<span id="total_0">5</span></a></li>'
        '<li><a href="#" id="mes_15">探险报告 <span id="unread_15">0</span>/'
        '<span id="total_15">12</span></a></li>'
        "</ul></div>"
    )
    counters = parse_message_counters(html)

    assert counters[0].unread == 2
    assert counters[0].total == 5
    assert counters[15].total == 12
    assert counters[15].category_name == "探险报告"


def test_parse_bonuses(fixture_html):
    bonuses = parse_bonuses(fixture_html("infobonus.html"))

    assert len(bonuses) == 9
    assert bonuses.percent("合金采集加成") == pytest.approx(3.0)
    assert bonuses.multiplier("合金采集加成") == pytest.approx(1.03)
    assert bonuses.percent("全资源生产加成") == pytest.approx(0.0)
    assert bonuses.multiplier("不存在") == 1.0
    assert "探险队上限" in bonuses
    assert bonuses.as_dict()["水晶采集加成"] == pytest.approx(1.03)


@pytest.mark.parametrize(
    "value, expected",
    [
        (100, 100),
        ("100", 100),
        ("all", 100),
        ("expedition", 15),
        ("探险报告", 15),
        ("sys", 4),
        ("outbox", 999),
    ],
)
def test_resolve_message_category(value, expected):
    assert resolve_message_category(value) == expected


def test_resolve_message_category_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_message_category("nope")
    with pytest.raises(ValueError):
        resolve_message_category(4242)


def test_client_get_messages_uses_ajax_endpoint(fixture_html, monkeypatch):
    client = _make_client()
    seen = {}

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(fixture_html("messages_view.html"))

    monkeypatch.setattr(client, "_request", fake_request)
    page = client.get_messages("expedition", page=2)

    assert seen["page"] == "messages"
    assert seen["mode"] == "view"
    assert seen["messcat"] == "15"
    assert seen["site"] == "2"
    assert seen["ajax"] == "1"
    assert page.category == 15
    assert page.category_name == "探险报告"
    assert page.page == 2


def test_client_get_bonuses(fixture_html, monkeypatch):
    client = _make_client()

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        assert (params or {}).get("page") == "infobonus"
        return FakeResponse(fixture_html("infobonus.html"))

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.get_bonuses().percent("合金采集加成") == pytest.approx(3.0)


def test_client_get_message_counters(monkeypatch):
    client = _make_client()
    html = (
        '<a id="mes_3">战斗报告 <span id="unread_3">1</span>/'
        '<span id="total_3">4</span></a>'
    )

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        assert (params or {}).get("page") == "messages"
        return FakeResponse(html)

    monkeypatch.setattr(client, "_request", fake_request)
    counters = client.get_message_counters()
    assert counters[3].unread == 1
    assert counters[3].total == 4
