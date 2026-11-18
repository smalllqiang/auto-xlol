"""Tests for the empire collector behind the dashboard (P2-3 groundwork)."""

import json
from pathlib import Path

import pytest

from autoplay.empire import collect_empire, load_empire, write_empire
from newstar_sdk.exceptions import TransportError
from newstar_sdk.parsers import parse_control

FIXTURES = Path(__file__).parent / "fixtures"


def _report():
    return parse_control((FIXTURES / "control.html").read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, report=None, error=None):
        self.report = report
        self.error = error

    def get_control(self):
        if self.error is not None:
            raise self.error
        return self.report


def test_collects_planets_and_slots():
    payload = collect_empire(FakeClient(_report()))

    assert payload["schema"] == 1
    assert payload["source"] == "game.php?page=control"
    assert payload["captured_at"]
    assert payload["colony_slots"] == [1, 9]
    assert len(payload["planets"]) == 1
    planet = payload["planets"][0]
    assert planet["name"] == "母星"
    assert planet["resources"]["901"] == 100_000_017_609
    assert payload["errors"] == []


def test_fetch_error_is_recorded_not_raised():
    payload = collect_empire(FakeClient(error=TransportError("timeout")))

    assert payload["planets"] == []
    assert payload["errors"][0]["section"] == "control"
    assert "TransportError" in payload["errors"][0]["error"]


def test_empty_panel_is_reported():
    payload = collect_empire(FakeClient(parse_control("<html></html>")))

    assert payload["planets"] == []
    assert payload["errors"][0]["section"] == "planets"


def test_write_then_load_round_trip(tmp_path):
    path = tmp_path / "state" / "empire.json"
    payload = collect_empire(FakeClient(_report()))

    write_empire(path, payload)
    loaded = load_empire(path)

    assert loaded["colony_slots"] == [1, 9]
    assert json.loads(path.read_text(encoding="utf-8"))["planets"][0]["name"] == "母星"
    # Atomic write leaves no temp file behind.
    assert [item.name for item in path.parent.iterdir()] == ["empire.json"]


def test_load_missing_or_broken_file_returns_empty(tmp_path):
    broken = tmp_path / "empire.json"
    broken.write_text("{not json", encoding="utf-8")

    assert load_empire(tmp_path / "absent.json") == {}
    assert load_empire(broken) == {}


@pytest.mark.parametrize("value", [[], "text", None])
def test_non_dict_json_is_ignored(tmp_path, value):
    path = tmp_path / "empire.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    assert load_empire(path) == {}
