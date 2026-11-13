import json
from datetime import datetime, timezone

from autoplay.dashboard import (
    fmt_number,
    load_state,
    pad,
    render_text,
    text_width,
    truncate,
)


def _write(path, items):
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


def _snapshot():
    return {
        "captured_at": "2026-11-13T09:00:00+00:00",
        "account": {"username": "TestUser", "universe": "UNI1", "game_name": "测试宇宙"},
        "planet": {"name": "母星", "coordinates": "[1:1:8]"},
        "resources": {
            "amounts": {"901": 1_500_000_000, "902": 2_500_000, "903": 3_000, "911": 500},
            "capacities": {"901": 50_000, "902": 50_000, "903": 50_000, "911": 5_900},
            "production": {"901": 3600, "902": 2400, "903": 0},
        },
        "research": {"expedition_slots": 2},
    }


def _state_dir(tmp_path):
    _write(tmp_path / "snapshots.jsonl", [_snapshot()])
    _write(
        tmp_path / "journal.jsonl",
        [
            {
                "at": "2026-11-13T09:01:00+00:00",
                "kind": "cycle",
                "mode": "dry_run",
                "phase": "pre_gravity",
                "goal": "建成资源处理单元（0/1）",
                "status": "dry_run",
                "reason": "计划执行：能源技术 0->1",
                "queues": {"building": 0, "research": 1},
                "action": {"name": "能源技术"},
                "notes": ["引力子技术需要 300,000 幽能"],
                "alerts": [{"kind": "alert", "message": "收到战斗报告：新增 1 条"}],
            }
        ],
    )
    _write(
        tmp_path / "events.jsonl",
        [
            {
                "at": "2026-11-13T09:01:00+00:00",
                "kind": "message_new",
                "severity": "notice",
                "name": "战斗报告",
            }
        ],
    )
    (tmp_path / "daemon.lock").write_text("4242", encoding="utf-8")
    return tmp_path


def test_load_state(tmp_path):
    state = load_state(_state_dir(tmp_path), now=datetime(2026, 11, 13, 9, 2, tzinfo=timezone.utc))

    assert state.snapshot is not None
    assert state.last_cycle["status"] == "dry_run"
    assert state.daemon_pid == 4242
    assert len(state.alerts) == 1


def test_render_contains_key_panels(tmp_path):
    state = load_state(_state_dir(tmp_path), now=datetime(2026, 11, 13, 9, 2, tzinfo=timezone.utc))
    text = "\n".join(render_text(state, width=100))

    assert "测试宇宙 · TestUser" in text
    assert "金属" in text and "1.50B" in text
    assert "种田初起（pre_gravity）" in text
    assert "探险航道 2" in text
    assert "ALERT" in text and "收到战斗报告" in text
    assert "message_new" in text
    assert "研究1" in text  # queue labels are localised


def test_empty_state_prints_hint(tmp_path):
    state = load_state(tmp_path)
    text = "\n".join(render_text(state))
    assert "尚无快照" in text


def test_cjk_width_helpers():
    assert text_width("abc") == 3
    assert text_width("母星") == 4
    assert truncate("abcdef", 3) == "ab…"
    assert text_width(truncate("母星测试", 5)) <= 5
    assert text_width(pad("母星", 10)) == 10


def test_fmt_number():
    assert fmt_number(1_500_000_000) == "1.50B"
    assert fmt_number(2_500_000) == "2.50M"
    assert fmt_number(3_600) == "3.60K"
    assert fmt_number(42) == "42"
    assert fmt_number(None) == "-"
