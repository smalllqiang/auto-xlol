import json

from autoplay.memory import (
    decision_history,
    last_brief,
    next_cycle,
    read_agent_entries,
)


def _write(path, entries):
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _entry(cycle, tool="upgrade_building", status="completed", brief=None):
    return {
        "kind": "agent",
        "cycle": cycle,
        "decision": {"steps": [{"tool": tool, "args": {"element": 1}}]},
        "report": {"status": status},
        "brief": brief or {"resources": {}, "queues": {}, "energy": {}},
    }


def test_reads_only_agent_entries_and_respects_limit(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write(
        path,
        [
            {"kind": "cycle", "cycle": 1},
            _entry(2),
            {"kind": "error", "error": "boom"},
            _entry(3),
            _entry(4),
        ],
    )

    entries = read_agent_entries(path, limit=2)

    assert [entry["cycle"] for entry in entries] == [3, 4]


def test_malformed_lines_and_missing_file_degrade_to_empty(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"kind": "agent", "cycle": 1}\nnot json\n', encoding="utf-8")

    assert [entry["cycle"] for entry in read_agent_entries(path)] == [1]
    assert read_agent_entries(tmp_path / "missing.jsonl") == []


def test_rotated_file_is_part_of_the_history(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write(path.with_name(path.name + ".1"), [_entry(1), _entry(2)])
    _write(path, [_entry(3)])

    assert [entry["cycle"] for entry in read_agent_entries(path)] == [1, 2, 3]


def test_history_rows_explain_the_choice():
    entries = [
        _entry(2, tool="build_ships", status="ok"),
        {"kind": "agent", "cycle": 3, "decision": {"steps": []}, "report": {"status": "empty"}},
    ]

    history = decision_history(entries)

    assert history[0]["cycle"] == 2
    assert history[0]["chose"].startswith("build_ships")
    assert history[0]["result"] == "ok"
    assert history[1]["chose"] == "wait"


def test_last_brief_baseline_and_next_cycle():
    entries = [_entry(2), _entry(5, brief={"resources": {"901": {"amount": 5}}})]

    assert last_brief(entries) == {"resources": {"901": {"amount": 5}}}
    assert last_brief([]) is None
    assert next_cycle(entries) == 6
    assert next_cycle([]) == 1
