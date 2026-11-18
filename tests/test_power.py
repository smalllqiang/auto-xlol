"""Tests for the power-balance ratio planner (production control write path)."""

from types import SimpleNamespace

import pytest

from autoplay.power import apply_ratio_plan, plan_power_response
from newstar_sdk.exceptions import ActionRejected


def _report(net, ratios, buildings=None):
    return SimpleNamespace(
        resources={
            911: SimpleNamespace(
                total=net,
                buildings=buildings
                if buildings is not None
                else {1: -118_000.0, 2: -89_500.0, 4: 271_000.0},
            )
        },
        ratios=dict(ratios),
    )


def test_surplus_power_needs_no_change():
    plan = plan_power_response(_report(392_000, {1: 100, 2: 100, 4: 100}))

    assert plan.changes == {}
    assert plan.net_power == 392_000
    assert "不需要降耗" in plan.reason
    assert [item["element_id"] for item in plan.consumers] == [1, 2]


def test_deficit_lowers_the_largest_consumer():
    plan = plan_power_response(_report(-1_000, {1: 100, 2: 100, 4: 100}))

    assert plan.changes == {1: 90}
    assert plan.before == {1: 100}
    # 118,000/h * 10% of the ratio back.
    assert plan.projected_net == pytest.approx(10_800)
    assert "合金冶炼厂" in plan.reason
    assert "100% 降到 90%" in plan.reason


def test_skips_consumers_already_at_zero():
    plan = plan_power_response(
        _report(-500, {1: 0, 2: 100, 4: 100}, buildings={1: -118_000.0, 2: -89_500.0})
    )

    assert plan.changes == {2: 90}


def test_no_change_when_everything_is_at_zero():
    plan = plan_power_response(
        _report(-500, {1: 0, 2: 0}, buildings={1: -118_000.0, 2: -89_500.0})
    )

    assert plan.changes == {}
    assert "没有可继续下调" in plan.reason


def test_missing_energy_column_is_reported():
    report = SimpleNamespace(resources={}, ratios={1: 100})

    plan = plan_power_response(report)

    assert plan.changes == {}
    assert "没有幽能数据" in plan.reason


class FakeProductionClient:
    def __init__(self, ratios):
        self.ratios = dict(ratios)
        self.calls = []

    def set_production_ratios(self, changes):
        self.calls.append(dict(changes))
        self.ratios.update(changes)
        return SimpleNamespace(ratios=dict(self.ratios))


def test_preview_does_not_post():
    plan = plan_power_response(_report(-1_000, {1: 100, 2: 100}))
    client = FakeProductionClient({1: 100, 2: 100})

    result = apply_ratio_plan(client, plan, dry_run=True)

    assert result.status == "preview"
    assert result.changes == {1: 90}
    assert client.calls == []


def test_apply_requires_allow_list():
    plan = plan_power_response(_report(-1_000, {1: 100, 2: 100}))
    client = FakeProductionClient({1: 100, 2: 100})

    with pytest.raises(ActionRejected) as info:
        apply_ratio_plan(client, plan, dry_run=False)
    assert info.value.before_post is True
    assert client.calls == []


def test_apply_rejects_elements_outside_the_allow_list():
    plan = plan_power_response(_report(-1_000, {1: 100, 2: 100}))
    client = FakeProductionClient({1: 100, 2: 100})

    with pytest.raises(ActionRejected) as info:
        apply_ratio_plan(client, plan, dry_run=False, allowed=[2])
    assert info.value.before_post is True
    assert client.calls == []


def test_apply_verifies_the_readback():
    plan = plan_power_response(_report(-1_000, {1: 100, 2: 100}))
    client = FakeProductionClient({1: 100, 2: 100})

    result = apply_ratio_plan(client, plan, dry_run=False, allowed=[1])

    assert result.status == "applied"
    assert result.before == {1: 100}
    assert result.after == {1: 90}
    assert client.calls == [{1: 90}]


def test_nothing_to_do_is_skipped_without_posting():
    plan = plan_power_response(_report(500, {1: 100}))
    client = FakeProductionClient({1: 100})

    result = apply_ratio_plan(client, plan, dry_run=False, allowed=[1])

    assert result.status == "skipped"
    assert client.calls == []
