from types import SimpleNamespace

import pytest

from autoplay.runner import (
    Facts,
    check,
    load_facts,
    parse_steps,
    run_steps,
)
from autoplay.tools import ToolResult, ToolSpec
from newstar_sdk.exceptions import TransportError


class FakeRegistry:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def specs(self):
        return [ToolSpec(name="step", description="", parameters={}, kind="write")]

    def call(self, name, args):
        self.calls.append((name, args))
        return self.results.pop(0)


class FactsClient:
    def __init__(self, level=0, queue=0, resources=None, ship_queue=0, ships=None, ships_error=None):
        self.level = level
        self.queue = queue
        self.ship_queue = ship_queue
        self.resources = resources or {901: 1000}
        self.ships = ships or {}
        self.ships_error = ships_error
        self.ship_reads = 0

    def get_shipyard(self, mode="fleet"):
        self.ship_reads += 1
        if self.ships_error is not None:
            raise self.ships_error
        return [
            SimpleNamespace(id=key, available=value) for key, value in self.ships.items()
        ]

    def get_buildings(self):
        return [SimpleNamespace(id=1, level=self.level)]

    def get_research(self):
        return [SimpleNamespace(id=124, level=self.level)]

    def get_building_queue(self):
        return [object()] * self.queue

    def get_research_queue(self):
        return []

    def get_ship_queue(self, mode="fleet"):
        return [object()] * self.ship_queue

    def get_overview(self):
        return SimpleNamespace(resources=SimpleNamespace(amounts=dict(self.resources)))


def _ok():
    return ToolResult(ok=True, status="ok", message="ok")


def test_check_condition_kinds():
    facts = Facts(
        levels={1: 3},
        research_levels={124: 9},
        queues={"building": 0},
        queues_ok=True,
        resources={901: 50},
    )

    assert check({"kind": "always"}, facts) == (True, "")
    assert check({"kind": "queue_empty", "queue": "building"}, facts)[0] is True
    assert check({"kind": "level_at_least", "element": 1, "level": 4}, facts)[0] is False
    assert check({"kind": "level_at_least", "element": 124, "level": 9}, facts)[0] is True
    assert check({"kind": "resources_at_least", "resources": {"901": 100}}, facts)[0] is False
    assert check({"kind": "phase", "phase": "expedition_prep"}, facts)[0] is False


def test_unknown_condition_fails_closed():
    holds, why = check({"kind": "no_such_condition"}, Facts())

    assert holds is False
    assert "未知条件" in why


def test_load_facts_reads_the_pages():
    facts = load_facts(FactsClient(level=3, queue=1))

    assert facts.level(1) == 3
    assert facts.queues["building"] == 1
    assert facts.phase == "pre_gravity"


def test_when_not_met_blocks_before_any_call():
    registry = FakeRegistry([])
    steps = parse_steps(
        [
            {
                "tool": "step",
                "args": {},
                "when": {"kind": "queue_empty", "queue": "building"},
                
            }
        ]
    )

    report = run_steps(registry, steps, client=FactsClient(queue=2))

    assert report.status == "blocked"
    assert report.stopped_at == 0
    assert registry.calls == []


def test_failed_step_stops_the_run():
    registry = FakeRegistry(
        [_ok(), ToolResult(ok=False, status="rejected", code="REJECTED", message="nope")]
    )
    steps = parse_steps([{"tool": "step", "args": {}}, {"tool": "step", "args": {}}])

    report = run_steps(registry, steps, client=FactsClient())

    assert report.status == "failed"
    assert report.stopped_at == 1
    assert len(registry.calls) == 2


def test_until_not_reached_is_unexpected_and_stops():
    registry = FakeRegistry([_ok()])
    steps = parse_steps(
        [{"tool": "step", "args": {}, "until": {"kind": "level_at_least", "element": 1, "level": 1}}]
    )

    report = run_steps(registry, steps, client=FactsClient(level=0))

    assert report.status == "unexpected"
    assert report.steps[-1].status == "unexpected"


def test_dry_run_steps_do_not_stop_the_plan():
    registry = FakeRegistry(
        [
            ToolResult(ok=True, status="dry_run", code="DRY_RUN"),
            ToolResult(ok=True, status="dry_run", code="DRY_RUN"),
        ]
    )
    steps = parse_steps([{"tool": "step", "args": {}}, {"tool": "step", "args": {}}])

    report = run_steps(registry, steps, client=FactsClient())

    assert report.status == "completed"
    assert len(registry.calls) == 2


def test_empty_plan_is_not_a_failure():
    report = run_steps(FakeRegistry([]), [], client=FactsClient())

    assert report.status == "empty"


