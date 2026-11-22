"""Offline tests for the colonize script tick loop (fake game client)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoplay.colonize import (
    ColonizeRange,
    ColonizeState,
    InflightColony,
    PlanetFilter,
    PlanetRecord,
)
from newstar_sdk.exceptions import GameError
from newstar_sdk.models import (
    ControlReport,
    Coordinates,
    FleetDispatch,
    FleetInfo,
    FleetShips,
    FleetSlots,
    FleetTable,
    GalaxySlot,
    GalaxyView,
    Overview,
    PlanetAbandon,
    PlanetInfo,
    PlanetSummary,
    Resources,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "colonize.py"


def _module():
    name = "colonize_script"
    if name in sys.modules:
        return sys.modules[name]
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec: the script defines dataclasses and theirs fields
    # are resolved through ``sys.modules[cls.__module__]``.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fake game client
# ---------------------------------------------------------------------------


def _free_view(galaxy: int, system: int, positions) -> GalaxyView:
    slots = []
    for position in range(1, 16):
        actions = {7: "殖民"} if position in positions else {}
        slots.append(
            GalaxySlot(
                position=position,
                coordinates=Coordinates(galaxy, system, position),
                actions=actions,
            )
        )
    return GalaxyView(galaxy=galaxy, system=system, slots=slots)


def _occupancy_view(galaxy: int, system: int, occupied, own) -> GalaxyView:
    slots = []
    for position in range(1, 16):
        if position in own:
            slots.append(
                GalaxySlot(
                    position=position,
                    planet_id=10000 + position,
                    planet_name="母星",
                    player_name="TestUser",
                    coordinates=Coordinates(galaxy, system, position),
                    is_own=True,
                    actions={3: "运输"},
                )
            )
        elif position in occupied:
            slots.append(
                GalaxySlot(
                    position=position,
                    planet_id=20000 + position,
                    planet_name="别人的球",
                    player_name="Other",
                    coordinates=Coordinates(galaxy, system, position),
                    actions={3: "运输"},
                )
            )
        else:
            slots.append(
                GalaxySlot(
                    position=position,
                    coordinates=Coordinates(galaxy, system, position),
                    actions={7: "殖民"},
                )
            )
    return GalaxyView(galaxy=galaxy, system=system, slots=slots)


class FakeClient:
    def __init__(
        self,
        *,
        planets,
        ships: int = 3,
        deuterium: float = 1_000_000,
        slots: FleetSlots | None = None,
        fleets=(),
        galaxies=None,
        colony_slots=(1, 11),
        infos=None,
        send_errors=(),
    ) -> None:
        self.username = "TestUser"
        self.planets = list(planets)
        self.ships = int(ships)
        self.deuterium = float(deuterium)
        self.slots = slots or FleetSlots(active=0, max_slots=11)
        self.fleets = list(fleets)
        self.galaxies = dict(galaxies or {})
        self.colony_slots = colony_slots
        self.infos = dict(infos or {})
        self.send_errors = list(send_errors)
        self.sent = []
        self.recalled = []
        self.abandoned = []
        self.control_reads = 0
        self._current = self.planets[0].id if self.planets else None
        self._overview = None

    # -- plumbing --------------------------------------------------------

    @property
    def planet_id(self):
        return self._current

    @property
    def last_overview(self):
        return self._overview

    def _make_overview(self, current_id):
        current = next(
            (item for item in self.planets if item.id == current_id),
            self.planets[0] if self.planets else None,
        )
        overview = Overview(
            username="TestUser",
            server_time=datetime(2026, 11, 22, 12, 0, 0),
            resources=Resources(
                amounts={901: 10**6, 902: 10**6, 903: self.deuterium},
                capacities={903: 10**7},
            ),
            planet=PlanetInfo(
                id=current.id if current else None,
                name=current.name if current else "",
                coordinates=current.coordinates if current else None,
            ),
            planets=list(self.planets),
        )
        self._overview = overview
        return overview

    def get_overview(self):
        return self._make_overview(self._current)

    def select_planet(self, planet_id):
        if not any(item.id == int(planet_id) for item in self.planets):
            raise GameError(f"planet {planet_id} not found")
        self._current = int(planet_id)
        self._make_overview(self._current)
        info = self.infos.get(int(planet_id)) or PlanetInfo(
            id=int(planet_id),
            name="殖民地",
            coordinates=next(
                item.coordinates for item in self.planets if item.id == int(planet_id)
            ),
        )
        return info

    def get_fleet_table(self):
        return FleetTable(
            fleets=list(self.fleets),
            slots=self.slots,
            available={208: FleetShips(id=208, name="殖民舰", count=self.ships)},
        )

    def get_control(self):
        self.control_reads += 1
        return ControlReport(planets=[], colony_slots=self.colony_slots)

    def get_galaxy(self, galaxy, system):
        return self.galaxies[(int(galaxy), int(system))]

    def send_fleet(self, target, ships, mission=7, *, speed=10):
        if self.send_errors:
            error = self.send_errors.pop(0)
            if error is not None:
                raise error
        self.sent.append((str(target), dict(ships)))
        self.ships = max(0, self.ships - int(dict(ships).get(208, 0)))
        return FleetDispatch(
            mission=7,
            mission_name="殖民",
            target_coordinates=Coordinates.parse(str(target)),
            start_time="2026-11-22 13:00",
            arrival_time="2026-11-22 14:00",
            ships={208: 1},
        )

    def recall_fleet(self, fleet_id):
        self.recalled.append(int(fleet_id))
        return []

    def abandon_planet(self, planet_id, password=None):
        self.abandoned.append(int(planet_id))
        return PlanetAbandon(planet_id=int(planet_id), ok=True, message="行星已放弃")

    def close(self):
        return None


def _state(tmp_path: Path, box="1-1:2-2:1-15", *, limit=5, where=()) -> ColonizeState:
    return ColonizeState(
        path=tmp_path / "colonize_state.json",
        box=ColonizeRange.parse(box),
        filters=PlanetFilter.parse(where),
        user_limit=limit,
    )


def _refuse_state(tmp_path: Path) -> ColonizeState:
    return ColonizeState(
        path=tmp_path / "colonize_state.json",
        box=ColonizeRange.parse("1-1:2-2:1-15"),
        filters=PlanetFilter.parse([], accept=False),
        user_limit=0,
    )


def _args(tmp_path: Path, *extra: str):
    return _module().parse_args(
        [
            "--range",
            "1:2:1 1:2:15",
            "--stop-file",
            str(tmp_path / "STOP"),
            *extra,
        ]
    )


def _home() -> PlanetSummary:
    return PlanetSummary(id=10001, name="母星", coordinates=Coordinates(1, 1, 8))


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_sends_colony_ships_until_ships_or_limit_run_out(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=3,
        galaxies={(1, 2): _free_view(1, 2, range(1, 16))},
    )
    state = _state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is False
    assert len(client.sent) == 3
    assert all(mission_ships == {208: 1} for _, mission_ships in client.sent)
    assert len(state.inflight) == 3
    assert result.ships == 0
    assert state.scan_complete is True


def test_preview_scans_without_sending_or_mutating_the_ledger(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=3,
        galaxies={(1, 2): _free_view(1, 2, range(1, 16))},
    )
    state = _state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=True)

    assert client.sent == []
    assert client.abandoned == []
    assert result.planned
    assert state.inflight == []
    assert state.kept == []


def test_send_failure_marks_the_target_and_continues(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=3,
        galaxies={(1, 2): _free_view(1, 2, range(1, 16))},
        send_errors=[GameError("Target planet already exists")],
    )
    state = _state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is False
    # Three colony ships: one target was taken, the next three sends worked.
    assert len(client.sent) == 3
    assert len(state.failed) == 1
    assert "已被占" in state.failed[0].note


def test_a_hard_send_failure_stops_the_run(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=3,
        galaxies={(1, 2): _free_view(1, 2, range(1, 16))},
        send_errors=[GameError("Not enough deuterium available.")],
    )
    state = _state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is True
    assert result.code == "send_failed"
    assert "deuterium" in result.reason
    # The target stays queued so the next run can try it again.
    assert client.sent == []


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


def test_a_new_colony_is_kept_when_it_matches(tmp_path):
    colony = PlanetSummary(id=30001, name="殖民地", coordinates=Coordinates(1, 2, 5))
    client = FakeClient(
        planets=[_home(), colony],
        ships=0,
        infos={
            30001: PlanetInfo(
                id=30001,
                fields_max=273,
                temp_max=50,
                temp_min=10,
                coordinates=Coordinates(1, 2, 5),
            )
        },
    )
    state = _state(tmp_path, where=("面积>260",))
    state.colony_used = 1
    state.colony_max = 11
    state.inflight.append(
        InflightColony(
            target="1:2:5", fleet_id=43, missing_since=1.0, managed=True
        )
    )
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert state.kept_count == 1
    assert state.kept[0].area == 273
    assert state.kept[0].planet_id == 30001
    # The colony-slot counter is re-read after the ledger changed.
    assert client.control_reads >= 2
    assert state.inflight == []
    # No ships left and nothing in flight -> the run stops.
    assert result.stop is True
    assert result.code == "no_ships"


def test_a_failing_colony_is_abandoned_and_the_slot_returns(tmp_path):
    colony = PlanetSummary(id=30002, name="殖民地", coordinates=Coordinates(1, 2, 6))
    client = FakeClient(
        planets=[_home(), colony],
        ships=0,
        infos={
            30002: PlanetInfo(
                id=30002,
                fields_max=100,
                temp_max=50,
                temp_min=10,
                coordinates=Coordinates(1, 2, 6),
            )
        },
    )
    state = _state(tmp_path, where=("面积>260",))
    state.colony_used = 1
    state.colony_max = 11
    state.inflight.append(
        InflightColony(
            target="1:2:6", fleet_id=44, missing_since=1.0, managed=True
        )
    )
    args = _args(tmp_path)

    _module().run_tick(client, state, args, dry_run=False)

    assert client.abandoned == [30002]
    assert state.kept_count == 0
    assert len(state.discarded) == 1
    assert "面积 100" in state.discarded[0].note
    assert client.control_reads >= 2
    assert client.planet_id == 10001


def test_no_abandon_keeps_the_bad_planet_but_records_it(tmp_path):
    colony = PlanetSummary(id=30003, name="殖民地", coordinates=Coordinates(1, 2, 7))
    client = FakeClient(
        planets=[_home(), colony],
        ships=0,
        infos={
            30003: PlanetInfo(
                id=30003,
                fields_max=100,
                temp_max=50,
                temp_min=10,
                coordinates=Coordinates(1, 2, 7),
            )
        },
    )
    state = _state(tmp_path, where=("面积>260",))
    state.inflight.append(
        InflightColony(
            target="1:2:7", fleet_id=45, missing_since=1.0, managed=True
        )
    )
    args = _args(tmp_path, "--no-abandon")

    _module().run_tick(client, state, args, dry_run=False)

    assert client.abandoned == []
    assert state.discarded[0].note.startswith("面积 100 不满足")
    assert "--no-abandon" in state.discarded[0].note


def test_a_vanished_fleet_without_a_planet_eventually_fails(tmp_path):
    client = FakeClient(planets=[_home()], ships=0)
    state = _state(tmp_path)
    state.inflight.append(
        InflightColony(
            target="1:2:8",
            fleet_id=46,
            missing_since=time.time() - 1000,
            managed=True,
        )
    )
    args = _args(tmp_path)

    _module().run_tick(client, state, args, dry_run=False)

    assert state.inflight == []
    assert len(state.failed) == 1
    assert "仍无新星球" in state.failed[0].note


def test_a_recently_missing_fleet_is_still_waited_for(tmp_path):
    client = FakeClient(planets=[_home()], ships=0)
    state = _state(tmp_path)
    state.inflight.append(
        InflightColony(
            target="1:2:9", fleet_id=47, missing_since=None, managed=True
        )
    )
    state.scan_complete = True
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is False
    assert result.code == "waiting_ships"
    assert len(state.inflight) == 1
    assert state.failed == []


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------


def test_the_cap_recalls_fleets_still_on_their_way(tmp_path):
    fleet = FleetInfo(
        id=43,
        mission="殖民",
        mission_id=7,
        target_coordinates=Coordinates(1, 2, 3),
        start_time="2026-11-22 13:00",
        target_time="2026-11-22 14:00",
    )
    client = FakeClient(planets=[_home()], ships=0, fleets=[fleet])
    state = _state(tmp_path, limit=2)
    state.kept.append(PlanetRecord(coordinates="1:2:4", area=300))
    state.kept.append(PlanetRecord(coordinates="1:2:5", area=310))
    state.inflight.append(
        InflightColony(target="1:2:3", fleet_id=43, arrive_at=time.time() + 600, managed=True)
    )
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is True
    assert result.code == "limit"
    assert client.recalled == [43]
    assert state.inflight == []


def test_a_foreign_colony_fleet_is_never_evaluated_or_abandoned(tmp_path):
    colony = PlanetSummary(id=30009, name="殖民地", coordinates=Coordinates(1, 2, 9))
    client = FakeClient(planets=[_home(), colony], ships=0)
    state = _state(tmp_path)
    state.inflight.append(
        InflightColony(
            target="1:2:9", fleet_id=50, missing_since=time.time() - 1000, managed=False
        )
    )
    args = _args(tmp_path)

    _module().run_tick(client, state, args, dry_run=False)

    assert client.abandoned == []
    assert state.kept == []
    assert state.discarded == []
    # The foreign fleet is dropped once its result cannot concern us anymore.
    assert state.inflight == []


def test_no_colony_ship_stops_the_run(tmp_path):
    client = FakeClient(planets=[_home()], ships=0)
    state = _state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is True
    assert result.code == "no_ships"


def test_no_deuterium_stops_the_run(tmp_path):
    client = FakeClient(planets=[_home()], ships=5, deuterium=0)
    state = _state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is True
    assert result.code == "no_fuel"


def test_a_fully_scanned_range_stops_before_checking_ships(tmp_path):
    client = FakeClient(planets=[_home()], ships=0)
    state = _state(tmp_path)
    state.scan_next = state.scan_total
    state.scan_complete = True
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is True
    assert result.code == "range_explored"


def test_occupied_positions_are_not_queued(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=3,
        galaxies={(1, 2): _occupancy_view(1, 2, occupied={2, 3}, own={8})},
    )
    state = _state(tmp_path, limit=1)
    args = _args(tmp_path)

    _module().run_tick(client, state, args, dry_run=False)

    assert len(client.sent) == 1
    target = client.sent[0][0]
    assert target not in {"1:2:2", "1:2:3", "1:2:8"}
    assert len(state.inflight) == 1


def test_unknown_colony_limit_without_a_user_cap_refuses_to_run(tmp_path):
    client = FakeClient(planets=[_home()], ships=3)
    client.colony_slots = None
    state = _state(tmp_path, limit=0)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert result.stop is True
    assert result.code == "no_limit"
    assert client.sent == []


def test_execute_needs_the_colony_ship_in_the_allow_list(tmp_path):
    module = _module()
    requires = module.parse_args(["--range", "1:2:3 1:2:4", "--execute"])
    assert module._check_args(requires, 208) == 2

    allowed = module.parse_args(
        ["--range", "1:2:3 1:2:4", "--execute", "--allow", "208"]
    )
    assert module._check_args(allowed, 208) is None


class FakeNotifier:
    def __init__(self) -> None:
        self.messages = []

    def send_text(self, text):
        self.messages.append(text)
        return SimpleNamespace(ok=True, skipped=False, error="")

    def close(self):
        return None


def test_the_loop_reports_only_start_and_stop_and_writes_status(tmp_path):
    client = FakeClient(planets=[_home()], ships=0)
    state = _state(tmp_path)
    args = _args(
        tmp_path,
        "--status-file",
        str(tmp_path / "colonize.json"),
        "--journal",
        str(tmp_path / "journal.jsonl"),
    )
    notifier = FakeNotifier()

    code = _module().run_loop(client, state, args, notifier)

    assert code == 0
    assert len(notifier.messages) == 2
    assert all(message.startswith("[殖民]") for message in notifier.messages)
    assert "开始" in notifier.messages[0]
    assert "停止" in notifier.messages[1]
    status = json.loads((tmp_path / "colonize.json").read_text(encoding="utf-8"))
    assert status["state"] == "stopped"
    assert status["last_code"] == "no_ships"
    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "no_ships"


def test_accept_false_colonizes_and_abandons_everything(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=2,
        galaxies={(1, 2): _free_view(1, 2, range(1, 16))},
    )
    state = _refuse_state(tmp_path)
    args = _args(tmp_path)

    first = _module().run_tick(client, state, args, dry_run=False)

    assert first.stop is False
    assert len(client.sent) == 2
    assert len(state.inflight) == 2
    assert state.kept == []
    sent_targets = [target for target, _ in client.sent]

    # Both fleets arrive and create colonies; the filter rejects both.
    colonies = [
        PlanetSummary(
            id=40000 + index,
            name="殖民地",
            coordinates=Coordinates.parse(target),
        )
        for index, target in enumerate(sent_targets)
    ]
    client.planets = [_home(), *colonies]
    client.infos = {
        colony.id: PlanetInfo(
            id=colony.id,
            fields_max=300,
            temp_max=50,
            temp_min=10,
            coordinates=colony.coordinates,
        )
        for colony in colonies
    }
    client.fleets = []

    second = _module().run_tick(client, state, args, dry_run=False)

    assert client.abandoned == [colony.id for colony in colonies]
    assert state.kept == []
    assert len(state.discarded) == 2
    assert all("accept=false" in item.note for item in state.discarded)
    assert state.inflight == []
    # Out of colony ships and nothing in flight -> the run stops.
    assert second.stop is True
    assert second.code == "no_ships"


def test_accept_false_cannot_be_combined_with_no_abandon(tmp_path):
    module = _module()
    for extra in (["--no-accept"], ["--accept", "false"], ["--accept", "0"]):
        args = module.parse_args(["--range", "1:2:1 1:2:15", *extra, "--no-abandon"])
        assert module._check_args(args, 208) == 2


def test_accept_flag_parses_true_and_false(tmp_path):
    module = _module()
    assert module._accept_value(module.parse_args(["--range", "1:2:1 1:2:15"]))
    assert module._accept_value(
        module.parse_args(["--range", "1:2:1 1:2:15", "--accept", "true"])
    )
    assert not module._accept_value(
        module.parse_args(["--range", "1:2:1 1:2:15", "--accept", "false"])
    )
    assert not module._accept_value(
        module.parse_args(["--range", "1:2:1 1:2:15", "--no-accept"])
    )


def test_the_old_accept_false_early_exit_is_gone(tmp_path):
    client = FakeClient(
        planets=[_home()],
        ships=1,
        galaxies={(1, 2): _free_view(1, 2, range(1, 16))},
    )
    state = _refuse_state(tmp_path)
    args = _args(tmp_path)

    result = _module().run_tick(client, state, args, dry_run=False)

    assert client.sent  # it does start colonizing instead of stopping
    assert result.code != "no_ships"


@pytest.mark.parametrize(
    "spec",
    ["1:2:3 4:5:6", "1-4:2-5:3-6", "4:5:6 1:2:3"],
)
def test_cli_accepts_both_range_forms(spec, tmp_path):
    args = _module().parse_args(["--range", spec])
    box = ColonizeRange.parse(args.range_spec)
    assert box.as_text() == "1-4:2-5:3-6"
