import json
from types import SimpleNamespace

from autoplay import dispatch_expedition
from autoplay.expedition import ExpeditionGroup, ExpeditionReport
from newstar_sdk.exceptions import NewStarError


def _report(groups=None, target="3:505:16"):
    return ExpeditionReport(
        expedition_tech=1,
        slots=1,
        active_expeditions=0,
        free_slots=1,
        target_slots=1,
        current_score=4.0,
        target_score=500_000.0,
        required_score=500_000.0,
        score_gap=499_996.0,
        target=target,
        available_ships={202: 1},
        ship_names={202: "轻型运输舰"},
        fleet_groups=list(groups or []),
        can_send=True,
    )


class FakeClient:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def preview_fleet(self, target, ships, *, mission, speed):
        self.calls.append(("preview", target, dict(ships), int(mission), speed))
        return SimpleNamespace(
            mission=int(mission),
            mission_name="探险",
            consumption=3.0,
            available_missions=[],
            stay_times=[1],
        )

    def send_fleet(self, target, ships, *, mission, speed, stay_time):
        self.calls.append(
            ("send", target, dict(ships), int(mission), speed, stay_time)
        )
        if self.fail:
            raise NewStarError("lost")
        return SimpleNamespace(
            mission=15,
            mission_name="探险",
            consumption=3.0,
            arrival_time="2026-11-14 17:00:00",
        )


def test_dispatch_defaults_to_preview():
    client = FakeClient()
    group = ExpeditionGroup(index=1, ships={202: 1}, score=4.0)
    result = dispatch_expedition(client, _report([group]))

    assert result.status == "preview"
    assert result.group == group
    assert client.calls == [("preview", "3:505:16", {202: 1}, 15, 10)]
    assert result.preview is not None


def test_dispatch_requires_allow_list_for_execute():
    client = FakeClient()
    group = ExpeditionGroup(index=1, ships={202: 1}, score=4.0)
    result = dispatch_expedition(client, _report([group]), dry_run=False)

    assert result.status == "skipped"
    assert "允许列表" in result.reason
    assert client.calls == []


def test_dispatch_executes_with_allow_list():
    client = FakeClient()
    group = ExpeditionGroup(index=1, ships={202: 1}, score=4.0)
    result = dispatch_expedition(
        client,
        _report([group]),
        dry_run=False,
        allowed_elements=[202],
        stay_time=2,
    )

    assert result.status == "dispatched"
    assert client.calls == [("send", "3:505:16", {202: 1}, 15, 10, 2)]
    assert result.dispatch is not None
    json.dumps(result.as_dict(), ensure_ascii=False)


def test_dispatch_rejects_disallowed_ship():
    client = FakeClient()
    group = ExpeditionGroup(index=1, ships={202: 1}, score=4.0)
    result = dispatch_expedition(
        client,
        _report([group]),
        dry_run=False,
        allowed_elements=[207],
    )

    assert result.status == "skipped"
    assert "未允许舰船" in result.reason
    assert client.calls == []


def test_dispatch_without_groups_is_skipped():
    client = FakeClient()
    result = dispatch_expedition(client, _report([]))

    assert result.status == "skipped"
    assert "没有第 1 支编队" in result.reason
    assert client.calls == []


def test_dispatch_reports_send_error():
    client = FakeClient(fail=True)
    group = ExpeditionGroup(index=1, ships={202: 1}, score=4.0)
    result = dispatch_expedition(
        client,
        _report([group]),
        dry_run=False,
        allowed_elements=[202],
    )

    assert result.status == "error"
    assert "NewStarError" in result.reason
    assert len(client.calls) == 1
