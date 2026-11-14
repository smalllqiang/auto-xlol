import json

from autoplay import Action, build_recommended_ships
from autoplay.expedition import ExpeditionReport, ShipRecommendation
from newstar_sdk.exceptions import ActionRejected


def _report(*, requirements_met=True, blockers=None):
    recommendation = ShipRecommendation(
        element_id=202,
        name="轻型运输舰",
        score_per_ship=4.0,
        needed=100,
        total_cost={901: 2000.0, 902: 2000.0},
        affordable=True,
        requirements_met=requirements_met,
        requirements=[] if requirements_met else ["星际船坞 等级 2 (0/2)"],
        blockers=list(blockers or []),
    )
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
        target="3:505:16",
        available_ships={202: 1},
        ship_names={202: "轻型运输舰"},
        recommendation=recommendation,
        can_send=True,
    )


class FakeClient:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def build_ships(self, ships):
        self.calls.append(dict(ships))
        if self.fail:
            raise ActionRejected("shipyard rejected")
        return [{"element_id": next(iter(ships)), "amount": next(iter(ships.values()))}]

    def get_ship_queue(self):
        return []


def test_ship_build_defaults_to_dry_run():
    client = FakeClient()
    result = build_recommended_ships(client, _report(), count=2)

    assert result.status == "dry_run"
    assert result.count == 2
    assert client.calls == []


def test_ship_build_requires_allow_list_for_execute():
    client = FakeClient()
    result = build_recommended_ships(client, _report(), dry_run=False)

    assert result.status == "skipped"
    assert "允许列表" in result.reason
    assert client.calls == []


def test_ship_build_executes_with_allow_list_and_cap():
    client = FakeClient()
    result = build_recommended_ships(
        client,
        _report(),
        count=5,
        max_count=2,
        dry_run=False,
        allowed_elements=[202],
    )

    assert result.status == "built"
    assert result.count == 2
    assert client.calls == [{202: 2}]
    json.dumps(result.as_dict(), ensure_ascii=False)


def test_ship_build_rejects_unmet_requirements():
    client = FakeClient()
    result = build_recommended_ships(
        client,
        _report(requirements_met=False),
        dry_run=False,
        allowed_elements=[202],
    )

    assert result.status == "skipped"
    assert "前置未满足" in result.reason
    assert client.calls == []


def test_ship_build_rejects_remaining_blockers():
    client = FakeClient()
    result = build_recommended_ships(
        client,
        _report(
            blockers=[
                Action(
                    category="blocker",
                    kind="building",
                    element_id=21,
                    name="星际船坞",
                    current_level=0,
                    target_level=1,
                )
            ]
        ),
        dry_run=False,
        allowed_elements=[202],
    )

    assert result.status == "skipped"
    assert "前置动作" in result.reason
    assert client.calls == []


def test_ship_build_reports_error():
    client = FakeClient(fail=True)
    result = build_recommended_ships(
        client,
        _report(),
        dry_run=False,
        allowed_elements=[202],
    )

    assert result.status == "error"
    assert "ActionRejected" in result.reason
    assert len(client.calls) == 1
