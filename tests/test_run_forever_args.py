"""Tests for the daemon's allow-list parsing (comma and repeated flags)."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_forever.py"


def _module():
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("run_forever_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_split_tokens_accepts_commas_and_repeats():
    module = _module()

    assert module.split_tokens(["1,2", "3"]) == ["1", "2", "3"]
    assert module.split_tokens(["1，2"]) == ["1", "2"]
    assert module.split_tokens([" 1 , ", "2"]) == ["1", "2"]
    assert module.split_tokens([]) == []


def test_resolve_allow_accepts_ids_and_names():
    module = _module()

    assert module.resolve_allow(["1,14"]) == [1, 14]
    assert module.resolve_allow(["自律工厂"]) == [14]


def test_resolve_allow_rejects_unknown_names():
    module = _module()

    with pytest.raises(SystemExit):
        module.resolve_allow(["不是建筑"])


def test_resolve_ids_accepts_commas():
    module = _module()

    assert module.resolve_ids(["1,2"]) == [1, 2]
    assert module.resolve_ids(["1", "2"]) == [1, 2]


def test_resolve_ids_rejects_non_numeric():
    module = _module()

    with pytest.raises(ValueError):
        module.resolve_ids(["1,x"])
