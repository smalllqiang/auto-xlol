"""Cross-cycle memory: read the agent journal back into the next decision.

Without this every ``scripts/agent.py`` run starts cold: the brief says
``first_decision`` and the model has no idea it already claimed the bonus or
just ordered 500 ships.  The journal written by the previous runs is enough to
give it a short memory, and reading it keeps the process itself stateless.

The functions here are tolerant on purpose: a truncated or rotated journal must
degrade to "no history", never crash a cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

#: Journal entries we care about (the daemon writes other ``kind`` values too).
AGENT_KIND = "agent"


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for candidate in (path.with_name(path.name + ".1"), path):
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                entries.append(dict(value))
    return entries


def read_agent_entries(path: Path, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Last ``limit`` agent cycles from ``path`` (and its rotated ``.1``)."""

    entries = [entry for entry in _read_lines(Path(path)) if entry.get("kind") == AGENT_KIND]
    if limit <= 0:
        return []
    return entries[-int(limit) :]


def next_cycle(entries: List[Dict[str, Any]]) -> int:
    cycles = [int(entry.get("cycle") or 0) for entry in entries]
    return (max(cycles) if cycles else 0) + 1


def decision_history(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact ``{cycle, chose, result}`` rows for the brief's history section."""

    history: List[Dict[str, Any]] = []
    for entry in entries:
        decision = entry.get("decision") or {}
        report = entry.get("report") or {}
        steps = decision.get("steps") or []
        if steps:
            first = steps[0]
            choice = str(first.get("tool") or "?")
            args = first.get("args") or {}
            if args:
                choice += " " + json.dumps(args, ensure_ascii=False, sort_keys=True)
        else:
            choice = "wait"
        history.append(
            {
                "cycle": entry.get("cycle"),
                "chose": choice,
                "result": report.get("status") or entry.get("status") or "",
            }
        )
    return history


def last_brief(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The most recent brief, used as the delta baseline."""

    for entry in reversed(entries):
        brief = entry.get("brief")
        if isinstance(brief, Mapping):
            return dict(brief)
    return None


__all__ = [
    "AGENT_KIND",
    "decision_history",
    "last_brief",
    "next_cycle",
    "read_agent_entries",
]
