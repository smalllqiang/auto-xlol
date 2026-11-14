import json

from autoplay.battles import collect_battles, load_battles, write_battles
from newstar_sdk.exceptions import TransportError
from newstar_sdk.models import BattleHall, BattleRow


class FakeBattleClient:
    base_url = "https://example.test/"

    def get_battle_hall(self, order, sort):
        if order == "date":
            raise TransportError("date ordering timed out")
        return BattleHall(
            order=order,
            sort=sort,
            rows=[
                BattleRow(
                    rank=1,
                    attacker="PlayerOne",
                    defender="玩家二",
                    result="a",
                    date="2026年11月13日, 07:14:37",
                    time="2026-11-13T07:14:37",
                    units=42,
                    report_id="abc",
                )
            ],
        )


def test_collect_battles_keeps_partial_results():
    payload = collect_battles(FakeBattleClient())

    assert payload["base_url"] == "https://example.test/"
    assert "units:desc" in payload["boards"]
    assert "date:desc" not in payload["boards"]
    assert payload["errors"]
    assert payload["errors"][0]["board"] == "date:desc"
    row = payload["boards"]["units:desc"]["rows"][0]
    assert row["attacker"] == "PlayerOne"
    assert row["winner"] == "PlayerOne"
    assert row["report_id"] == "abc"


def test_collect_battles_flags_empty_board():
    class EmptyClient:
        def get_battle_hall(self, order, sort):
            return BattleHall(order=order, sort=sort, rows=[])

    payload = collect_battles(EmptyClient(), boards=(("units", "desc"),))
    assert payload["errors"][0]["board"] == "units:desc"
    assert "空榜" in payload["errors"][0]["error"]


def test_collect_battles_flags_ordering_mismatch():
    class MismatchClient:
        base_url = ""

        def get_battle_hall(self, order, sort):
            return BattleHall(
                order="units",
                sort="asc",
                rows=[BattleRow(rank=1, attacker="A", defender="B")],
            )

    payload = collect_battles(MismatchClient(), boards=(("units", "desc"),))
    assert payload["errors"][0]["board"] == "units:desc"
    assert "请求" in payload["errors"][0]["error"]


def test_battles_round_trip(tmp_path):
    path = tmp_path / "battles.json"
    payload = {
        "schema": 1,
        "captured_at": "2026-11-14T09:00:00+00:00",
        "base_url": "https://example.test/",
        "boards": {"units:desc": {"rows": []}},
        "errors": [],
    }
    write_battles(path, payload)
    assert load_battles(path) == payload
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


def test_battles_missing_file(tmp_path):
    assert load_battles(tmp_path / "nope.json") == {}
