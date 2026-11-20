"""Instance limits that no page exposes.

The game enforces a maximum number of entries per queue but never prints that
number on a page: it only says "你最多只能添加 N 个…队列" once the request is
already over the limit.  These values are therefore strategy facts verified on
the live instance, not parsed page data.  They live in one module so the brief
and the tools cannot disagree about them.

Evidence (2026-11-19, 战争宇宙：猛犸):

* a shipyard order with 6 different ship types hit the queue limit, so the
  shipyard queue holds at most 5 entries;
* the game's rejection text for the building queue names 5 as well.

Override per run with ``--shipyard-slots`` when another instance (or a queue
upgrade) changes the number.
"""

SHIPYARD_QUEUE_SLOTS = 5

__all__ = ["SHIPYARD_QUEUE_SLOTS"]
