from newstar_sdk.client import NewStarClient
from newstar_sdk.parsers import parse_universe_records


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/game.php?page=records"):
        self.text = text
        self.url = url
        self.status_code = 200


def test_parse_universe_records(fixture_html):
    records = parse_universe_records(fixture_html("universe_records.html"))

    assert records.updated_text == "2026年11月14日, 19:00:05"
    assert records.updated_at == "2026-11-14T19:00:05"
    assert [section.label for section in records.sections] == [
        "建筑",
        "科研",
        "舰只",
        "行星防御",
    ]
    assert [len(section.entries) for section in records.sections] == [3, 2, 2, 2]
    assert len(records.entries) == 9

    first = records.entries[0]
    assert first.section == "建筑"
    assert first.item_id == 1
    assert first.name == "合金冶炼厂"
    assert first.server == 48
    assert first.own == 21
    assert first.percent == 44
    assert first.holders == ["TestUser"]

    multi = records.entries[2]
    assert multi.name == "帝国大学"
    assert multi.holders == ["玩家二", "PlayerOne", "PlayerThree"]

    ship = records.entries[5]
    assert ship.section == "舰只"
    assert ship.name == "间谍探针"
    assert ship.server == 1_170_243_908
    assert ship.own == 0
    assert ship.percent == 0

    transport = records.entries[6]
    assert transport.server == 4_268_000
    assert transport.own == 7_002


def test_parse_universe_records_skips_unrecorded_rows(fixture_html):
    records = parse_universe_records(fixture_html("universe_records.html"))
    names = [entry.name for entry in records.entries]

    # The page renders these rows without a holder/value block.
    assert "狂热者级护卫舰" not in names
    assert "惰性深渊物" not in names
    assert all(entry.holders for entry in records.entries)


def test_parse_universe_records_empty_page():
    records = parse_universe_records("<html><body>no records</body></html>")
    assert records.sections == []
    assert records.entries == []
    assert records.updated_at == ""


def test_parse_universe_records_without_server_class():
    html = """
    <div id="ally_content">
      <div class="gray_stripe">最后更新于:: 2026年11月14日, 19:00:05</div>
      <div id="u000">
        <div class="record_header">建筑</div>
        <div class="record_rows">
          <div class="record_name_utits"><a href="#">合金冶炼厂</a></div>
          <div class="record_made" data-tooltip-content="
            &lt;a href='#' onclick='return Dialog.Playercard(1);'&gt;TestUser&lt;/a&gt;">
          </div>
          <div class="record_count">
            <div class="record_text"><div>100</div></div>
            <div class="record_text"><div>25</div></div>
          </div>
        </div>
      </div>
    </div>
    """

    records = parse_universe_records(html)

    entry = records.sections[0].entries[0]
    assert entry.server == 100
    assert entry.own == 25
    assert entry.percent == 25
    assert entry.holders == ["TestUser"]


def test_get_universe_records_uses_records_page(fixture_html):
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    captured = {}

    def fake_game_page(page, **kwargs):
        captured["page"] = page
        captured.update(kwargs)
        return FakeResponse(fixture_html("universe_records.html"))

    client._game_page = fake_game_page  # type: ignore[method-assign]
    records = client.get_universe_records()

    assert captured["page"] == "records"
    assert "data" not in captured
    assert records.entries[0].name == "合金冶炼厂"
