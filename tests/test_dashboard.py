import json
from datetime import datetime, timedelta, timezone

from autoplay.dashboard import (
    fmt_message_time,
    fmt_number,
    fmt_seconds,
    load_state,
    pad,
    render_lines,
    render_message_body,
    render_messages_text,
    render_statistics_text,
    render_text,
    text_width,
    truncate,
)

NOW = datetime(2026, 11, 13, 9, 2, tzinfo=timezone.utc)


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
        "energy": {"available": 500, "production": 120},
        "research": {"expedition_tech": 2, "expedition_slots": 2},
        "queues": {
            "building": [
                {
                    "index": 0,
                    "element_id": 3,
                    "name": "合金冶炼厂",
                    "target_level": 22,
                    "remaining_seconds": 125,
                }
            ],
            "research": [],
            "shipyard": [],
        },
        "fleets": [
            {
                "mission": "探险",
                "target": "[3:505:16]",
                "arrival_time": "17:30:00",
                "returning": False,
            }
        ],
        "messages": {
            "counters": [
                {"category": 4, "name": "系统消息", "unread": 3, "total": 5},
                {"category": 100, "name": "所有消息", "unread": 3, "total": 7},
                {"category": 999, "name": "发件箱", "unread": 0, "total": 2},
            ],
            "unread": 3,
            "total": 7,
            "outbox": 2,
        },
    }


def _cycle(**overrides):
    entry = {
        "at": "2026-11-13T09:01:00+00:00",
        "kind": "cycle",
        "mode": "dry_run",
        "phase": "pre_gravity",
        "goal": "建成资源处理单元（0/1）",
        "status": "dry_run",
        "reason": "计划执行：能源技术 0->1",
        "queues": {"building": 1, "research": 0},
        "queue_items": {
            "building": [
                {
                    "name": "合金冶炼厂",
                    "target_level": 22,
                    "remaining_seconds": 125,
                }
            ],
            "research": [],
        },
        "action": {
            "name": "能源技术",
            "current_level": 0,
            "target_level": 1,
            "element_id": 113,
            "kind": "research",
            "selected": True,
        },
        "plan_top": [
            {
                "name": "能源技术",
                "current_level": 0,
                "target_level": 1,
                "element_id": 113,
                "kind": "research",
                "cost": {"901": 100},
                "affordable": True,
                "selected": True,
            },
            {
                "name": "引力子技术",
                "current_level": 0,
                "target_level": 1,
                "element_id": 199,
                "kind": "research",
                "cost": {"911": 300_000},
                "missing_resources": {"911": 289_458},
                "affordable": False,
                "selected": False,
            },
        ],
        "notes": ["引力子技术需要 300,000 幽能"],
        "alerts": [{"kind": "alert", "message": "收到战斗报告：新增 1 条"}],
        "messages": {
            "unread": 3,
            "total": 7,
            "outbox": 2,
            "categories": [{"category": 4, "name": "系统消息", "unread": 3}],
        },
    }
    entry.update(overrides)
    return entry


def _daemon(**overrides):
    status = {
        "schema": 1,
        "pid": 4242,
        "state": "running",
        "mode": "dry_run",
        "interval": 60.0,
        "snapshot_every": 1,
        "failures": 0,
        "max_failures": 10,
        "last_cycle_at": "2026-11-13T09:01:00+00:00",
        "next_cycle_at": "2026-11-13T09:02:00+00:00",
        "updated_at": "2026-11-13T09:01:50+00:00",
    }
    status.update(overrides)
    return status


