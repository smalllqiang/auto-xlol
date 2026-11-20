from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from autoplay.expedition_scheduler import (
    ExpeditionPlan,
    ExpeditionTimetable,
    GameClock,
    ScheduledSlot,
    fill_expedition_slots,
    parse_composition,
    parse_duration,
    parse_game_time,
)
from newstar_sdk.exceptions import ActionRejected
from newstar_sdk.models import (
    Coordinates,
    FleetDispatch,
    FleetInfo,
    FleetPreview,
    FleetShips,
    ResearchInfo,
)


def _wall_in(seconds: float) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).strftime(
        "%Y年%m月%d日, %H:%M:%S"
    )


def _fleet(*, target: str = "3:505:16", target_time: str = "") -> FleetInfo:
    return FleetInfo(
        mission="探险",
        mission_id=15,
        target_coordinates=Coordinates(3, 505, 16),
        start_time=_wall_in(3600),
        target_time=target_time or _wall_in(7200),
        arrival_time="01h 59m 00s",
        ships={"战列舰": 8621},
        returning=False,
    )


class FakeClient:
    def __init__(
        self,
        *,
        tech: int = 16,
        fleets=None,
        available: int = 100_000,
        fail=None,
    ):
        self.tech = tech
        self.fleets = list(fleets or [])
        self.available = available
        self.fail = fail
        self.sent = []
        self.previewed = []

    def get_research(self):
        return [ResearchInfo(id=124, name="探险技术", level=self.tech)]

    def get_fleets(self):
        return list(self.fleets)

    def get_available_ships(self):
        return {207: FleetShips(id=207, name="战列舰", count=self.available)}

    def preview_fleet(self, target, ships, mission=None, speed=10):
        self.previewed.append((target, ships, mission, speed))
        return FleetPreview(
            mission=15,
            mission_name="探险",
            target_coordinates=Coordinates(3, 505, 16),
            consumption=1234,
            stay_times=[1, 2],
            available_missions=[],
        )

    def send_fleet(self, target, ships, mission=None, speed=10, stay_time=0):
        if self.fail is not None:
            raise self.fail
        dispatch = FleetDispatch(
            mission=15,
            mission_name="探险",
            target_coordinates=Coordinates(3, 505, 16),
            arrival_time=_wall_in(7200),
        )
        self.sent.append(
            {
                "target": target,
                "ships": dict(ships),
                "mission": mission,
                "speed": speed,
                "stay_time": stay_time,
            }
        )
        self.available -= sum(int(value) for value in ships.values())
        self.fleets.append(_fleet(target_time=_wall_in(7200)))
        return dispatch


def _plan() -> ExpeditionPlan:
    return ExpeditionPlan(target="3:505:16", ships={207: 8621}, speed=10, stay_time=1)


def test_parse_composition_default_and_multiple():
    assert parse_composition("207=8621") == {207: 8621}
    assert parse_composition("207:10, 203=5") == {207: 10, 203: 5}
    assert parse_composition("207x2 203*3") == {207: 2, 203: 3}
    with pytest.raises(ValueError):
        parse_composition("999=1")
    with pytest.raises(ValueError):
        parse_composition("")


def test_parse_game_time_accepts_game_formats():
    assert parse_game_time("2026年11月21日, 02:00:00") == datetime(
        2026, 11, 21, 2, 0, 0
    )
    assert parse_game_time("2026年11月21日 02:00") == datetime(2026, 11, 21, 2, 0)
    assert parse_game_time("2026-11-21 02:00:00") == datetime(2026, 11, 21, 2, 0, 0)
    assert parse_game_time("21.11.2026 02:00:00") == datetime(2026, 11, 21, 2, 0, 0)
    assert parse_game_time("-") is None
    assert parse_game_time(None) is None


def test_parse_duration_accepts_english_and_chinese_units():
    assert parse_duration("01h 02m 03s") == 3723
    assert parse_duration("1d 02h 00m 00s") == 93600
    assert parse_duration("1天 02小时 03分 04秒") == 93600 + 184
    assert parse_duration("-") is None


def test_scheduled_slot_uses_target_time_as_home_return():
    clock = GameClock()
    slot = ScheduledSlot.from_fleet(_fleet(), clock, plan_target="3:505:16")
    assert slot.target == "[3:505:16]"
    assert slot.ours is True
    assert slot.expected_return_at == pytest.approx(
        datetime.now().timestamp() + 7200, abs=3
    )
    assert slot.remaining == "01h 59m 00s"


def test_scheduled_slot_falls_back_to_remaining_duration():
    clock = GameClock()
    fleet = _fleet()
    fleet.target_time = "-"
    fleet.arrival_time = "02h 00m 00s"
    slot = ScheduledSlot.from_fleet(fleet, clock)
    assert slot.expected_return_at == pytest.approx(
        datetime.now().timestamp() + 7200, abs=3
    )


