"""Offline tests for the colonization strategy layer."""

import json
from pathlib import Path

import pytest

from autoplay.colonize import (
    STATUS_FREE,
    ColonizeRange,
    ColonizeState,
    Condition,
    InflightColony,
    IntRange,
    PlanetFilter,
    PlanetRecord,
    available_colony_lanes,
    classify_system,
    effective_limit,
    game_colony_remaining,
    sort_candidates,
)
from newstar_sdk.models import Coordinates, FleetSlots
from newstar_sdk.parsers import parse_galaxy

FIXTURES = Path(__file__).parent / "fixtures"


def _galaxy():
    return parse_galaxy(
        (FIXTURES / "galaxy.html").read_text(encoding="utf-8"),
        current_planet_id=10001,
        username="TestUser",
    )


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------


def test_two_corners_are_normalised_in_any_order():
    box = ColonizeRange.parse("4:5:6 1:2:3")

    assert box.galaxy == IntRange(1, 4)
    assert box.system == IntRange(2, 5)
    assert box.position == IntRange(3, 6)
    assert box.as_text() == "1-4:2-5:3-6"
    assert box.system_count == 16
    assert box.slot_count == 64


def test_three_range_form_matches_the_corner_form():
    assert ColonizeRange.parse("1-4:2-5:3-6") == ColonizeRange.parse("1:2:3 4:5:6")


def test_bracketed_corners_and_a_single_position():
    assert ColonizeRange.parse("[1:2:3] [2:3:4]").as_text() == "1-2:2-3:3-4"
    single = ColonizeRange.parse("3:505:8")
    assert single.system_count == 1
    assert single.positions() == [8]
    assert Coordinates(3, 505, 8) in single
    assert Coordinates(3, 505, 9) not in single


def test_system_cursor_walks_galaxy_major():
    box = ColonizeRange.parse("1-2:98-99:1-15")

    assert box.system_count == 4
    assert [box.system_at(index) for index in range(4)] == [
        (1, 98),
        (1, 99),
        (2, 98),
        (2, 99),
    ]
    with pytest.raises(IndexError):
        box.system_at(4)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "1:2", "1:2:3:4", "a:b:c", "1-0:2:3", "100:1:1", "1:1000:1", "1:1:16"],
)
def test_bad_ranges_are_rejected(text):
    with pytest.raises(ValueError):
        ColonizeRange.parse(text)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_conditions_accept_chinese_and_english_aliases():
    assert Condition.parse("面积>260") == Condition("area", ">", 260.0)
    assert Condition.parse("最高温度<=50") == Condition("temp_max", "<=", 50.0)
    assert Condition.parse("最低温度>-50") == Condition("temp_min", ">", -50.0)
    assert Condition.parse("area>=200") == Condition("area", ">=", 200.0)
    assert Condition.parse("tmin<= -10") == Condition("temp_min", "<=", -10.0)


def test_full_width_comparators_are_normalised():
    assert Condition.parse("面积≥260") == Condition("area", ">=", 260.0)
    assert Condition.parse("面积≤100") == Condition("area", "<=", 100.0)
    assert Condition.parse("面积＞260") == Condition("area", ">", 260.0)
    assert Condition.parse("最高温度＜50") == Condition("temp_max", "<", 50.0)


def test_unknown_field_and_missing_comparator_are_rejected():
    with pytest.raises(ValueError, match="未知条件字段"):
        Condition.parse("重力>1")
    with pytest.raises(ValueError, match="无法解析条件"):
        Condition.parse("面积260")


def test_filter_checks_only_the_given_fields():
    flt = PlanetFilter.parse(["面积>260"])

    assert flt.evaluate(area=300, temp_max=-200, temp_min=-240) == (
        True,
        "满足全部条件",
    )
    ok, reason = flt.evaluate(area=99)
    assert ok is False
    assert "面积 99" in reason
    # Nothing configured: everything is accepted.
    assert PlanetFilter.parse([]).evaluate(area=1) == (True, "未指定条件（全部接受）")


def test_filter_rejects_everything_when_accept_is_false():
    ok, reason = PlanetFilter.parse([], accept=False).evaluate(area=999)

    assert ok is False
    assert "accept=false" in reason