def test_parse_steps_rejects_bad_shape():
    with pytest.raises(ValueError):
        parse_steps([{"args": {}}])
    with pytest.raises(ValueError):
        parse_steps([{"tool": "step", "args": "not-an-object"}])
    with pytest.raises(ValueError):
        parse_steps([{"tool": "step", "when": "nope"}])


def test_shipyard_queue_condition_is_really_read():
    registry = FakeRegistry([])
    steps = parse_steps(
        [{"tool": "step", "args": {}, "when": {"kind": "queue_empty", "queue": "shipyard"}}]
    )

    report = run_steps(registry, steps, client=FactsClient(ship_queue=1))

    assert report.status == "blocked"
    assert "shipyard" in report.reason


def test_queue_condition_fails_closed_when_unreadable():
    facts = Facts(levels={1: 1}, queues={}, queues_ok=False)

    holds, why = check({"kind": "queue_empty", "queue": "shipyard"}, facts)

    assert holds is False
    assert "队列读取失败" in why


def test_full_queue_blocks_instead_of_failing():
    registry = FakeRegistry(
        [ToolResult(ok=False, status="rejected", code="QUEUE_FULL", message="队列满了")]
    )
    steps = parse_steps([{"tool": "step", "args": {}}])

    report = run_steps(registry, steps, client=FactsClient())

    assert report.status == "blocked"
    assert "满队列" in report.reason


def test_shipyard_slot_limit_blocks_instead_of_failing():
    registry = FakeRegistry(
        [
            ToolResult(
                ok=False,
                status="partial",
                code="QUEUE_SLOTS_LIMIT",
                retryable=True,
                message="船坞只剩 1 个槽位",
            )
        ]
    )
    steps = parse_steps([{"tool": "step", "args": {}}])

    report = run_steps(registry, steps, client=FactsClient())

    assert report.status == "blocked"
    assert "船坞槽位不足" in report.reason


def test_ships_condition_checks_stationed_ships():
    facts = Facts(ships={202: 4, 203: 1}, ships_ok=True)

    assert check({"kind": "ships_at_least", "ships": {"202": 3}}, facts) == (True, "")

    holds, why = check({"kind": "ships_at_least", "ships": {"202": 5, "203": 1}}, facts)
    assert holds is False
    assert "202" in why
    assert "203" not in why


def test_ships_condition_rejects_non_numeric_elements():
    holds, why = check(
        {"kind": "ships_at_least", "ships": {"轻型运输舰": 1}}, Facts(ships_ok=True)
    )

    assert holds is False
    assert "整数" in why


def test_ships_condition_fails_closed_when_unreadable():
    holds, why = check(
        {"kind": "ships_at_least", "ships": {"202": 1}}, Facts(levels={1: 1})
    )

    assert holds is False
    assert "读取失败" in why


def test_load_facts_reads_the_shipyard_only_when_a_step_needs_it():
    client = FactsClient(ships={202: 4})

    plain = load_facts(client)
    assert plain.ships == {}
    assert plain.ships_ok is False
    assert client.ship_reads == 0

    steps = parse_steps(
        [{"tool": "step", "args": {}, "when": {"kind": "ships_at_least", "ships": {"202": 1}}}]
    )
    facts = load_facts(client, steps=steps)
    assert facts.ships == {202: 4}
    assert facts.ships_ok is True
    assert client.ship_reads == 1


def test_ships_condition_blocks_until_the_fleet_is_ready():
    registry = FakeRegistry([])
    steps = parse_steps(
        [{"tool": "step", "args": {}, "when": {"kind": "ships_at_least", "ships": {"202": 4}}}]
    )

    report = run_steps(registry, steps, client=FactsClient(ships={202: 1}))

    assert report.status == "blocked"
    assert "舰船 202" in report.reason
    assert registry.calls == []


def test_ships_condition_releases_a_ready_fleet():
    registry = FakeRegistry([_ok()])
    steps = parse_steps(
        [{"tool": "step", "args": {}, "when": {"kind": "ships_at_least", "ships": {"202": 4}}}]
    )

    report = run_steps(registry, steps, client=FactsClient(ships={202: 4}))

    assert report.status == "completed"
    assert registry.calls == [("step", {})]


def test_ships_condition_fails_closed_when_the_shipyard_is_unreadable():
    registry = FakeRegistry([])
    steps = parse_steps(
        [{"tool": "step", "args": {}, "when": {"kind": "ships_at_least", "ships": {"202": 4}}}]
    )

    report = run_steps(
        registry, steps, client=FactsClient(ships_error=TransportError("boom"))
    )

    assert report.status == "blocked"
    assert "读取失败" in report.reason
    assert registry.calls == []
