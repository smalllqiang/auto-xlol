import json
import os

import pytest

from autoplay.runtime import acquire_lock, alive, read_lock, release_lock, write_status


def test_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "agent.lock"

    fd = acquire_lock(path)
    try:
        assert read_lock(path) == os.getpid()
        with pytest.raises(SystemExit):
            acquire_lock(path)
    finally:
        release_lock(fd, path)

    assert not path.exists()


def test_stale_lock_is_taken_over(tmp_path):
    path = tmp_path / "agent.lock"
    path.write_text("999999", encoding="utf-8")  # not a live pid on any sane host
    assert alive(999999) is False

    fd = acquire_lock(path)
    try:
        assert read_lock(path) == os.getpid()
    finally:
        release_lock(fd, path)


def test_garbage_lock_is_treated_as_stale(tmp_path):
    path = tmp_path / "agent.lock"
    path.write_text("not-a-pid", encoding="utf-8")

    fd = acquire_lock(path)
    try:
        assert read_lock(path) == os.getpid()
    finally:
        release_lock(fd, path)


def test_status_file_is_json_and_replaced_atomically(tmp_path):
    path = tmp_path / "agent.json"
    write_status(path, {"cycles": 1, "status": "ok"})
    write_status(path, {"cycles": 2, "status": "degraded"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"cycles": 2, "status": "degraded"}
    assert not path.with_name(path.name + ".tmp").exists()
