from types import SimpleNamespace

from autoplay import run_expedition_cycle
from autoplay.expedition import ExpeditionGroup, ExpeditionReport, ShipRecommendation
from newstar_sdk.exceptions import NewStarError


def _recommendation(**overrides):
    values = {
        "element_id": 202,
        "name": "轻型运输舰",
        "score_per_ship": 4.0,
        "needed": 10,
        "total_cost": {901: 2000.0, 902: 2000.0},
        "affordable": True,
        "requirements_met": True,
        "requirements": [],
        "blockers": [],
    }
    values.update(overrides)
    return ShipRecommendation(**values)


def _report(free_slots, group=None, target="3:505:16", recommendation=None):
    return ExpeditionReport(
        expedition_tech=2,
        slots=2,
        active_expeditions=2 - free_slots,
        free_slots=free_slots,
        target_slots=free_slots,
        current_score=4.0 if group is not None else 0.0,
        target_score=500_000.0,
        required_score=500_000.0 * free_slots,
        score_gap=max(0.0, 500_000.0 * free_slots - (4.0 if group else 0.0)),
        target=target,
        available_ships={202: 1} if group is not None else {},
        ship_names={202: "轻型运输舰"},
        fleet_groups=[group] if group is not None else [],
        recommendation=recommendation,
        can_send=free_slots > 0 and group is not None,
    )


class SequenceLoader:
    def __init__(self, reports):
        self.reports = list(reports)

    def __call__(self, client):
        return self.reports.pop(0)


class FakeClient:
    def __init__(self, *, fail_on=0):
        self.calls = []
        self.sends = 0
        self.fail_on = fail_on
        self.build_calls = []
        self.wait_calls = []

    def build_ships(self, ships):
        self.build_calls.append(dict(ships))
        return [{"element_id": next(iter(ships)), "amount": next(iter(ships.values()))}]

    def get_ship_queue(self):
        return []

    def wait_for_ship_queue(self, timeout, poll_interval):
        self.wait_calls.append((timeout, poll_interval))
        return []

    def preview_fleet(self, target, ships, *, mission, speed):
        self.calls.append(("preview", target, dict(ships), int(mission), speed))
        return SimpleNamespace(
            mission=15,
            mission_name="探险",
            consumption=3.0,
            available_missions=[],
            stay_times=[1],
        )

    def send_fleet(self, target, ships, *, mission, speed, stay_time):
        self.sends += 1
        self.calls.append(("send", target, dict(ships), int(mission), speed, stay_time))
        if self.fail_on and self.sends == self.fail_on:
            raise NewStarError("send failed")
        return SimpleNamespace(
            mission=15,
            mission_name="探险",
            consumption=3.0,
            arrival_time="2026-11-14 17:00:00",
        )


def _group(index=1):
    return ExpeditionGroup(index=index, ships={202: 1}, score=4.0)


def test_cycle_preview_uses_first_group_only():
    client = FakeClient()
    loader = SequenceLoader([_report(2, _group())])
    result = run_expedition_cycle(client, report_loader=loader)

    assert result.status == "dry_run"
    assert len(result.dispatches) == 1
    assert client.calls == [("preview", "3:505:16", {202: 1}, 15, 10)]


def test_cycle_execute_requires_allow_list():
    client = FakeClient()
    loader = SequenceLoader([_report(2, _group())])
    result = run_expedition_cycle(
        client,
        dry_run=False,
        report_loader=loader,
    )

    assert result.status == "skipped"
    assert "允许列表" in result.reason
    assert client.calls == []


def test_cycle_dispatches_up_to_max_dispatch():
    client = FakeClient()
    loader = SequenceLoader(
        [
            _report(2, _group(1)),
            _report(1, _group(2)),
        ]
    )
    result = run_expedition_cycle(
        client,
        dry_run=False,
        allowed_elements=[202],
        max_dispatch=2,
        report_loader=loader,
    )

    assert result.status == "dispatched"
    assert len(result.dispatches) == 2
    assert [call[0] for call in client.calls] == ["send", "send"]


def test_cycle_stops_when_slots_are_full():
    client = FakeClient()
    loader = SequenceLoader([_report(0, None)])
    result = run_expedition_cycle(
        client,
        dry_run=False,
        allowed_elements=[202],
        report_loader=loader,
    )

    assert result.status == "skipped"
    assert "没有空闲" in result.reason
    assert client.calls == []


def test_cycle_partial_when_second_send_fails():
    client = FakeClient(fail_on=2)
    loader = SequenceLoader(
        [
            _report(2, _group(1)),
            _report(1, _group(2)),
        ]
    )
    result = run_expedition_cycle(
        client,
        dry_run=False,
        allowed_elements=[202],
        max_dispatch=2,
        report_loader=loader,
    )

    assert result.status == "partial"
    assert [item.status for item in result.dispatches] == ["dispatched", "error"]
    assert len(client.calls) == 2


def test_cycle_auto_builds_then_dispatches():
    client = FakeClient()
    loader = SequenceLoader(
        [
            _report(1, None, recommendation=_recommendation()),
            _report(1, _group()),
        ]
    )
    result = run_expedition_cycle(
        client,
        dry_run=False,
        allowed_elements=[202],
        max_dispatch=1,
        auto_build=True,
        build_count=1,
        report_loader=loader,
    )

    assert result.status == "dispatched"
    assert result.build is not None and result.build.status == "built"
    assert client.build_calls == [{202: 1}]
    assert client.wait_calls == [(300.0, 2)]
    assert [call[0] for call in client.calls] == ["send"]


def test_cycle_auto_build_skips_when_requirements_unmet():
    client = FakeClient()
    loader = SequenceLoader(
        [
            _report(
                1,
                None,
                recommendation=_recommendation(requirements_met=False),
            )
        ]
    )
    result = run_expedition_cycle(
        client,
        dry_run=False,
        allowed_elements=[202],
        auto_build=True,
        report_loader=loader,
    )

    assert result.status == "skipped"
    assert result.build is not None
    assert result.build.status == "skipped"
    assert client.build_calls == []
    assert client.calls == []