def _state_dir(
    tmp_path,
    *,
    snapshot=True,
    daemon=True,
    journal=None,
    events=None,
    statistics=None,
    messages_log=None,
    stop=False,
):
    if snapshot:
        _write(tmp_path / "snapshots.jsonl", [_snapshot()])
    _write(tmp_path / "journal.jsonl", journal if journal is not None else [_cycle()])
    _write(
        tmp_path / "events.jsonl",
        events
        if events is not None
        else [
            {
                "at": "2026-11-13T09:01:00+00:00",
                "kind": "message_new",
                "severity": "notice",
                "name": "战斗报告",
            }
        ],
    )
    if daemon:
        (tmp_path / "daemon.json").write_text(
            json.dumps(_daemon(), ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "daemon.lock").write_text("4242", encoding="utf-8")
    if statistics is not None:
        (tmp_path / "statistics.json").write_text(
            json.dumps(statistics, ensure_ascii=False), encoding="utf-8"
        )
    if messages_log is not None:
        (tmp_path / "messages.json").write_text(
            json.dumps(messages_log, ensure_ascii=False), encoding="utf-8"
        )
    if stop:
        (tmp_path / "STOP").write_text("", encoding="utf-8")
    return tmp_path


def _render(tmp_path, **kwargs):
    state = load_state(_state_dir(tmp_path), now=NOW)
    return state, render_text(state, **kwargs)


def test_load_state(tmp_path):
    state = load_state(_state_dir(tmp_path), now=NOW)

    assert state.snapshot is not None
    assert state.last_cycle["status"] == "dry_run"
    assert state.daemon_pid == 4242
    assert len(state.alerts) == 1
    assert state.messages["unread"] == 3
    assert state.queue_items["building"][0]["name"] == "合金冶炼厂"


def test_render_contains_key_panels(tmp_path):
    _, text = _render(tmp_path, width=100)
    text = "\n".join(text)

    assert "测试宇宙 · TestUser" in text
    assert "金属" in text and "1.50B" in text
    assert "种田初起（pre_gravity）" in text
    assert "技术 2 · 航道 2" in text
    assert "能源技术 0→1" in text
    assert "引力子技术 0→1" in text and "缺幽能" in text
    assert "合金冶炼厂 →22" in text and "剩余 2m05s" in text
    assert "消息  未读 3" in text and "系统消息 3" in text
    assert "ALERT" in text and "收到战斗报告" in text
    assert "新消息 战斗报告" in text
    assert "预览  能源技术" in text
    assert "所有消息" not in text


def test_heartbeat_wins_over_invisible_pid(tmp_path):
    # Sandboxes/PID namespaces can hide a live daemon from os.kill(pid, 0).
    _state_dir(tmp_path)
    (tmp_path / "daemon.json").write_text(
        json.dumps(_daemon(pid=999999), ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "daemon.lock").write_text("999999", encoding="utf-8")
    state = load_state(tmp_path, now=NOW)
    assert state.daemon_alive is False
    health = render_lines(state, width=100)[1].text
    assert "运行中" in health and "不存在" not in health


def test_empty_state_prints_hint(tmp_path):
    state = load_state(tmp_path, now=NOW)
    text = "\n".join(render_text(state))
    assert "尚无快照" in text
    assert "守护未运行" in text


def test_missing_snapshot_still_shows_journal_and_alerts(tmp_path):
    state = load_state(
        _state_dir(tmp_path, snapshot=False), now=NOW
    )
    text = "\n".join(render_text(state, width=100))
    assert "尚无快照" in text
    assert "能源技术" in text
    assert "收到战斗报告" in text


def test_missing_kind_is_treated_as_cycle(tmp_path):
    legacy = _cycle()
    legacy.pop("kind")
    state = load_state(_state_dir(tmp_path, journal=[legacy]), now=NOW)
    assert state.last_cycle is not None
    assert state.last_cycle["status"] == "dry_run"


def test_journal_is_sorted_by_time(tmp_path):
    older = _cycle(at="2026-11-12T09:00:00+00:00", reason="旧计划")
    newer = _cycle(at="2026-11-13T09:01:00+00:00", reason="新计划")
    state = load_state(_state_dir(tmp_path, journal=[newer, older]), now=NOW)
    assert state.last_cycle["reason"] == "新计划"
    assert [entry["reason"] for entry in state.journal] == ["旧计划", "新计划"]


def test_rotated_journal_is_read(tmp_path):
    _state_dir(tmp_path, journal=[_cycle(at="2026-11-13T09:01:00+00:00")])
    (tmp_path / "journal.jsonl.1").write_text(
        json.dumps(_cycle(at="2026-11-12T09:00:00+00:00"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    state = load_state(tmp_path, now=NOW)
    assert len(state.journal) == 2
    assert state.journal[0]["at"].startswith("2026-11-12")


def test_stale_heartbeat_is_an_error(tmp_path):
    stale = (NOW - timedelta(minutes=10)).isoformat()
    _state_dir(tmp_path, daemon=False)
    (tmp_path / "daemon.json").write_text(
        json.dumps(_daemon(updated_at=stale), ensure_ascii=False), encoding="utf-8"
    )
    state = load_state(tmp_path, now=NOW)
    lines = render_lines(state, width=100)
    assert lines[1].style == "error"
    assert "心跳停更" in lines[1].text


def test_stop_request_is_prominent(tmp_path):
    state = load_state(_state_dir(tmp_path, stop=True), now=NOW)
    lines = render_lines(state, width=100)
    assert lines[1].style == "warn"
    assert "停止请求" in lines[1].text


def test_dead_lock_without_heartbeat(tmp_path):
    _state_dir(tmp_path, daemon=False, journal=[])
    (tmp_path / "daemon.lock").write_text("999999", encoding="utf-8")
    state = load_state(tmp_path, now=NOW)
    lines = render_lines(state, width=100)
    assert "锁残留" in lines[1].text


def test_narrow_width_never_overflows(tmp_path):
    state = load_state(_state_dir(tmp_path), now=NOW)
    lines = render_lines(state, width=40, log_lines=20, event_lines=10)
    assert lines
    for line in lines:
        assert text_width(line.text) <= 40


def test_detail_entry_is_attached_to_log_lines(tmp_path):
    state = load_state(_state_dir(tmp_path), now=NOW)
    lines = render_lines(state, width=100)
    logs = [line for line in lines if line.entry is not None]
    assert logs
    assert logs[-1].entry is not None
    assert logs[-1].entry.get("kind") == "cycle"


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


def test_fmt_seconds():
    assert fmt_seconds(None) == "-"
    assert fmt_seconds(45) == "45s"
    assert fmt_seconds(125) == "2m05s"
    assert fmt_seconds(3725) == "1h02m"


def test_in_progress_cycle_is_visible(tmp_path):
    _state_dir(tmp_path)
    status = _daemon(
        updated_at="2026-11-13T09:01:58+00:00",
        cycle_started_at="2026-11-13T09:01:55+00:00",
        last_cycle_at="2026-11-13T09:01:00+00:00",
    )
    (tmp_path / "daemon.json").write_text(json.dumps(status), encoding="utf-8")
    state = load_state(tmp_path, now=NOW)
    health = render_lines(state, width=100)[1].text
    assert "本轮进行中" in health


def test_repeated_cycles_are_grouped(tmp_path):
    journal = [
        _cycle(at=f"2026-11-13T09:0{index}:00+00:00", reason="同一计划")
        for index in range(1, 4)
    ]
    state = load_state(_state_dir(tmp_path, journal=journal), now=NOW)
    text = "\n".join(render_text(state, width=100, log_lines=10))
    assert "×3" in text


def test_expedition_and_fleet_panels(tmp_path):
    journal = [
        _cycle(),
        {
            "at": "2026-11-13T09:01:30+00:00",
            "kind": "expedition",
            "status": "dispatched",
            "reason": "派出 1 队，积分 500",
        },
    ]
    state = load_state(_state_dir(tmp_path, journal=journal), now=NOW)
    text = "\n".join(render_text(state, width=100))
    assert "探险 前往 [3:505:16]" in text
    assert "上次调度 已派出" in text


def _statistics():
    return {
        "schema": 1,
        "captured_at": "2026-11-13T09:00:30+00:00",
        "source": "game.php?page=statistics",
        "boards": {
            "1:1": {
                "who": 1,
                "sort": 1,
                "range": 1,
                "rows": [
                    {
                        "rank": 1,
                        "name": "PlayerOne",
                        "score": 210_975_746_268,
                        "alliance": "ALLY-A",
                        "status": "强",
                    },
                    {
                        "rank": 2,
                        "name": "TestUser",
                        "score": 1_234_567,
                        "alliance": "ALLY-B",
                    },
                ],
            },
            "2:1": {
                "who": 2,
                "sort": 1,
                "range": 1,
                "rows": [
                    {
                        "rank": 1,
                        "name": "ALLY-A",
                        "score": 300_000_000,
                        "members": 7,
                        "member_score": 42_000_000,
                    },
                ],
            },
        },
        "errors": [],
    }


def test_leaderboard_players_tab(tmp_path):
    state = load_state(_state_dir(tmp_path, statistics=_statistics()), now=NOW)
    text = "\n".join(render_statistics_text(state, who=1, width=100))

    assert "排行榜 · 玩家积分榜" in text
    assert "2 名 · 我的排名 #2" in text
    assert "PlayerOne" in text and "210.98B" in text
    assert "ALLY-A" in text
    assert "▸" in text and "TestUser" in text


def test_leaderboard_alliance_tab(tmp_path):
    state = load_state(_state_dir(tmp_path, statistics=_statistics()), now=NOW)
    text = "\n".join(render_statistics_text(state, who=2, width=100))

    assert "排行榜 · 联盟积分榜" in text
    assert "ALLY-A" in text
    assert "成员" in text and "7" in text
    assert "ALLY-B" not in text


def test_leaderboard_missing_data(tmp_path):
    state = load_state(_state_dir(tmp_path), now=NOW)
    text = "\n".join(render_statistics_text(state, who=1, width=100))
    assert "尚无排行榜数据" in text


def test_leaderboard_shows_collection_errors(tmp_path):
    payload = _statistics()
    payload["errors"] = [{"board": "2:1", "error": "TransportError: timed out"}]
    state = load_state(_state_dir(tmp_path, statistics=payload), now=NOW)
    text = "\n".join(render_statistics_text(state, who=1, width=100))
    assert "采集错误" in text
    assert "2:1" in text and "timed out" in text


def _message_log():
    return {
        "schema": 1,
        "captured_at": "2026-11-13T09:00:30+00:00",
        "category": 100,
        "category_name": "所有消息",
        "pages_total": 2,
        "pages_fetched": 2,
        "messages": [
            {
                "id": 11,
                "sender": "舰队司令部",
                "subject": "运输到达",
                "body": "一支友好的舰队已到达并交付了资源。",
                "time": "2026-11-13T08:55:00",
                "time_text": "2026年11月13日, 08:55:00",
                "category": 5,
                "category_name": "运输报告",
                "coordinates": ["[1:2:3]"],
            },
            {
                "id": 10,
                "sender": "系统",
                "subject": "",
                "body": "这是一条没有主题的长消息，用来验证正文换行是否按显示宽度处理。",
                "time": "2026-11-13T08:50:00",
                "time_text": "2026年11月13日, 08:50:00",
                "category": 4,
                "category_name": "系统消息",
                "coordinates": [],
            },
        ],
        "errors": [],
    }


def test_message_list_and_selection(tmp_path):
    state = load_state(_state_dir(tmp_path, messages_log=_message_log()), now=NOW)
    text = "\n".join(render_messages_text(state, width=100, selected=1))

    assert "消息 · 收件箱" in text
    assert "2 条" in text
    assert "舰队司令部" in text and "运输到达" in text
    assert "这是一条没有主题的长消息" in text
    assert "▸" in text


def test_message_body_wraps_and_shows_coordinates(tmp_path):
    state = load_state(_state_dir(tmp_path, messages_log=_message_log()), now=NOW)
    message = state.message_log["messages"][0]
    lines = render_message_body(message, width=40)

    assert any("运输到达" in line for line in lines)
    assert any("友好" in line for line in lines)
    assert any("[1:2:3]" in line for line in lines)
    assert all(text_width(line) <= 40 for line in lines)


def test_message_tab_missing_archive(tmp_path):
    state = load_state(_state_dir(tmp_path), now=NOW)
    text = "\n".join(render_messages_text(state, width=100))
    assert "尚无消息缓存" in text


def test_message_tab_shows_collection_errors(tmp_path):
    payload = _message_log()
    payload["errors"] = [{"page": 2, "error": "TransportError: timed out"}]
    state = load_state(_state_dir(tmp_path, messages_log=payload), now=NOW)
    text = "\n".join(render_messages_text(state, width=100))
    assert "采集错误" in text
    assert "第 2 页" in text and "timed out" in text


def test_message_time_keeps_server_wall_clock():
    now = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
    message = {"time": "2026-11-14T16:40:29", "time_text": "2026年11月14日, 16:40:29"}
    assert fmt_message_time(message, now=now) == "16:40:29"


def test_stopped_daemon_does_not_claim_next_cycle_exit(tmp_path):
    _state_dir(tmp_path, stop=True)
    status = _daemon(state="stopped", stopped_at="2026-11-13T09:01:50+00:00")
    (tmp_path / "daemon.json").write_text(json.dumps(status), encoding="utf-8")
    state = load_state(tmp_path, now=NOW)
    health = render_lines(state, width=100)[1].text
    assert "守护已停止" in health
    assert "下一轮退出" not in health
    assert "state/STOP 仍在" in health
