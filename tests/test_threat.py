"""Tests for threat assessment and the verified recall (P2-2)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from autoplay.daemon import DaemonConfig, run_cycle
from autoplay.threat import (
    assess_threat,
    parse_espionage,
)
from autoplay.threat_executor import recall_candidates, recall_fleets
from newstar_sdk.exceptions import ActionRejected
from newstar_sdk.models import Coordinates, FleetInfo, Message, Resources

ESPIONAGE_BODY = (
    "敌对行星 母星 [3:494:3] 发射的间谍探针在您的下列殖民地周围被发现 母星 [3:505:8]"
)
NOW = datetime(2026, 11, 18, 12, 0, 0)


def _message(category=0, body=None, minutes_ago=0, message_id=1):
    if body is None:
        body = ESPIONAGE_BODY if category == 0 else ""
    return Message(
        id=message_id,
        category=category,
        body=body,
        time=NOW - timedelta(minutes=minutes_ago),
    )


def _fleet(fleet_id=5, mission="探险", returning=False, can_recall=True):
    return FleetInfo(
        id=fleet_id,
        mission=mission,
        returning=returning,
        can_recall=can_recall,
        target_coordinates=Coordinates(3, 505, 16),
    )


def test_parse_espionage_reads_both_coordinates():
    event = parse_espionage(ESPIONAGE_BODY, message_id=7)

    assert event is not None
    assert event.attacker == "母星"
    assert event.attacker_coordinates == Coordinates(3, 494, 3)
    assert event.target == "母星"
    assert event.target_coordinates == Coordinates(3, 505, 8)


def test_parse_espionage_ignores_other_messages():
    assert parse_espionage("你的探险队已从探险中归来。") is None
    assert parse_espionage("") is None


def test_recent_scan_is_danger():
    assessment = assess_threat([_message(minutes_ago=10)], now=NOW)

    assert assessment.level == "danger"
    assert assessment.danger is True
    assert any("被侦察" in reason for reason in assessment.reasons)
    assert assessment.recommendations


def test_scan_within_a_day_is_watch():
    assessment = assess_threat([_message(minutes_ago=5 * 60)], now=NOW)

    assert assessment.level == "watch"
    assert assessment.danger is False


def test_old_scan_is_calm():
    assessment = assess_threat([_message(minutes_ago=4 * 24 * 60)], now=NOW)

    assert assessment.level == "calm"
    assert assessment.espionage  # still listed for context


def test_defensive_battle_report_is_danger():
    assessment = assess_threat(
        [_message(category=3, body="你的防御被摧毁，损失惨重", minutes_ago=30)],
        now=NOW,
    )

    assert assessment.level == "danger"
    assert assessment.battle_reports == [1]


def test_offensive_battle_report_only_watches():
    assessment = assess_threat(
        [_message(category=3, body="攻击成功，目标被摧毁", minutes_ago=30)],
        now=NOW,
    )

    assert assessment.level == "watch"


def test_recall_candidates_skip_returning_fleets():
    fleets = [
        _fleet(1),
        _fleet(2, returning=True),
        _fleet(3, can_recall=False),
        _fleet(4, mission="运输"),
    ]

    assert [fleet.id for fleet in recall_candidates(fleets)] == [1, 4]
    assessment = assess_threat([], fleets, now=NOW)
    assert assessment.in_flight == 4
    assert assessment.recallable == [1, 4]


def test_exposure_uses_capacity_ratio():
    resources = Resources(
        amounts={901: 300.0, 902: 50.0}, capacities={901: 100.0, 902: 100.0}
    )

    assessment = assess_threat(
        [_message(minutes_ago=5)], now=NOW, resources=resources
    )

    assert assessment.exposure == pytest.approx(3.0)
    assert any("目标" in item for item in assessment.recommendations)


class FakeRecallClient:
    def __init__(self, fleets, after):
        self.fleets = list(fleets)
        self.after = list(after)
        self.posted = []

    def get_fleets(self):
        return list(self.fleets)

    def recall_fleet(self, fleet_id):
        self.posted.append(fleet_id)
        return list(self.after)


def test_recall_preview_does_not_post():
    client = FakeRecallClient([_fleet(5)], [])

    result = recall_fleets(client, dry_run=True, fleets=client.fleets)

    assert result.status == "preview"
    assert result.fleet_id == 5
    assert client.posted == []


def test_recall_requires_allow_list():
    client = FakeRecallClient([_fleet(5)], [])

    with pytest.raises(ActionRejected) as info:
        recall_fleets(client, dry_run=False, fleets=client.fleets)
    assert info.value.before_post is True
    assert client.posted == []


def test_recall_rejects_fleet_outside_allow_list():
    client = FakeRecallClient([_fleet(5)], [])

    with pytest.raises(ActionRejected) as info:
        recall_fleets(client, dry_run=False, allowed=[9], fleets=client.fleets)
    assert info.value.before_post is True
    assert client.posted == []


def test_recall_verifies_returning_flag():
    client = FakeRecallClient([_fleet(5)], [_fleet(5, returning=True)])

    result = recall_fleets(client, dry_run=False, allowed=[5], fleets=client.fleets)

    assert result.status == "recalled"
    assert result.evidence["returning"] is True
    assert client.posted == [5]


def test_recall_without_page_evidence_is_rejected():
    client = FakeRecallClient([_fleet(5)], [_fleet(5)])

    with pytest.raises(ActionRejected) as info:
        recall_fleets(client, dry_run=False, allowed=[5], fleets=client.fleets)
    assert info.value.before_post is False
    assert client.posted == [5]


def test_recall_skips_when_nothing_can_be_recalled():
    client = FakeRecallClient([_fleet(5, returning=True)], [])

    result = recall_fleets(client, dry_run=False, allowed=[5], fleets=client.fleets)

    assert result.status == "skipped"
    assert client.posted == []


def test_danger_pauses_the_cycle(monkeypatch):
    assessment = assess_threat([_message(minutes_ago=5)], now=NOW)
    plan = SimpleNamespace(
        phase="pre_gravity",
        goal="建成资源处理单元（0/1）",
        goal_status="建成资源处理单元（0/1）",
        actions=[],
        notes=[],
    )
    monkeypatch.setattr("autoplay.daemon.build_plan", lambda *args, **kwargs: plan)

    result = run_cycle(
        SimpleNamespace(get_message_counters=lambda: {}),
        DaemonConfig(dry_run=True, pause_on_threat=True),
        threat=assessment,
    )

    assert result.status == "paused"
    assert "威胁" in result.reason
    assert result.threat["level"] == "danger"


def test_calm_does_not_pause(monkeypatch):
    assessment = assess_threat([], now=NOW)
    plan = SimpleNamespace(
        phase="pre_gravity",
        goal="建成资源处理单元（0/1）",
        goal_status="建成资源处理单元（0/1）",
        actions=[],
        notes=[],
    )
    monkeypatch.setattr("autoplay.daemon.build_plan", lambda *args, **kwargs: plan)

    result = run_cycle(
        SimpleNamespace(
            get_message_counters=lambda: {},
            get_building_queue=lambda: [],
            get_research_queue=lambda: [],
        ),
        DaemonConfig(dry_run=True, pause_on_threat=True),
        threat=assessment,
    )

    assert result.status != "paused"
    assert result.threat["level"] == "calm"
