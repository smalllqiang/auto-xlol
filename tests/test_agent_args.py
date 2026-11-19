"""Defaults for the agent CLI: one dry run unless told otherwise."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"


def _module():
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("agent_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_defaults_are_one_dry_cycle():
    args = _module().parse_args([])

    assert args.every == 0.0  # no loop
    assert args.execute is False  # default dry run
    assert args.max_cycles == 0
    assert args.stop_file == "state/STOP"
    assert args.status_file == "state/agent.json"
    assert args.lock_file == "state/agent.lock"


def test_loop_flags_are_parsed():
    args = _module().parse_args(["--every", "300", "--execute", "--max-cycles", "3"])

    assert args.every == 300.0
    assert args.execute is True
    assert args.max_cycles == 3
