"""CLI/default and planet-selection tests for the expedition farm script."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoplay.expedition_scheduler import ExpeditionPlan, ExpeditionTimetable, GameClock
from newstar_sdk.exceptions import SessionExpired
from newstar_sdk.models import (
    Coordinates,
    FleetShips,
    Overview,
    PlanetInfo,
    PlanetSummary,
)
from utils.notify import ENV_WEBHOOK

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "expedition_farm.py"


def _module():
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("expedition_farm_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_defaults_match_the_requested_behavior():
    args = _module().parse_args([])

    assert args.ships == "207=8621"
    assert args.check_hours == pytest.approx(10.0)
    assert args.return_delay == pytest.approx(5.0)
    assert args.speed == 10
    assert args.stay_time == 1
    assert args.execute is False
    assert args.once is False
    assert args.notify is False
    assert args.lock_file == "state/daemon.lock"


def test_execute_requires_allow_list_covering_the_composition():
    module = _module()

    assert module._check_args(module.parse_args(["--execute"]), {207: 8621}) == 2
    assert (
        module._check_args(
            module.parse_args(["--execute", "--allow", "203"]), {207: 8621}
        )
        == 2
    )
    assert (
        module._check_args(
            module.parse_args(["--execute", "--allow", "207"]), {207: 8621}
        )
        is None
    )


class _RecordingNotifier:
    def __init__(self):
        self.texts = []

    def send_text(self, text):
        self.texts.append(text)
        return SimpleNamespace(ok=True, skipped=False, error=None)


def test_notify_requires_both_flag_and_webhook(monkeypatch, tmp_path, capsys):
    module = _module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WEBHOOK, raising=False)

    assert module._make_notifier(module.parse_args([])) is None

    args = module.parse_args(["--notify"])
    assert module._make_notifier(args) is None
    assert ENV_WEBHOOK in capsys.readouterr().err

    monkeypatch.setenv(
        ENV_WEBHOOK, "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    )
    notifier = module._make_notifier(args)
    assert notifier is not None
    assert notifier.webhook.endswith("/test")
    notifier.close()


def test_report_prefixes_the_topic_and_swallows_errors(capsys):
    module = _module()
    notifier = _RecordingNotifier()

    module._report(notifier, "第 1/4 条航道")
    assert notifier.texts == ["[探险] 第 1/4 条航道"]

    class _Boom:
        def send_text(self, text):
            raise RuntimeError("boom")

    module._report(_Boom(), "x")
    assert "飞书上报异常" in capsys.readouterr().err


def test_resolve_planet_defaults_to_first_and_accepts_index_coordinates_and_id():
    module = _module()
    first = PlanetSummary(id=1, name="母星", coordinates=Coordinates(3, 505, 8))
    second = PlanetSummary(id=2, name="二星", coordinates=Coordinates(3, 506, 9))
    overview = Overview(
        planets=[first, second],
        planet=PlanetInfo(id=2, name="二星", coordinates=Coordinates(3, 506, 9)),
    )

    assert module.resolve_planet(overview, None) is first
    assert module.resolve_planet(overview, "1") is first
    assert module.resolve_planet(overview, "2") is second
    assert module.resolve_planet(overview, "3:506:9") is second
    assert module.resolve_planet(overview, "2") is second
    assert module.resolve_planet(overview, None, saved_id=1) is first
    with pytest.raises(ValueError):
        module.resolve_planet(overview, "9:9:9")


def test_resolve_target_defaults_to_the_sixteenth_position():
    module = _module()

    assert module.resolve_target(Coordinates(3, 505, 8), None) == "3:505:16"
    assert module.resolve_target(Coordinates(3, 505, 8), "4:4:4") == "4:4:4"
    with pytest.raises(ValueError):
        module.resolve_target(None, None)


class _OverviewClient:
    """Minimal client for one `run_tick` without touching the network."""

    def __init__(self):
        self.fleets = []
        self.sent = []
        self.tech = 16

    def get_overview(self):
        from datetime import datetime

        return Overview(
            server_time=datetime.now(),
            planets=[PlanetSummary(id=22828, name="母星", coordinates=Coordinates(3, 505, 8))],
            planet=PlanetInfo(
                id=22828, name="母星", coordinates=Coordinates(3, 505, 8)
            ),
        )

    def get_research(self):
        from newstar_sdk.models import ResearchInfo

        return [ResearchInfo(id=124, name="探险技术", level=self.tech)]

    def get_fleets(self):
        return list(self.fleets)

    def get_available_ships(self):
        return {207: FleetShips(id=207, name="战列舰", count=100_000)}

    def send_fleet(self, target, ships, mission=None, speed=10, stay_time=0):
        from datetime import datetime, timedelta

        from newstar_sdk.models import FleetDispatch, FleetInfo

        wall = (datetime.now() + timedelta(hours=2)).strftime(
            "%Y年%m月%d日, %H:%M:%S"
        )
        self.fleets.append(
            FleetInfo(
                mission="探险",
                mission_id=15,
                target_coordinates=Coordinates(3, 505, 16),
                target_time=wall,
                arrival_time="02h 00m 00s",
                ships={"战列舰": 8621},
            )
        )
        self.sent.append((target, dict(ships), speed, stay_time))
        return FleetDispatch(
            mission=15,
            mission_name="探险",
            target_coordinates=Coordinates(3, 505, 16),
            arrival_time=wall,
        )


def test_run_tick_fills_every_lane_and_records_expected_returns(tmp_path):
    module = _module()
    client = _OverviewClient()
    args = module.parse_args(["--timetable", str(tmp_path / "timetable.json")])
    timetable = module.ExpeditionTimetable(path=tmp_path / "timetable.json")

    tech, max_slots, slots, fill, next_wake, clock = module.run_tick(
        client,
        plan=module.ExpeditionPlan(
            target="3:505:16", ships={207: 8621}, speed=10, stay_time=1
        ),
        timetable=timetable,
        clock=module.GameClock(),
        planet_id=22828,
        args=args,
    )

    assert (tech, max_slots) == (16, 4)
    assert fill.status == "dispatched"
    assert len(client.sent) == 4
    assert len(timetable.slots) == 4
    assert all(slot.expected_return_at is not None for slot in timetable.slots)
    assert next_wake > 0

    tech, max_slots, slots, fill, next_wake, clock = module.run_tick(
        client,
        plan=module.ExpeditionPlan(
            target="3:505:16", ships={207: 8621}, speed=10, stay_time=1
        ),
        timetable=timetable,
        clock=clock,
        planet_id=22828,
        args=args,
    )
    assert fill.status == "skipped"
    assert len(client.sent) == 4
    assert len(timetable.slots) == 4


def test_run_once_reports_the_status_with_a_topic_prefix(tmp_path, capsys):
    module = _module()
    client = _OverviewClient()
    args = module.parse_args(
        [
            "--execute",
            "--allow",
            "207",
            "--timetable",
            str(tmp_path / "timetable.json"),
            "--status-file",
            str(tmp_path / "status.json"),
            "--journal",
            str(tmp_path / "journal.jsonl"),
            "--notify",
        ]
    )
    timetable = module.ExpeditionTimetable(path=tmp_path / "timetable.json")
    notifier = _RecordingNotifier()

    code = module.run_once(
        client,
        plan=module.ExpeditionPlan(
            target="3:505:16", ships={207: 8621}, speed=10, stay_time=1
        ),
        timetable=timetable,
        clock=module.GameClock(),
        planet_id=22828,
        planet_name="母星",
        planet_coordinates=Coordinates(3, 505, 8),
        args=args,
        notifier=notifier,
    )

    assert code == 0
    assert len(notifier.texts) == 1
    assert notifier.texts[0].startswith("[探险] 执行（单轮）")
    assert "星球      母星 [3:505:8]" in notifier.texts[0]


def test_run_loop_stops_on_session_expiry(monkeypatch, tmp_path, capsys):
    module = _module()
    plan = ExpeditionPlan(target="3:505:16", ships={207: 8621})
    timetable = ExpeditionTimetable(tech_level=1, max_slots=1)
    args = argparse.Namespace(
        stop_file=str(tmp_path / "STOP"),
        json=False,
        retry_delay=60.0,
        journal=str(tmp_path / "journal.jsonl"),
        status_file=str(tmp_path / "status.json"),
    )
    notifier = _RecordingNotifier()

    def fail_tick(*_args, **_kwargs):
        raise SessionExpired("the game session is no longer valid")

    monkeypatch.setattr(module, "run_tick", fail_tick)

    code = module.run_loop(
        object(),
        plan=plan,
        timetable=timetable,
        clock=GameClock(),
        planet_id=None,
        planet_name="test",
        planet_coordinates=None,
        args=args,
        notifier=notifier,
    )

    assert code == module.EXIT_SESSION_EXPIRED
    journal = [json.loads(line) for line in Path(args.journal).read_text().splitlines()]
    assert journal[-1]["kind"] == "session_error"
    assert "session.py import" in capsys.readouterr().err
    assert json.loads(Path(args.status_file).read_text())["state"] == "error"
    assert any(
        text.startswith("[探险] 会话不可用") for text in notifier.texts
    )
