import json
from pathlib import Path

from autoplay.agent import AgentConfig, decide, run_agent_cycle
from autoplay.llm import ChatReply, FakeProvider, ToolCall
from autoplay.tools import ToolRegistry
from newstar_sdk.parsers import (
    parse_bonuses,
    parse_building_queue,
    parse_buildings,
    parse_fleets,
    parse_overview,
    parse_production,
    parse_research,
)
from newstar_sdk.snapshot import build_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def __init__(self):
        self.writes = []

    def get_overview(self):
        return parse_overview(_html("overview.html"))

    def get_research(self):
        return parse_research(_html("research.html"))

    def get_buildings(self):
        return parse_buildings(_html("buildings.html"))

    def get_production(self):
        return parse_production(_html("production.html"))

    def get_bonuses(self):
        return parse_bonuses(_html("infobonus.html"))

    def get_building_queue(self):
        return parse_building_queue(_html("buildings_queue.html"))

    def get_research_queue(self):
        return []

    def get_ship_queue(self, mode="fleet"):
        return []

    def get_fleets(self):
        return parse_fleets(_html("fleet_table_empty.html"))

    def get_message_counters(self):
        return {}

    def upgrade_building(self, element, levels=1, **kwargs):
        self.writes.append(("upgrade_building", element, levels))
        return [{"element_id": element, "target_level": 1}]

    def research(self, element, levels=1, **kwargs):
        self.writes.append(("research", element, levels))
        return [{"element_id": element, "target_level": 1}]


def _brief():
    from autoplay.brief import build_brief
    from autoplay.planner import compute_plan

    plan = compute_plan(
        parse_buildings(_html("buildings.html")),
        parse_research(_html("research.html")),
        parse_production(_html("production.html")),
        parse_overview(_html("overview.html")).resources.amounts,
    )
    return build_brief(build_snapshot(FakeClient()), plan)


def _reply(tool_calls=None, content=""):
    calls = list(tool_calls or [])
    raw = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }
    return ChatReply(content=content, tool_calls=calls, raw_message=raw)


def test_submit_plan_becomes_a_typed_plan():
    plan_payload = {
        "goal": "补产出",
        "reason": "合金厂回本最快",
        "steps": [{"tool": "upgrade_building", "args": {"element": 1}, "why": "回本 0.2h"}],
    }
    provider = FakeProvider(
        [_reply([ToolCall(id="1", name="submit_plan", arguments=json.dumps(plan_payload))])]
    )

    decision = decide(provider, _brief(), ToolRegistry(FakeClient(), dry_run=True))

    assert decision.submitted is True
    assert decision.goal == "补产出"
    assert [step.tool for step in decision.steps] == ["upgrade_building"]
    assert decision.steps[0].args == {"element": 1}
    assert decision.steps[0].why == "回本 0.2h"


def test_write_tool_calls_are_staged_not_executed():
    provider = FakeProvider(
        [
            _reply([ToolCall(id="1", name="upgrade_building", arguments='{"element": 14}')]),
            _reply(content="done"),
        ]
    )
    client = FakeClient()
    registry = ToolRegistry(client, dry_run=False)

    decision = decide(provider, _brief(), registry)

    assert client.writes == []  # nothing fired during the loop
    assert [step.tool for step in decision.steps] == ["upgrade_building"]
    # Second request must contain the staged tool result, so the model is not lied to.
    tool_messages = [m for m in provider.calls[1]["messages"] if m.get("role") == "tool"]
    assert json.loads(tool_messages[0]["content"])["status"] == "staged"


def test_plain_json_reply_is_accepted():
    payload = {"goal": "g", "reason": "r", "steps": [{"tool": "research", "args": {"technology": 124}}]}
    provider = FakeProvider([_reply(content="```json\n" + json.dumps(payload) + "\n```")])

    decision = decide(provider, _brief(), ToolRegistry(FakeClient(), dry_run=True))

    assert decision.submitted is True
    assert decision.steps[0].tool == "research"


def test_provider_failure_falls_back_to_the_planner():
    provider = FakeProvider([])  # raises ProviderError immediately

    result = run_agent_cycle(
        FakeClient(),
        provider,
        config=AgentConfig(dry_run=True, stop_file="state/does-not-exist-STOP"),
    )

    assert result.status == "degraded"
    assert result.decision["fallback"] is True
    assert len(result.decision["steps"]) == 1


def test_stop_file_short_circuits_everything():
    provider = FakeProvider([])
    client = FakeClient()

    result = run_agent_cycle(client, provider, stop_exists=True)

    assert result.status == "stopped"
    assert result.brief == {}
    assert provider.calls == []


def test_explicit_empty_plan_means_wait_not_fallback():
    provider = FakeProvider(
        [
            _reply(
                [
                    ToolCall(
                        id="1",
                        name="submit_plan",
                        arguments=json.dumps({"goal": "等队列", "steps": []}),
                    )
                ]
            )
        ]
    )

    result = run_agent_cycle(
        FakeClient(),
        provider,
        config=AgentConfig(dry_run=True, stop_file="state/does-not-exist-STOP"),
    )

    assert result.status == "ok"
    assert result.decision["fallback"] is False
    assert result.decision["steps"] == []
    assert result.report["status"] == "empty"


def test_execute_cycle_writes_through_the_runner_only():
    provider = FakeProvider(
        [
            _reply(
                [
                    ToolCall(
                        id="1",
                        name="submit_plan",
                        arguments=json.dumps(
                            {"steps": [{"tool": "upgrade_building", "args": {"element": 14}}]}
                        ),
                    )
                ]
            )
        ]
    )
    client = FakeClient()

    result = run_agent_cycle(
        client,
        provider,
        config=AgentConfig(dry_run=False, stop_file="state/does-not-exist-STOP"),
    )

    # The write happened once, and only after the model finished planning.
    assert client.writes == [("upgrade_building", 14, 1)]
    assert result.report["status"] == "completed"
    assert result.report["steps"][0]["status"] == "ok"


def test_dry_run_cycle_runs_the_runner_without_writing():
    provider = FakeProvider(
        [
            _reply(
                [
                    ToolCall(
                        id="1",
                        name="submit_plan",
                        arguments=json.dumps(
                            {"steps": [{"tool": "upgrade_building", "args": {"element": 14}}]}
                        ),
                    )
                ]
            )
        ]
    )
    client = FakeClient()

    result = run_agent_cycle(
        client,
        provider,
        config=AgentConfig(dry_run=True, stop_file="state/does-not-exist-STOP"),
    )

    assert result.status == "ok"
    assert result.report["status"] == "completed"
    assert result.report["steps"][0]["status"] == "dry_run"
    assert client.writes == []


def test_failed_step_marks_the_cycle_failed():
    provider = FakeProvider(
        [
            _reply(
                [
                    ToolCall(
                        id="1",
                        name="submit_plan",
                        arguments=json.dumps({"steps": [{"tool": "launch_rocket", "args": {}}]}),
                    )
                ]
            )
        ]
    )

    result = run_agent_cycle(
        FakeClient(),
        provider,
        config=AgentConfig(dry_run=False, stop_file="state/does-not-exist-STOP"),
    )

    assert result.status == "failed"
    assert result.report["status"] == "failed"
    assert "UNKNOWN_TOOL" in result.report["reason"]
