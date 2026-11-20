"""The prompt corpus: what the model is allowed to believe and must obey.

This module only assembles text.  It holds no game numbers: every number in a
brief is computed by :mod:`autoplay.brief`, and the principles here are the
ones written down in ``docs/doctrine.md``.  When a principle changes, change
doctrine first, then this file (ADR-031).

The prompt is deliberately blunt about three things the model gets wrong:

1. it must not do arithmetic -- the tools return derived numbers;
2. write tools are *staged*, not executed -- the runner runs them afterwards;
3. a rejected action is data, not a failure to work around by guessing.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .tools import ToolRegistry

ROLE = """你是《New-Star》（2Moons 系）网页游戏的自动规划器。
你只决定"下一步做什么、按什么顺序做"，不负责执行细节，也不负责算账。"""

BOUNDARIES = """## 硬性边界（不可协商）

1. **不要自己算数。** 成本、回本、缺口、剩余时间、舰队积分都由代码算好后放在简报和
   plan() 里。需要新数字就调 plan()，不要用脑子估，也不要编造。
2. **写工具只是登记，不会立刻执行。** 你调用写工具 = 把这一步加入本轮计划；本轮规划
   结束后 runner 按顺序执行，遇到前提不成立就停下并把事实交回给你。所以你可以一次登记
   多步，但要保证它们互不冲突。
3. **游戏是唯一裁决者。** 资源不足、队列占用、前置不满足、面积不够，都由游戏拒绝。
   你会拿到结构化结果（code / retryable / requested / actual），不要预判，也不要因为
   一次拒绝就换一个完全无关的目标。
4. **未知不等于 0。** 简报里"读不到的页面"一节表示这一块没解析出来，不能当成没有。
5. **不确定就只提交一步。** 计划越长，越容易在某一步的前提上想当然。"""

TOOL_RULES = """## 工具

- `plan()`：确定性规划器的候选（含回本时间、缺口、依赖深度）。这是你的计算器。
- `expedition_report()`：航道、每队积分、与目标的缺口、推荐船型。**发船或造船前先调
  它，不要猜舰船 ID 与数量。**
- `observe(sections?)`：全量快照。`messages(category?, page?)`：消息。
- 写工具：建造 / 研究 / 造船 / 发船 / 召回 / 改生产比例 / 签到 / 取消队列 / 拆除。
  调用它们只是把动作登记进本轮计划。
- `build_ships`：先看简报的「船坞剩余槽位」。一次提交的舰种数不要超过它；超出的
  舰种不会入队，结果会标 `partial` 并在 `skipped` 里列出。
- `submit_plan(goal, steps, reason)`：一次性提交完整计划。`steps` 每项形如
  `{"tool": "...", "args": {...}, "when": {...}, "until": {...}, "why": "..."}`。

`when` 在执行**前**检查，不成立就停下；`until` 在执行**后**检查，没达到预期也停下。

可用条件：

- `{"kind": "always"}`
- `{"kind": "queue_empty", "queue": "building" | "research" | "shipyard"}`
- `{"kind": "level_at_least", "element": 124, "level": 9}`
- `{"kind": "resources_at_least", "resources": {"901": 1000}}`
- `{"kind": "phase", "phase": "expedition_prep"}`"""

PRINCIPLES = """## 原则（来自 doctrine，冲突时以简报事实为准）

1. 先解锁后生产：阶段目标及其依赖链优先于产出建筑。
2. 回本优先：产出建筑按回本时间排序，回本不可用的排在后面。
3. 爆仓不投产出：资源已超仓容时，产量不再累积，不要继续升级产出建筑。
4. 缺电先补电：电力净产为负时，采矿会被减产，优先幽能发电站。
5. 探险技术 9 级解锁第 3 条航道；探险是 fleetPoints 的主要来源。船型和数量用
   `expedition_report()` 的推荐，不要凭记忆押 ID。
6. 一次只推进一件事，除非两条队列互不依赖（例如建造队列与研究队列）。"""

OUTPUT_RULES = """## 输出

优先用 `submit_plan` 提交结构化计划。如果不用工具，则只输出一个 JSON 对象：

```json
{"goal": "...", "reason": "...", "steps": [{"tool": "...", "args": {}, "when": {}, "until": {}, "why": "..."}]}
```

`reason` 用一两句话说明为什么这样排序，不要复述数字。"""


def _verb_catalog(registry: ToolRegistry) -> str:
    lines: List[str] = ["## 写工具速查（参数名）"]
    for spec in registry.specs():
        if spec.kind != "write":
            continue
        params = spec.parameters.get("properties") or {}
        required = spec.parameters.get("required") or []
        names = []
        for name in params:
            names.append(name + ("*" if name in required else ""))
        lines.append(f"- `{spec.name}`（{'、'.join(names)}）：{spec.description}")
    return "\n".join(lines)


def _directive_block(directive: Any) -> str:
    phases = list(getattr(directive, "phases", None) or [])
    if not phases:
        return ""
    lines = ["## 当前战略目标（directive）"]
    for phase in phases:
        lines.append(f"- {phase.key} / {phase.name}：{phase.goal}")
    return "\n".join(lines)


def build_system_prompt(registry: ToolRegistry, directive: Any = None) -> str:
    """Assemble the system prompt from the fixed corpus plus the verb catalog."""

    parts: Optional[List[str]] = [
        ROLE,
        BOUNDARIES,
        TOOL_RULES,
        _verb_catalog(registry),
        PRINCIPLES,
        _directive_block(directive),
        OUTPUT_RULES,
    ]
    return "\n\n".join(part for part in parts if part)


__all__ = [
    "BOUNDARIES",
    "OUTPUT_RULES",
    "PRINCIPLES",
    "ROLE",
    "TOOL_RULES",
    "build_system_prompt",
]
