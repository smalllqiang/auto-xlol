import pytest

from newstar_sdk.client import NewStarClient
from newstar_sdk.models import BattleHall
from newstar_sdk.parsers import parse_battle_hall


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.url = "https://example.test/game.php?page=battleHall"
        self.status_code = 200


def test_parse_battle_hall(fixture_html):
    hall = parse_battle_hall(fixture_html("battle_hall.html"))

    assert hall.order == "units"
    assert hall.sort == "desc"
    assert len(hall.rows) == 4

    first = hall.rows[0]
    assert first.rank == 1
    assert first.attacker == "AttackerA"
    assert first.defender == "DefenderB"
    assert first.result == "r"
    assert first.winner == "DefenderB"
    assert first.loser == "AttackerA"
    assert first.date == "2026年11月13日, 07:14:37"
    assert first.time == "2026-11-13T07:14:37"
    assert first.units == 581_500
    assert first.report_id == "c342df899f9cf23f2e453f86b824e280"

    second = hall.rows[1]
    assert second.result == "a"
    assert second.winner == "PlayerOne"
    assert second.loser == "玩家二"
    assert second.units == 0

    third = hall.rows[2]
    assert third.result == ""
    assert third.winner is None and third.loser is None
    assert third.attacker == "AttackerC" and third.defender == "DefenderD"

    fourth = hall.rows[3]
    assert fourth.attacker == "TestUser"
    assert fourth.units == 12_345


def test_parse_battle_hall_empty_page():
    hall = parse_battle_hall("<html><body>no table</body></html>")
    assert hall.rows == []
    assert hall.order == "units"
    assert hall.sort == "desc"


def test_parse_battle_hall_infers_date_ascending():
    html = """
    <table class="tabstatistica">
      <tr><th><a href="game.php?page=battleHall&order=date&sort=DESC"
                 style="font-weight:bold;">战役发生日期</a></th></tr>
      <tr class="classificabarra">
        <td class="classstat1">1</td>
        <td class="classstat3"><a href="game.php?page=raport&amp;raport=abc">
          <span style="color:#00FF00">A</span> VS <span style="color:#FF0000">B</span>
        </a></td>
        <td class="classstat8">13.11.2026 07:14:37</td>
        <td class="classstat10">1.234</td>
      </tr>
    </table>
    """
    hall = parse_battle_hall(html)

    assert hall.order == "date"
    assert hall.sort == "asc"
    assert hall.rows[0].time == "2026-11-13T07:14:37"
    assert hall.rows[0].units == 1_234
    assert hall.rows[0].report_id == "abc"


def test_get_battle_hall_passes_ordering(fixture_html):
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    captured = {}

    def fake_game_page(page, **kwargs):
        captured["page"] = page
        captured.update(kwargs)
        return FakeResponse(fixture_html("battle_hall.html"))

    client._game_page = fake_game_page  # type: ignore[method-assign]
    hall = client.get_battle_hall(order="date", sort="desc")

    assert captured["page"] == "battleHall"
    assert captured["params"] == {"order": "date", "sort": "DESC"}
    assert isinstance(hall, BattleHall)


@pytest.mark.parametrize("kwargs", [{"order": "score"}, {"sort": "up"}])
def test_get_battle_hall_rejects_bad_ordering(kwargs):
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    with pytest.raises(ValueError):
        client.get_battle_hall(**kwargs)