def test_missing_value_fails_a_configured_condition():
    ok, reason = PlanetFilter.parse(["最高温度>10"]).evaluate(area=300, temp_max=None)

    assert ok is False
    assert "最高温度 未知" in reason


def test_filter_and_record_round_trip_json():
    flt = PlanetFilter.parse(["面积>260", "最低温度>-60"], accept=True)
    payload = json.dumps(flt.as_dict(), ensure_ascii=False)
    restored = PlanetFilter.from_dict(json.loads(payload))

    assert restored == flt
    assert restored.as_text() == "面积>260、最低温度>-60"


# ---------------------------------------------------------------------------
# Lanes and the acceptance cap
# ---------------------------------------------------------------------------


def test_colony_lanes_reserve_the_expedition_share():
    slots = FleetSlots(active=2, max_slots=11, active_expeditions=1, max_expeditions=3)

    # 11 - 2 in flight - (3 - 1 station slots still reserved for expeditions)
    assert available_colony_lanes(slots) == 7
    assert available_colony_lanes(slots, reserve_expeditions=False) == 9
    full = FleetSlots(active=11, max_slots=11, active_expeditions=3, max_expeditions=3)
    assert available_colony_lanes(full) == 0


def test_game_remaining_comes_from_the_colony_slots():
    assert game_colony_remaining(1, 11) == 10
    assert game_colony_remaining(11, 11) == 0
    assert game_colony_remaining(12, 11) == 0
    assert game_colony_remaining(None, 11) is None


def test_effective_limit_is_the_smaller_cap():
    assert effective_limit(5, 1, 11) == 5
    assert effective_limit(40, 1, 11) == 10
    assert effective_limit(0, 1, 11) == 10  # 0 = no personal cap
    assert effective_limit(0, None, None) is None
    assert effective_limit(3, None, None) == 3


def test_effective_limit_counts_planets_already_kept_by_this_run():
    # One planet colonized and kept: the game counter already includes it, so
    # the total allowance must not shrink by one.
    assert effective_limit(0, 2, 11, kept=1) == 10
    assert effective_limit(0, 2, 11, kept=0) == 9
    assert effective_limit(5, 2, 11, kept=1) == 5
    # Never exceed the absolute colony cap.
    assert effective_limit(0, 11, 11, kept=11) == 11


# ---------------------------------------------------------------------------
# Galaxy scan
# ---------------------------------------------------------------------------


def test_galaxy_rows_are_classified():
    decisions = {
        item.coordinates: item
        for item in classify_system(
            _galaxy(),
            ColonizeRange.parse("1:1:1 1:1:15"),
            own_coordinates=["1:1:8"],
            username="TestUser",
        )
    }

    assert decisions["1:1:1"].status == STATUS_FREE
    assert decisions["1:1:1"].area == 96
    assert decisions["1:1:7"].status == "unavailable"
    assert decisions["1:1:7"].note == "被遗弃的殖民地"
    assert decisions["1:1:8"].status == "own"
    assert len(decisions) == 15


def test_positions_outside_the_box_are_ignored_and_inflight_targets_skipped():
    decisions = classify_system(
        _galaxy(),
        ColonizeRange.parse("1-1:1-1:4-6"),
        own_coordinates=["1:1:8"],
        inflight_targets=["1:1:5"],
    )

    assert sorted(item.coordinates for item in decisions) == [
        "1:1:4",
        "1:1:6",
    ]


def test_candidates_are_sorted_by_distance_from_the_departure_planet():
    ordered = sort_candidates(
        ["2:1:1", "1:9:1", "1:1:5", "1:1:2"],
        Coordinates(1, 1, 8),
    )

    assert ordered == ["1:1:5", "1:1:2", "1:9:1", "2:1:1"]


# ---------------------------------------------------------------------------
# State / timetable
# ---------------------------------------------------------------------------