def test_timetable_round_trip(tmp_path):
    path = tmp_path / "timetable.json"
    timetable = ExpeditionTimetable(
        path=path,
        planet_id=22828,
        planet_label="母星",
        planet_coordinates="[3:505:8]",
        target="3:505:16",
        ships={207: 8621},
        max_slots=4,
        tech_level=16,
        next_check_at=5000.0,
    )
    timetable.record_send(
        ScheduledSlot(
            target="3:505:16",
            ships={"战列舰": 8621},
            expected_return_at=6000.0,
            expected_return_game="2026年11月21日, 02:00:00",
        ),
        now=1000.0,
    )
    assert timetable.save() is True

    loaded = ExpeditionTimetable.load(path)
    assert loaded.planet_id == 22828
    assert loaded.planet_label == "母星"
    assert loaded.target == "3:505:16"
    assert loaded.ships == {207: 8621}
    assert loaded.max_slots == 4
    assert loaded.tech_level == 16
    assert loaded.next_check_at == 5000.0
    assert len(loaded.slots) == 1
    assert loaded.slots[0].expected_return_at == 6000.0
    assert loaded.history[-1]["event"] == "sent"


def test_timetable_load_tolerates_bad_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    timetable = ExpeditionTimetable.load(path)
    assert timetable.load_error
    assert timetable.slots == []


def test_next_wake_is_return_plus_delay():
    timetable = ExpeditionTimetable(
        next_check_at=5000.0,
        slots=[ScheduledSlot(expected_return_at=2000.0)],
    )
    # The return itself is the next event when it comes before the safety poll.
    assert timetable.next_wake(1000.0, return_delay=5, retry_delay=60, max_sleep=2000) == 2005.0
    # A return sooner than retry_delay keeps its 5-second delay.
    soon = ExpeditionTimetable(slots=[ScheduledSlot(expected_return_at=1020.0)])
    assert soon.next_wake(1000.0, return_delay=5, retry_delay=60, max_sleep=2000) == 1025.0
    # The safety poll preempts a distant return; the next tick then waits again.
    assert timetable.next_wake(1000.0, return_delay=5, retry_delay=60, max_sleep=900) == 1900.0
    assert timetable.next_wake(1000.0, return_delay=5, retry_delay=60, max_sleep=100) == 1100.0


def test_next_wake_retries_after_a_past_return():
    timetable = ExpeditionTimetable(
        next_check_at=5000.0,
        slots=[ScheduledSlot(expected_return_at=900.0)],
    )
    assert timetable.next_wake(1000.0, return_delay=5, retry_delay=60, max_sleep=900) == 1060.0


def test_fill_sends_until_all_free_lanes_are_used():
    client = FakeClient(tech=16)
    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=2,
        active=0,
    )
    assert fill.status == "dispatched"
    assert fill.free == 0
    assert len(fill.sent) == 2
    assert len(client.sent) == 2
    assert all(slot.expected_return_at is not None for slot in fill.sent)


def test_fill_reports_ship_shortage_after_partial_fill():
    client = FakeClient(tech=16, available=8621)
    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=2,
        active=0,
    )
    assert fill.status == "partial"
    assert len(fill.sent) == 1
    assert fill.free == 1
    assert "舰船不足" in fill.reason
    assert len(client.sent) == 1


def test_fill_skips_when_all_lanes_busy():
    client = FakeClient(tech=16, fleets=[_fleet(), _fleet(), _fleet(), _fleet()])
    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=4,
        active=4,
    )
    assert fill.status == "skipped"
    assert fill.free == 0
    assert client.sent == []


def test_fill_dry_run_previews_without_sending():
    client = FakeClient(tech=16)
    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=4,
        active=1,
        dry_run=True,
    )
    assert fill.status == "dry_run"
    assert fill.free == 3
    assert fill.preview is not None
    assert client.previewed
    assert client.sent == []


def test_fill_reports_send_failure():
    client = FakeClient(tech=16, fail=ActionRejected("航道已满"))
    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=4,
        active=0,
    )
    assert fill.status == "error"
    assert "ActionRejected" in fill.reason


def test_fill_honors_stop_check_before_the_first_send():
    client = FakeClient(tech=16)
    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=4,
        active=0,
        stop_check=lambda: True,
    )
    assert fill.status == "skipped"
    assert "急停" in fill.reason
    assert client.sent == []


def test_fill_honors_stop_check_between_sends():
    client = FakeClient(tech=16)
    state = {"calls": 0}

    def stop_check():
        state["calls"] += 1
        return state["calls"] > 1

    fill = fill_expedition_slots(
        client,
        plan=_plan(),
        clock=GameClock(),
        max_slots=2,
        active=0,
        stop_check=stop_check,
    )
    assert fill.status == "partial"
    assert len(fill.sent) == 1
    assert "急停" in fill.reason
    assert len(client.sent) == 1
