from newstar_sdk.report import summarize_snapshots


def snapshot(ts, metal=1000.0, crystal=500.0, deut=100.0, queues=None, levels=None, unread=0):
    return {
        "captured_at": ts,
        "resources": {"amounts": {"901": metal, "902": crystal, "903": deut}},
        "production": {"resources": {"901": {"total": 100.0}, "902": {"total": 50.0}}},
        "queues": queues or {"building": [], "research": [], "shipyard": []},
        "research": {"levels": levels or {}},
        "messages": {"unread": unread},
        "errors": [],
    }


def test_empty():
    summary = summarize_snapshots([])
    assert summary["count"] == 0
    assert summary["resources"] == {}


def test_resource_trend_and_rate():
    snaps = [
        snapshot("2026-11-13T08:00:00+00:00", metal=1000),
        snapshot("2026-11-13T09:00:00+00:00", metal=1600),
        snapshot("2026-11-13T10:00:00+00:00", metal=2200),
    ]
    summary = summarize_snapshots(snaps)

    metal = summary["resources"]["901"]
    assert metal["first"] == 1000
    assert metal["last"] == 2200
    assert metal["delta"] == 1200
    assert metal["per_hour"] == 600
    assert metal["min"] == 1000
    assert metal["max"] == 2200
    assert summary["span_seconds"] == 7200
    assert summary["median_interval"] == 3600


def test_queue_occupancy():
    busy = {"building": [{"element_id": 1}], "research": [], "shipyard": []}
    idle = {"building": [], "research": [], "shipyard": []}
    snaps = [
        snapshot("2026-11-13T08:00:00+00:00", queues=busy),
        snapshot("2026-11-13T08:05:00+00:00", queues=idle),
        snapshot("2026-11-13T08:10:00+00:00", queues=busy),
        snapshot("2026-11-13T08:15:00+00:00", queues=idle),
    ]
    summary = summarize_snapshots(snaps)
    assert summary["queues"]["building"] == {"occupancy": 0.5, "max": 1, "last": 0}


def test_gap_detection():
    snaps = [
        snapshot("2026-11-13T08:00:00+00:00"),
        snapshot("2026-11-13T08:05:00+00:00"),
        snapshot("2026-11-13T09:05:00+00:00"),  # one hour missing
    ]
    summary = summarize_snapshots(snaps)
    assert len(summary["gaps"]) == 1
    assert summary["gaps"][0]["seconds"] == 3600


def test_research_progress():
    snaps = [
        snapshot("2026-11-13T08:00:00+00:00", levels={"115": 3}),
        snapshot("2026-11-13T09:00:00+00:00", levels={"115": 4, "124": 1}),
    ]
    summary = summarize_snapshots(snaps)
    completed = summary["research"]["completed"]
    assert {"element_id": "115", "from": 3, "to": 4} in completed
    assert {"element_id": "124", "from": 0, "to": 1} in completed


def test_event_stats():
    events = [
        {"kind": "message_new", "severity": "notice"},
        {"kind": "message_new", "severity": "notice"},
        {"kind": "queue_empty", "severity": "notice"},
        {"kind": "energy_deficit", "severity": "warning"},
    ]
    summary = summarize_snapshots([snapshot("2026-11-13T08:00:00+00:00")], events)
    assert summary["events"]["total"] == 4
    assert summary["events"]["by_kind"]["message_new"] == 2
    assert summary["events"]["by_severity"]["warning"] == 1


def test_snapshot_errors_counted():
    snaps = [snapshot("2026-11-13T08:00:00+00:00")]
    snaps[0]["errors"] = [{"section": "research", "error": "boom"}]
    summary = summarize_snapshots(snaps)
    assert summary["errors"] == 1
