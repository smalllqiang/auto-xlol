from autoplay import compute_plan, execute_plan
from autoplay.executor import ExecutionResult
from newstar_sdk.parsers import (
    parse_buildings,
    parse_overview,
    parse_production,
    parse_research,
)


class FakeClient:
    def __init__(self, building_queue=None, research_queue=None):
        self.building_queue = list(building_queue or [])
        self.research_queue = list(research_queue or [])
        self.calls = []

    def get_building_queue(self):
        return list(self.building_queue)

    def get_research_queue(self):
        return list(self.research_queue)

    def upgrade_building(self, element, levels=1):
        self.calls.append(("building", element, levels))
        return [{"element_id": element, "target_level": 1}]

    def research(self, element, levels=1):
        self.calls.append(("research", element, levels))
        return [{"element_id": element, "target_level": 1}]


def _plan(fixture_html, resources=None):
    buildings = parse_buildings(fixture_html("buildings.html"))
    research = parse_research(fixture_html("research.html"))
    production = parse_production(fixture_html("production.html"))
    amounts = resources or parse_overview(fixture_html("overview.html")).resources.amounts
    return compute_plan(buildings, research, production, amounts)


def test_dry_run_does_not_touch_the_game(fixture_html):
    client = FakeClient()
    result = execute_plan(client, _plan(fixture_html), dry_run=True)

    assert isinstance(result, ExecutionResult)
    assert result.status == "dry_run"
    assert result.action is not None and result.action.name == "自律工厂"
    assert client.calls == []


def test_execute_building(fixture_html):
    client = FakeClient()
    result = execute_plan(
        client,
        _plan(fixture_html),
        dry_run=False,
        allowed_elements=[14],
    )

    assert result.status == "executed"
    assert client.calls == [("building", 14, 1)]


def test_busy_building_queue_falls_through_to_research(fixture_html):
    client = FakeClient(building_queue=[object()])
    result = execute_plan(
        client,
        _plan(fixture_html),
        dry_run=False,
        allowed_elements=[14, 108],
    )

    assert result.status == "executed"
    assert result.action is not None and result.action.name == "计算机技术"
    assert client.calls == [("research", 108, 1)]


def test_allow_list_blocks_everything(fixture_html):
    client = FakeClient()
    result = execute_plan(
        client,
        _plan(fixture_html),
        dry_run=False,
        allowed_elements=[999],
    )

    assert result.status == "skipped"
    assert "不在允许列表" in result.reason
    assert client.calls == []


def test_execute_requires_allow_list(fixture_html):
    client = FakeClient()
    result = execute_plan(client, _plan(fixture_html), dry_run=False)

    assert result.status == "skipped"
    assert "允许列表" in result.reason
    assert client.calls == []


def test_unaffordable_actions_are_skipped(fixture_html):
    plan = _plan(fixture_html, resources={901: 0, 902: 0, 903: 0})
    client = FakeClient()
    result = execute_plan(client, plan, dry_run=False, allowed_elements=[14])

    assert result.status == "skipped"
    assert "资源不足" in result.reason
    assert client.calls == []


def test_max_cost_filters_action(fixture_html):
    plan = _plan(fixture_html)
    client = FakeClient()
    result = execute_plan(
        client,
        plan,
        dry_run=False,
        allowed_elements=[14],
        max_cost_equivalent=1.0,
    )

    assert result.status == "skipped"
    assert "单步预算" in result.reason


def test_error_is_reported_not_retried(fixture_html):
    class Failing(FakeClient):
        def upgrade_building(self, element, levels=1):
            self.calls.append(("building", element, levels))
            from newstar_sdk.exceptions import ActionRejected

            raise ActionRejected("queue full")

    client = Failing()
    result = execute_plan(
        client,
        _plan(fixture_html),
        dry_run=False,
        allowed_elements=[14],
    )

    assert result.status == "error"
    assert "ActionRejected" in result.reason
    assert len(client.calls) == 1


def test_lab_upgrade_skipped_while_researching(fixture_html):
    from autoplay.executor import RESEARCH_LAB_ID

    client = FakeClient(research_queue=[object()])
    result = execute_plan(
        client,
        _plan(fixture_html),
        dry_run=False,
        allowed_elements=[RESEARCH_LAB_ID],
    )

    assert result.status == "skipped"
    assert "实验室暂不可升级" in result.reason
    assert client.calls == []
