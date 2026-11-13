"""Deterministic reactions to events.

An event says "something changed"; a reaction says what the loop should *do*
about it, and how loudly.  There are only two kinds:

* ``alert`` -- worth stopping for: espionage/battle reports, energy deficit,
  a lost planet, a long collection gap, a failed message check;
* ``info`` -- worth logging, nothing more: queue idle, new non-combat message,
  storage threshold, research finished.

Nothing here decides to spend resources; that stays in the planner/executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from newstar_sdk.events import ALERT_MESSAGE_CATEGORIES, Event


@dataclass(frozen=True)
class Reaction:
    kind: str  # "alert" | "info"
    message: str
    event: Event

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "event": self.event.as_dict(),
        }


def _message_reaction(event: Event) -> Reaction:
    data = event.data
    category = int(data.get("category", 0))
    name = data.get("name") or f"分类 {category}"
    new = int(data.get("new", 0))
    unread = int(data.get("unread", 0))
    if category in ALERT_MESSAGE_CATEGORIES:
        return Reaction("alert", f"收到{name}：新增 {new} 条，未读 {unread}", event)
    return Reaction("info", f"新消息（{name}）：{new} 条", event)


def _reaction(event: Event) -> Reaction | None:
    if event.kind == "message_new":
        return _message_reaction(event)
    if event.kind == "energy_deficit":
        return Reaction(
            "alert",
            f"能源转负：可用 {event.data.get('available', 0):,.0f}",
            event,
        )
    if event.kind == "queue_empty":
        return Reaction("info", f"队列空闲：{event.data.get('queue', '')}", event)
    if event.kind == "planet_lost":
        return Reaction("alert", f"星球丢失：{event.data.get('planet_id')}", event)
    if event.kind == "snapshot_gap":
        return Reaction(
            "alert",
            f"采集断档：{event.data.get('seconds', 0)} 秒",
            event,
        )
    if event.kind == "storage_full":
        return Reaction(
            "info",
            f"资源 {event.data.get('resource')} 占用 {event.data.get('usage', 0):.0%}"
            "（仓容语义待核实）",
            event,
        )
    if event.kind == "research_completed":
        return Reaction(
            "info",
            f"研究完成：{event.data.get('element_id')} "
            f"{event.data.get('from')}→{event.data.get('to')}",
            event,
        )
    return None


def react(events: Iterable[Event]) -> List[Reaction]:
    """Map events to reactions; unknown events are ignored."""

    reactions: List[Reaction] = []
    for event in events:
        reaction = _reaction(event)
        if reaction is not None:
            reactions.append(reaction)
    return reactions


def has_alert(reactions: Iterable[Reaction]) -> bool:
    return any(reaction.kind == "alert" for reaction in reactions)
