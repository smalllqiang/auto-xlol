import json

from autoplay.statistics import collect_statistics, load_statistics, write_statistics
from newstar_sdk.exceptions import TransportError
from newstar_sdk.models import StatisticsBoard, StatisticsRow


class FakeStatisticsClient:
    def get_statistics(self, who, sort, range):
        if who == 2:
            raise TransportError("alliance tab timed out")
        return StatisticsBoard(
            who=who,
            sort=sort,
            range=range,
            rows=[StatisticsRow(rank=1, name="PlayerOne", score=42)],
        )


def test_collect_statistics_keeps_partial_results():
    payload = collect_statistics(FakeStatisticsClient(), who_values=(1, 2), sort_values=(1,))

    assert "1:1" in payload["boards"]
    assert "2:1" not in payload["boards"]
    assert payload["errors"]
    assert payload["errors"][0]["board"] == "2:1"
    assert payload["boards"]["1:1"]["rows"][0]["name"] == "PlayerOne"


def test_collect_statistics_flags_empty_board():
    class EmptyClient:
        def get_statistics(self, who, sort, range):
            return StatisticsBoard(who=who, sort=sort, range=range, rows=[])

    payload = collect_statistics(EmptyClient(), who_values=(1,), sort_values=(1,))
    assert payload["errors"][0]["board"] == "1:1"
    assert "空榜" in payload["errors"][0]["error"]


def test_statistics_round_trip(tmp_path):
    path = tmp_path / "statistics.json"
    payload = {
        "schema": 1,
        "captured_at": "2026-11-14T09:00:00+00:00",
        "boards": {"1:1": {"rows": []}},
        "errors": [],
    }
    write_statistics(path, payload)
    assert load_statistics(path) == payload
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


def test_statistics_missing_file(tmp_path):
    assert load_statistics(tmp_path / "nope.json") == {}