def _state(tmp_path: Path) -> ColonizeState:
    return ColonizeState(
        path=tmp_path / "colonize_state.json",
        departure_id=10001,
        departure_name="母星",
        departure_coordinates="1:1:8",
        box=ColonizeRange.parse("1-2:1-3:1-15"),
        filters=PlanetFilter.parse(["面积>260"]),
        user_limit=5,
    )


def test_state_round_trips_through_disk(tmp_path):
    state = _state(tmp_path)
    state.scan_next = 3
    state.add_candidate("1:2:3")
    state.record_send(
        InflightColony(
            target="1:2:3",
            fleet_id=43,
            sent_at=100.0,
            arrive_at=200.0,
            managed=True,
        )
    )
    state.record_keep(
        PlanetRecord(coordinates="1:2:4", planet_id=1234, area=273, temp_max=50, temp_min=10)
    )
    state.record_discard(PlanetRecord(coordinates="1:2:5", area=120, note="面积不足"))
    state.record_failure(PlanetRecord(coordinates="1:2:6", note="目标已被占"))

    assert state.save() is True
    loaded = ColonizeState.load(state.path)

    assert loaded.load_error == ""
    assert loaded.box == state.box
    assert loaded.filters == state.filters
    assert loaded.scan_next == 3
    assert loaded.kept_count == 1
    assert loaded.kept[0].planet_id == 1234
    assert loaded.discarded[0].note == "面积不足"
    assert loaded.failed[0].coordinates == "1:2:6"
    assert loaded.inflight_targets() == ["1:2:3"]
    assert loaded.inflight[0].managed is True
    assert loaded.candidates == []


def test_state_reports_explored_only_when_nothing_is_pending(tmp_path):
    state = _state(tmp_path)
    assert state.explored is False

    state.scan_next = state.scan_total
    state.scan_complete = True
    assert state.explored is True

    state.add_candidate("1:2:3")
    assert state.explored is False
    state.pop_candidate()
    state.record_send(InflightColony(target="1:2:3"))
    assert state.explored is False
    state.drop_inflight("1:2:3")
    assert state.explored is True


def test_scan_reset_keeps_the_ledger(tmp_path):
    state = _state(tmp_path)
    state.scan_next = 4
    state.scan_complete = True
    state.add_candidate("1:2:3")
    state.record_keep(PlanetRecord(coordinates="1:1:9", area=300))

    state.reset_scan()

    assert (state.scan_next, state.scan_complete, state.candidates) == (0, False, [])
    assert state.kept_count == 1


def test_candidates_are_never_duplicated_or_re_queued(tmp_path):
    state = _state(tmp_path)
    assert state.add_candidate("1:2:3") is True
    assert state.add_candidate("1:2:3") is False
    state.record_keep(PlanetRecord(coordinates="1:2:4"))
    assert state.add_candidate("1:2:4") is False
    assert state.add_candidate("1:2:5") is True


def test_next_wake_prefers_the_earliest_arrival(tmp_path):
    state = _state(tmp_path)
    state.scan_complete = True
    state.record_send(InflightColony(target="1:2:3", arrive_at=1_000.0))
    state.record_send(InflightColony(target="1:2:4", arrive_at=2_000.0))

    wake = state.next_wake(500.0, arrive_grace=30.0, retry_delay=60.0, max_sleep=900.0)

    assert wake == 1_030.0


def test_next_wake_retries_while_scanning_or_waiting_without_eta(tmp_path):
    state = _state(tmp_path)
    state.record_send(InflightColony(target="1:2:3", arrive_at=None))

    wake = state.next_wake(500.0, arrive_grace=30.0, retry_delay=60.0, max_sleep=900.0)

    assert wake == 560.0


def test_next_wake_falls_back_to_the_safety_poll(tmp_path):
    state = _state(tmp_path)
    state.scan_next = state.scan_total
    state.scan_complete = True

    wake = state.next_wake(500.0, arrive_grace=30.0, retry_delay=60.0, max_sleep=900.0)

    assert wake == 1_400.0


def test_broken_state_file_is_reported_but_does_not_raise(tmp_path):
    path = tmp_path / "colonize_state.json"
    path.write_text("{not json", encoding="utf-8")

    state = ColonizeState.load(path)

    assert state.load_error
    assert state.kept_count == 0
