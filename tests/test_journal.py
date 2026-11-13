import json

from autoplay.journal import append_jsonl, rotate_if_needed


def test_append_jsonl_creates_parent(tmp_path):
    path = tmp_path / "nested" / "journal.jsonl"
    append_jsonl(path, {"a": 1})
    assert json.loads(path.read_text().strip()) == {"a": 1}


def test_rotate_when_over_limit(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_jsonl(path, {"a": 1})
    append_jsonl(path, {"b": 2}, max_bytes=1)

    backup = tmp_path / "journal.jsonl.1"
    assert backup.exists()
    assert json.loads(backup.read_text().strip()) == {"a": 1}
    assert json.loads(path.read_text().strip()) == {"b": 2}


def test_rotation_replaces_previous_backup(tmp_path):
    path = tmp_path / "journal.jsonl"
    backup = tmp_path / "journal.jsonl.1"
    backup.write_text('{"old": true}\n', encoding="utf-8")
    path.write_text('{"new": true}\n', encoding="utf-8")

    assert rotate_if_needed(path, max_bytes=1) is True
    assert json.loads(backup.read_text().strip()) == {"new": True}


def test_rotation_disabled(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("x" * 100, encoding="utf-8")

    assert rotate_if_needed(path, max_bytes=0) is False
    assert path.exists()
