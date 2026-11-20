import hashlib
import json
from pathlib import Path

from autoplay.agent import SUBMIT_PLAN, AgentConfig, decide, run_agent_cycle
from autoplay.brief import render_brief
from autoplay.llm import ChatReply, FakeProvider, ToolCall
from autoplay.prompt import build_system_prompt
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
        self.ship_queue = []
        self.ship_queue = []

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
        return list(self.ship_queue)

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

    def build_ships(self, ships, **kwargs):
        self.writes.append(("build_ships", dict(ships)))
        return []


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


def _submit(steps):
    payload = {"goal": "g", "reason": "r", "steps": steps}
    return _reply([ToolCall(id="submit", name="submit_plan", arguments=json.dumps(payload))])


def _replies_from_transcript(transcript):
    """Rebuild provider replies from a journaled ``decision.transcript``."""

    replies = []
    for turn in transcript:
        calls = [
            ToolCall(id=item["id"], name=item["name"], arguments=item["arguments"])
            for item in turn.get("tool_calls") or []
        ]
        replies.append(ChatReply(content=turn.get("content") or "", tool_calls=calls))
    return replies


def test_read_tool_results_land_in_the_journal_trace():
    provider = FakeProvider(
        [
            _reply([ToolCall(id="r1", name="observe", arguments="{}")]),
            _submit([]),
        ]
    )

    result = run_agent_cycle(
        FakeClient(),
        provider,
        config=AgentConfig(dry_run=True, stop_file="state/does-not-exist-STOP"),
    )
    payload = result.as_dict()

    events = payload["tool_events"]
    assert [event["tool"] for event in events] == ["observe", "submit_plan"]
    assert events[0]["kind"] == "read"
    assert events[0]["result"]["status"] == "ok"
    # The whole read result is journaled, not just a summary.
    assert events[0]["result"]["evidence"]["resources"]["amounts"]["901"] > 0
    assert events[1]["kind"] == "control"
    assert events[1]["result"]["status"] == "accepted"
    # Arguments survive too, so the assistant turn can be replayed verbatim.
    transcript = payload["decision"]["transcript"]
    assert transcript[0]["tool_calls"] == [{"id": "r1", "name": "observe", "arguments": "{}"}]


def test_prompt_tools_and_brief_hashes_match_the_request():
    brief = _brief()
    registry = ToolRegistry(FakeClient(), dry_run=True)
    provider = FakeProvider([_submit([])])

    decision = decide(provider, brief, registry)

    expected_prompt = build_system_prompt(registry, None)
    expected_tools = registry.schema() + [SUBMIT_PLAN.as_openai()]
    canonical = json.dumps(
        expected_tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert decision.prompt_hash == hashlib.sha256(expected_prompt.encode("utf-8")).hexdigest()
    assert decision.tools_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert decision.brief_hash == hashlib.sha256(
        render_brief(brief).encode("utf-8")
    ).hexdigest()
    assert decision.max_chars == 6000


def test_journal_entry_is_enough_to_replay_the_decision():
    provider = FakeProvider(
        [
            _reply([ToolCall(id="p1", name="plan", arguments="{}")]),
            _submit([{"tool": "upgrade_building", "args": {"element": 1}}]),
        ]
    )
    config = AgentConfig(dry_run=True, stop_file="state/does-not-exist-STOP")
    first = run_agent_cycle(FakeClient(), provider, config=config)
    entry = first.as_dict()

    # 1. The hashes let an offline reader prove it has the same request.
    registry = ToolRegistry(FakeClient(), dry_run=True)
    assert entry["prompt_hash"] == hashlib.sha256(
        build_system_prompt(registry, None).encode("utf-8")
    ).hexdigest()
    tools = registry.schema() + [SUBMIT_PLAN.as_openai()]
    canonical = json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert entry["tools_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    brief_text = render_brief(entry["brief"], max_chars=entry["max_chars"])
    assert entry["brief_hash"] == hashlib.sha256(brief_text.encode("utf-8")).hexdigest()

    # 2. Feeding the recorded turns back reproduces the plan and the trace.
    replay_provider = FakeProvider(
        _replies_from_transcript(entry["decision"]["transcript"])
    )
    replay = decide(
        replay_provider,
        entry["brief"],
        registry,
        brief_text=brief_text,
        max_chars=entry["max_chars"],
    )

    assert replay.as_dict() == entry["decision"]
    assert replay.tool_events == entry["tool_events"]


def test_provider_failure_keeps_the_partial_trace():
    provider = FakeProvider([_reply([ToolCall(id="p1", name="plan", arguments="{}")])])
    # The second round raises ProviderError: the cycle must still journal what
    # the model already read, not just the fallback plan.
    result = run_agent_cycle(
        FakeClient(),
        provider,
        config=AgentConfig(dry_run=True, stop_file="state/does-not-exist-STOP"),
    )
    payload = result.as_dict()

    assert payload["status"] == "degraded"
    assert payload["decision"]["fallback"] is True
    assert len(payload["prompt_hash"]) == 64
    assert [event["tool"] for event in payload["tool_events"]] == ["plan"]
    assert payload["decision"]["transcript"][0]["tool_calls"][0]["name"] == "plan"


def test_full_shipyard_queue_blocks_the_cycle_instead_of_failing_it():
    provider = FakeProvider(
        [_submit([{"tool": "build_ships", "args": {"ships": {"203": 1}}}])]
    )
    client = FakeClient()
    client.ship_queue = [{"element_id": 202, "amount": 1}] * 5

    result = run_agent_cycle(
        client,
        provider,
        config=AgentConfig(dry_run=False, stop_file="state/does-not-exist-STOP"),
    )

    # No queue slot left: the order never reaches the game, and "come back
    # later" is not a broken plan.
    assert client.writes == []
    assert result.report["status"] == "blocked"
    assert "船坞槽位不足" in result.report["reason"]
    assert result.status == "ok"
