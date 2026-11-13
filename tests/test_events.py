from newstar_sdk.events import diff_snapshots


def snapshot(**overrides):
    base = {
        "captured_at": "2026-11-13T08:00:00+00:00",
        "planet": {"id": 1},
        "planets": [{"id": 1}],
        "resources": {"usage": {"901": 0.1, "902": 0.1, "903": 0.1}},
        "energy": {"available": 100.0},
        "research": {"levels": {}},
        "queues": {"building": [], "research": [], "shipyard": []},
        "messages": {"counters": []},
    }
    base.update(overrides)
    return base


def test_no_previous_snapshot():
    assert diff_snapshots(None, snapshot()) == []


def test_no_change():
    assert diff_snapshots(snapshot(), snapshot()) == []


def test_message_events():
    before = snapshot(
        messages={"counters": [{"category": 3, "name": "战斗报告", "unread": 0, "total": 2}]}
    )
    after = snapshot(
        captured_at="2026-11-13T08:05:00+00:00",
        messages={"counters": [{"category": 3, "name": "战斗报告", "unread": 2, "total": 4}]},
    )
    events = diff_snapshots(before, after)

    assert len(events) == 1
    event = events[0]
    assert event.kind == "message_new"
    assert event.severity == "notice"
    assert event.data["new"] == 2
    assert event.data["total"] == 4


def test_queue_events():
    before = snapshot(
        queues={
            "building": [{"element_id": 1, "target_level": 5, "amount": None}],
            "research": [],
            "shipyard": [],
        }
    )
    after = snapshot(
        captured_at="2026-11-13T08:05:00+00:00",
        queues={
            "building": [{"element_id": 2, "target_level": 4, "amount": None}],
            "research": [],
            "shipyard": [],
        },
    )
    events = diff_snapshots(before, after)
    kinds = [event.kind for event in events]

    assert "queue_item_finished" in kinds
    assert "queue_item_added" in kinds


def test_queue_empty_event():
    before = snapshot(
        queues={
            "building": [{"element_id": 1, "target_level": 5, "amount": None}],
            "research": [],
            "shipyard": [],
        }
    )
    events = diff_snapshots(before, snapshot())
    assert [event.kind for event in events] == ["queue_empty"]
    assert events[0].data["queue"] == "building"


def test_energy_events():
    events = diff_snapshots(snapshot(energy={"available": 10.0}), snapshot(energy={"available": -5.0}))
    assert events[0].kind == "energy_deficit"
    assert events[0].severity == "warning"

    events = diff_snapshots(snapshot(energy={"available": -5.0}), snapshot(energy={"available": 1.0}))
    assert events[0].kind == "energy_recovered"


def test_storage_event():
    before = snapshot(resources={"usage": {"901": 0.5}})
    after = snapshot(resources={"usage": {"901": 1.2}})
    events = diff_snapshots(before, after)
    assert events[0].kind == "storage_full"
    assert events[0].data["resource"] == "901"


def test_planet_events():
    before = snapshot(planets=[{"id": 1}], planet={"id": 1})
    after = snapshot(
        captured_at="2026-11-13T08:05:00+00:00",
        planets=[{"id": 1}, {"id": 2}],
        planet={"id": 2},
    )
    events = diff_snapshots(before, after)
    kinds = {event.kind for event in events}
    assert "planet_added" in kinds
    assert "planet_switched" in kinds


def test_research_event():
    before = snapshot(research={"levels": {"115": 3}})
    after = snapshot(research={"levels": {"115": 4, "124": 1}})
    events = diff_snapshots(before, after)
    assert [event.kind for event in events] == ["research_completed", "research_completed"]
    assert events[0].data == {"element_id": "115", "from": 3, "to": 4}


def test_snapshot_gap():
    before = snapshot()
    after = snapshot(captured_at="2026-11-13T12:00:00+00:00")
    events = diff_snapshots(before, after)
    assert [event.kind for event in events] == ["snapshot_gap"]
    assert events[0].data["seconds"] == 4 * 3600
