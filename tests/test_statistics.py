import pytest

from newstar_sdk.client import NewStarClient
from newstar_sdk.models import StatisticsBoard
from newstar_sdk.parsers import parse_statistics


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/game.php?page=statistics"):
        self.text = text
        self.url = url
        self.status_code = 200


def test_parse_statistics_players(fixture_html):
    board = parse_statistics(fixture_html("statistics.html"))

    assert board.who == 1
    assert board.sort == 1
    assert board.range == 1
    assert len(board.rows) == 2

    first = board.rows[0]
    assert first.rank == 1
    assert first.name == "PlayerOne"
    assert first.score == 210_975_746_268
    assert first.player_id == 1204
    assert first.alliance == "ALLY-A"
    assert first.alliance_id == 69
    assert first.tags == ["深空", "帝制"]
    assert first.status == "强"

    second = board.rows[1]
    assert second.name == "玩家二"
    assert second.score == 1_234_567
    assert second.status == "弱"


def test_parse_statistics_empty_page():
    board = parse_statistics("<html><body>no table</body></html>")
    assert board.rows == []
    assert board.who == 1


def test_get_statistics_posts_filter_fields(fixture_html):
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    captured = {}

    def fake_game_page(page, **kwargs):
        captured["page"] = page
        captured.update(kwargs)
        return FakeResponse(fixture_html("statistics.html"))

    client._game_page = fake_game_page  # type: ignore[method-assign]
    board = client.get_statistics(who=2, sort=3, range=1)

    assert captured["page"] == "statistics"
    assert captured["method"] == "POST"
    assert captured["data"] == {"who": 2, "type": 3, "range": 1}
    assert isinstance(board, StatisticsBoard)


@pytest.mark.parametrize(
    "kwargs",
    [{"who": 3}, {"sort": 0}, {"sort": 6}, {"range": 0}],
)
def test_get_statistics_rejects_bad_filters(kwargs):
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    with pytest.raises(ValueError):
        client.get_statistics(**kwargs)
