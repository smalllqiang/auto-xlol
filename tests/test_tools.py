import pytest

from autoplay.tools import ToolRegistry, ToolResult, translate
from newstar_sdk.exceptions import ActionRejected, GameError, TransportError


class FakeClient:
    def __init__(self, queue=None, reject=None):
        self.queue = list(queue or [])
        self.reject = reject
        self.calls = []

    def _maybe_reject(self):
        if self.reject is not None:
            raise self.reject

    def upgrade_building(self, element, levels=1, *, target_level=None, precheck=True, verify=True):
        self.calls.append(
            {
                "tool": "upgrade_building",
                "element": element,
                "levels": levels,
                "target_level": target_level,
                "precheck": precheck,
                "verify": verify,
            }
        )
        self._maybe_reject()
        return self.queue

    def research(self, element, levels=1, *, target_level=None, precheck=True, verify=True):
        self.calls.append({"tool": "research", "element": element, "levels": levels})
        self._maybe_reject()
        return self.queue

    def get_building_queue(self):
        return self.queue

    def get_research_queue(self):
        return self.queue

    def build_ships(self, ships, *, mode="fleet", precheck=True, verify=True):
        self.calls.append({"tool": "build_ships", "ships": ships, "mode": mode, "precheck": precheck})
        self._maybe_reject()
        return self.queue

    def send_fleet(self, target, ships, mission, **kwargs):
        self.calls.append({"tool": "send_fleet", "target": target, "ships": ships, "mission": mission})
        self._maybe_reject()

        class Dispatch:
            def as_dict(self):
                return {"mission": mission, "target_coordinates": str(target)}

        return Dispatch()

    def recall_fleet(self, fleet_id):
        self.calls.append({"tool": "recall_fleet", "fleet_id": fleet_id})
        return []

    def set_production_ratios(self, ratios):
        self.calls.append({"tool": "set_production_ratios", "ratios": ratios})

        class Report:
            ratios = {1: 90}

        return Report()

    def claim_daily_bonus(self):
        class Bonus:
            claimed = False

        return Bonus()

    def destroy_building(self, element):
        self.queue = [{"element_id": element, "target_level": 0}]
        return self.queue

    def cancel_building_queue(self, position):
        return []


def _registry(client, dry_run=False):
    return ToolRegistry(client, dry_run=dry_run)


def test_schema_lists_verbs_not_nouns():
    names = {spec.name for spec in _registry(FakeClient()).specs()}

    assert {"upgrade_building", "upgrade_building_to", "research", "research_to"} <= names
    assert {"build_ships", "send_fleet", "recall_fleet", "cancel_queue"} <= names
    # No per-element tools: that is the explosion this design avoids.
    assert not any(name.startswith("build_metal") for name in names)
    for spec in _registry(FakeClient()).specs():
        assert spec.parameters["type"] == "object"


def test_dry_run_never_calls_the_client():
    client = FakeClient()
    result = _registry(client, dry_run=True).call("upgrade_building", {"element": 1})

    assert result.status == "dry_run"
    assert result.ok is True
    assert client.calls == []


def test_write_passes_precheck_false_and_verifies_from_the_page():
    client = FakeClient(queue=[{"element_id": 14, "target_level": 1}])
    result = _registry(client).call("upgrade_building", {"element": 14})

    assert result.ok is True
    assert result.status == "ok"
    assert result.actual["target_levels"] == [1]
    call = client.calls[0]
    assert call["precheck"] is False  # the game adjudicates (ADR-030)
    assert call["verify"] is True  # but the page still has to prove it


def test_after_post_rejection_reports_actual_state():
    client = FakeClient(
        queue=[{"element_id": 14, "target_level": 2}],
        reject=ActionRejected("not accepted", before_post=False),
    )
    result = _registry(client).call("upgrade_building", {"element": 14})

    assert result.ok is False
    assert result.status == "partial"
    assert result.code == "PARTIAL_QUEUE"
    assert result.retryable is True
    assert result.before_post is False
    assert result.actual["target_levels"] == [2]


def test_nothing_queued_is_rejected_not_partial():
    client = FakeClient(queue=[], reject=ActionRejected("no", before_post=False))
    result = _registry(client).call("research", {"technology": 124})

    assert result.status == "rejected"
    assert result.code == "NOT_QUEUED"
    assert result.actual["target_levels"] == []


def test_batch_reports_partial_shipyard():
    client = FakeClient(queue=[{"element_id": 202, "amount": 2}])
    result = _registry(client).call("build_ships", {"ships": {"202": 5}})

    assert result.ok is False
    assert result.status == "partial"
    assert result.actual["queued"] == {"202": 2}


def test_unknown_tool_is_data_not_an_exception():
    result = _registry(FakeClient()).call("launch_rocket", {})

    assert result.ok is False
    assert result.code == "UNKNOWN_TOOL"


@pytest.mark.parametrize(
    "exc,code,retryable",
    [
        (ActionRejected("x", before_post=True), "REJECTED_BEFORE_POST", True),
        (ActionRejected("x", before_post=False), "REJECTED_AFTER_POST", False),
        (GameError("module disabled"), "GAME_ERROR", False),
        (TransportError("timeout"), "TRANSPORT_ERROR", True),
        (ValueError("bad"), "INVALID_ARGS", False),
    ],
)
def test_translate_maps_sdk_errors(exc, code, retryable):
    result = translate(exc)

    assert isinstance(result, ToolResult)
    assert result.code == code
    assert result.retryable is retryable


def test_send_fleet_half_sent_is_never_retried():
    client = FakeClient(reject=ActionRejected("token lost", before_post=False))
    result = _registry(client).call(
        "send_fleet", {"target": "3:505:16", "ships": {"202": 1}, "mission": 15}
    )

    assert result.ok is False
    assert result.retryable is False
