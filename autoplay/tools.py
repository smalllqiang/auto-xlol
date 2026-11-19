"""Typed game verbs exposed to the planning model.

Design rules (ADR-030 / ADR-031):

* **verbs are tools, nouns are arguments** -- there is one tool per game verb
  (build, research, ship, dispatch, recall, ...), never one per element.  This
  is why the tool count stays around a dozen no matter how long a plan is.
* **the game adjudicates** -- write tools do not enforce an allow-list or a
  budget, and they pass ``precheck=False`` so a local approximation cannot
  reject something the game would accept.  A rejection comes back as data.
* **requested vs actual** -- a multi-level batch may be partially queued, so a
  result always says what was asked for and what the page shows afterwards.
  ``ok`` being false must never be read as "nothing happened".

Read tools are safe to call at any time.  Write tools honour the registry's
``dry_run`` flag, which returns the arguments without touching the game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from newstar_sdk.constants import (
    CATEGORY_BUILDING,
    CATEGORY_DEFENSE,
    CATEGORY_SHIP,
    CATEGORY_TECHNOLOGY,
    resolve_element,
)
from newstar_sdk.exceptions import (
    ActionRejected,
    AuthenticationError,
    ConfigurationError,
    GameError,
    NewStarError,
    SessionExpired,
    TransportError,
)

from .planner import Plan, compute_plan

#: Tools that never change the account.  Everything else is a write.
READ_TOOLS = frozenset({"observe", "plan", "messages"})


def _field(entry: Any, name: str, default: Any = None) -> Any:
    """Read a queue entry as either an object or a plain dict (tests use both)."""

    if isinstance(entry, Mapping):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _ids(entries: Iterable[Any], element_id: int) -> List[Any]:
    return [
        entry
        for entry in entries
        if _field(entry, "element_id") is not None
        and int(_field(entry, "element_id")) == int(element_id)
    ]


def _targets(entries: Iterable[Any], element_id: int) -> List[int]:
    return sorted(
        int(_field(entry, "target_level"))
        for entry in _ids(entries, element_id)
        if _field(entry, "target_level") is not None
    )


@dataclass
class ToolResult:
    """The only thing a tool ever returns to the model."""

    ok: bool
    status: str  # "ok" | "partial" | "rejected" | "dry_run" | "error"
    code: str = ""
    retryable: bool = False
    before_post: Optional[bool] = None
    message: str = ""
    requested: Dict[str, Any] = field(default_factory=dict)
    actual: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(self.ok),
            "status": self.status,
            "code": self.code,
            "retryable": bool(self.retryable),
            "message": self.message,
            "requested": dict(self.requested),
            "actual": dict(self.actual),
            "evidence": dict(self.evidence),
        }
        if self.before_post is not None:
            payload["before_post"] = bool(self.before_post)
        return payload


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    kind: str  # "read" | "write"

    def as_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _element_schema(kind: str) -> Dict[str, Any]:
    return {
        "type": ["integer", "string"],
        "description": (
            f"{kind} 的元素 ID 或页面上的名字（例如 1 / 合金冶炼厂）。"
            "只能使用简报或 plan 工具里出现过的名字。"
        ),
    }


_LEVELS_SCHEMA = {
    "type": "integer",
    "minimum": 1,
    "description": "一次提交要升多少级（游戏会展开成同样多的队列项）。",
}


def translate(exc: BaseException) -> ToolResult:
    """Turn an SDK exception into a result the model can branch on."""

    if isinstance(exc, ActionRejected):
        before = bool(getattr(exc, "before_post", False))
        return ToolResult(
            ok=False,
            status="rejected",
            code="REJECTED_BEFORE_POST" if before else "REJECTED_AFTER_POST",
            retryable=before,
            before_post=before,
            message=str(exc),
        )
    if isinstance(exc, GameError):
        return ToolResult(
            ok=False,
            status="rejected",
            code="GAME_ERROR",
            retryable=False,
            message=str(exc),
        )
    if isinstance(exc, SessionExpired):
        return ToolResult(
            ok=False,
            status="error",
            code="SESSION_EXPIRED",
            retryable=True,
            message=str(exc),
        )
    if isinstance(exc, TransportError):
        return ToolResult(
            ok=False,
            status="error",
            code="TRANSPORT_ERROR",
            retryable=True,
            message=str(exc),
        )
    if isinstance(exc, (AuthenticationError, ConfigurationError)):
        return ToolResult(
            ok=False,
            status="error",
            code=type(exc).__name__.upper(),
            retryable=False,
            message=str(exc),
        )
    if isinstance(exc, ValueError):
        return ToolResult(
            ok=False,
            status="error",
            code="INVALID_ARGS",
            retryable=False,
            message=str(exc),
        )
    if isinstance(exc, NewStarError):
        return ToolResult(
            ok=False,
            status="error",
            code=type(exc).__name__.upper(),
            retryable=False,
            message=str(exc),
        )
    return ToolResult(
        ok=False,
        status="error",
        code="INTERNAL_ERROR",
        retryable=False,
        message=f"{type(exc).__name__}: {exc}",
    )


class ToolRegistry:
    """The verb set, bound to one client instance.

    There is deliberately no allow-list and no budget here (ADR-030).  The only
    gate left is ``dry_run``, which is the operator's preview switch, and
    ``state/STOP``, which the caller checks before running anything.
    """

    def __init__(self, client: Any, *, dry_run: bool = True) -> None:
        self.client = client
        self.dry_run = dry_run

    # -- read tools ---------------------------------------------------------

    def _specs(self) -> Dict[str, ToolSpec]:
        return {
            "plan": ToolSpec(
                name="plan",
                description=(
                    "跑一遍确定性规划器，返回阶段、目标与候选（含回本时间、缺口、"
                    "依赖深度）。数字由代码算好，不要自己估算；需要新数字就调它。"
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                kind="read",
            ),
            "observe": ToolSpec(
                name="observe",
                description="读取账号全量状态快照（资源、队列、舰队、研究、消息计数等）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "可选，只取这些节：overview/research/bonuses/production/"
                                "queues/fleets/messages。省略取全部。"
                            ),
                        }
                    },
                    "additionalProperties": False,
                },
                kind="read",
            ),
            "messages": ToolSpec(
                name="messages",
                description="读取消息（默认收件箱）。用于发现被侦察、战斗报告等。",
                parameters={
                    "type": "object",
                    "properties": {
                        "category": {"type": "integer", "description": "消息分类 id，省略为全部收件箱。"},
                        "page": {"type": "integer", "minimum": 1},
                        "unread_only": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                kind="read",
            ),
            "upgrade_building": ToolSpec(
                name="upgrade_building",
                description=(
                    "建造/升级建筑。默认一次一级；游戏自己裁决资源、前置、队列与面积，"
                    "被拒会作为结果返回，不会抛给模型。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "element": _element_schema("建筑"),
                        "levels": _LEVELS_SCHEMA,
                    },
                    "required": ["element"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "upgrade_building_to": ToolSpec(
                name="upgrade_building_to",
                description=(
                    "把建筑一次提交到指定等级（会展开成多条队列项）。注意：只有第一级"
                    "在提交时检查并扣款，后面的级不预付，可能中途资源不够。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "element": _element_schema("建筑"),
                        "target_level": {"type": "integer", "minimum": 1},
                    },
                    "required": ["element", "target_level"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "destroy_building": ToolSpec(
                name="destroy_building",
                description="拆除一级建筑（不可逆，返还一半成本）。",
                parameters={
                    "type": "object",
                    "properties": {"element": _element_schema("建筑")},
                    "required": ["element"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "research": ToolSpec(
                name="research",
                description="研究科技。默认一次一级；合法性由游戏裁决。",
                parameters={
                    "type": "object",
                    "properties": {
                        "technology": _element_schema("科技"),
                        "levels": _LEVELS_SCHEMA,
                    },
                    "required": ["technology"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "research_to": ToolSpec(
                name="research_to",
                description="把科技一次提交到指定等级（展开成多条队列项）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "technology": _element_schema("科技"),
                        "target_level": {"type": "integer", "minimum": 1},
                    },
                    "required": ["technology", "target_level"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "build_ships": ToolSpec(
                name="build_ships",
                description="在船坞建造舰船或防御。游戏会按资源上限截断，结果里会报告实际数量。",
                parameters={
                    "type": "object",
                    "properties": {
                        "ships": {
                            "type": "object",
                            "description": "元素 ID 或名字 → 数量，例如 {\"202\": 3}。",
                            "additionalProperties": {"type": "integer", "minimum": 1},
                        },
                        "mode": {"type": "string", "enum": ["fleet", "defense"]},
                    },
                    "required": ["ships"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "send_fleet": ToolSpec(
                name="send_fleet",
                description=(
                    "发送舰队（三步流程）。任务可用性由游戏在第二步给出；成功后返回"
                    "确认页解析出的任务、坐标、燃料与舰船。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": ["string", "array"],
                            "description": "目标坐标，例如 \"3:505:16\" 或 [3, 505, 16]。",
                        },
                        "ships": {
                            "type": "object",
                            "description": "元素 ID 或名字 → 数量。",
                            "additionalProperties": {"type": "integer", "minimum": 1},
                        },
                        "mission": {
                            "type": ["integer", "string"],
                            "description": "任务 ID 或名字，例如 15 / expedition / transport。",
                        },
                        "resources": {
                            "type": "object",
                            "description": "随船资源，例如 {\"901\": 1000}。",
                            "additionalProperties": {"type": "number"},
                        },
                        "stay_time": {"type": "integer", "description": "探险停留小时数，必须来自第二步页面的选项。"},
                        "speed": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["target", "ships", "mission"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "recall_fleet": ToolSpec(
                name="recall_fleet",
                description="召回自己的舰队。",
                parameters={
                    "type": "object",
                    "properties": {"fleet_id": {"type": "integer"}},
                    "required": ["fleet_id"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "set_production_ratios": ToolSpec(
                name="set_production_ratios",
                description=(
                    "设置生产比例（百分数 0–100，步长 10）。表单必须整表提交，"
                    "工具会先读当前值再合并，并按页面回读验证。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ratios": {
                            "type": "object",
                            "description": "元素 ID 或名字 → 百分比，例如 {\"1\": 80}。",
                            "additionalProperties": {"type": "integer", "minimum": 0, "maximum": 100},
                        }
                    },
                    "required": ["ratios"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
            "claim_daily_bonus": ToolSpec(
                name="claim_daily_bonus",
                description="领取每日签到（访问即领取，冷却期内不会访问该页）。",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                kind="write",
            ),
            "cancel_queue": ToolSpec(
                name="cancel_queue",
                description="取消队列项。position=0 是当前进行中的那条。",
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["building", "research", "shipyard"]},
                        "position": {"type": "integer", "minimum": 0},
                        "mode": {"type": "string", "enum": ["fleet", "defense"]},
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
                kind="write",
            ),
        }

    def specs(self) -> List[ToolSpec]:
        return list(self._specs().values())

    def schema(self) -> List[Dict[str, Any]]:
        """OpenAI-compatible ``tools`` payload."""

        return [spec.as_openai() for spec in self.specs()]

    def call(self, name: str, args: Optional[Mapping[str, Any]] = None) -> ToolResult:
        spec = self._specs().get(str(name))
        if spec is None:
            return ToolResult(
                ok=False,
                status="error",
                code="UNKNOWN_TOOL",
                message=f"没有这个工具：{name}；可用：{', '.join(sorted(self._specs()))}",
            )
        if not isinstance(args, (Mapping, type(None))):
            return ToolResult(ok=False, status="error", code="INVALID_ARGS", message="参数必须是对象")
        payload = dict(args or {})

        handler = getattr(self, f"_tool_{spec.name}", None)
        if handler is None:
            return ToolResult(ok=False, status="error", code="INTERNAL_ERROR", message="handler 缺失")

        if spec.kind == "write" and self.dry_run:
            return ToolResult(
                ok=True,
                status="dry_run",
                code="DRY_RUN",
                message=f"预览：未执行 {spec.name}",
                requested=payload,
            )

        try:
            return handler(payload)
        except BaseException as exc:  # noqa: BLE001 - every failure is data here
            return translate(exc)

    # -- read handlers ------------------------------------------------------

    def _tool_plan(self, args: Mapping[str, Any]) -> ToolResult:
        buildings = self.client.get_buildings()
        research = self.client.get_research()
        production = self.client.get_production()
        resources = self.client.get_overview().resources.amounts
        plan: Plan = compute_plan(buildings, research, production, resources)
        return ToolResult(
            ok=True,
            status="ok",
            message=f"阶段 {plan.phase_name}，{len(plan.actions)} 条候选",
            evidence=plan.as_dict(),
        )

    def _tool_observe(self, args: Mapping[str, Any]) -> ToolResult:
        from newstar_sdk.snapshot import build_snapshot

        sections = args.get("sections")
        kwargs: Dict[str, Any] = {}
        if sections:
            wanted = {str(item) for item in sections}
            mapping = {
                "research": "include_research",
                "bonuses": "include_bonuses",
                "production": "include_production",
                "queues": "include_queues",
                "fleets": "include_fleets",
                "messages": "include_messages",
            }
            for key, flag in mapping.items():
                kwargs[flag] = key in wanted
        snapshot = build_snapshot(self.client, **kwargs)
        return ToolResult(
            ok=True,
            status="ok",
            message="快照已读取",
            evidence=snapshot,
        )

    def _tool_messages(self, args: Mapping[str, Any]) -> ToolResult:
        page = int(args.get("page", 1))
        category = args.get("category")
        result = self.client.get_messages(
            category=int(category) if category is not None else "all",
            page=page,
        )
        messages = [message.as_dict() for message in result.messages]
        if args.get("unread_only"):
            messages = [message for message in messages if message.get("unread")]
        return ToolResult(
            ok=True,
            status="ok",
            message=f"{len(messages)} 条消息",
            evidence={
                "page": result.page,
                "pages": result.pages,
                "messages": messages,
            },
        )

    # -- write handlers -----------------------------------------------------

    def _tool_upgrade_building(self, args: Mapping[str, Any]) -> ToolResult:
        levels = int(args.get("levels", 1))
        return self._queue_build("building", args["element"], levels=levels)

    def _tool_upgrade_building_to(self, args: Mapping[str, Any]) -> ToolResult:
        return self._queue_build("building", args["element"], target_level=int(args["target_level"]))

    def _tool_research(self, args: Mapping[str, Any]) -> ToolResult:
        return self._queue_build("research", args["technology"], levels=int(args.get("levels", 1)))

    def _tool_research_to(self, args: Mapping[str, Any]) -> ToolResult:
        return self._queue_build(
            "research", args["technology"], target_level=int(args["target_level"])
        )

    def _queue_build(
        self,
        kind: str,
        element: Any,
        *,
        levels: Optional[int] = None,
        target_level: Optional[int] = None,
    ) -> ToolResult:
        """Shared path for building/research: let the game judge, report actual.

        ``precheck=False`` hands the decision to the game; ``verify=True`` is
        kept because it is *observation*, not permission: it is what proves the
        request reached the page.  When it fails, the queue is read once to tell
        "nothing happened" apart from "part of the batch happened".
        """

        category = CATEGORY_BUILDING if kind == "building" else CATEGORY_TECHNOLOGY
        element_id = int(resolve_element(element, category))
        requested: Dict[str, Any] = {"element_id": element_id}
        if levels is not None:
            requested["levels"] = levels
        if target_level is not None:
            requested["target_level"] = target_level

        call = self.client.upgrade_building if kind == "building" else self.client.research
        read_queue = (
            self.client.get_building_queue if kind == "building" else self.client.get_research_queue
        )
        action = "建造" if kind == "building" else "研究"

        try:
            queue = call(
                element_id,
                levels=levels if levels is not None else 1,
                target_level=target_level,
                precheck=False,
                verify=True,
            )
        except ActionRejected as exc:
            # POST went out and the page does not show the target: find out how
            # much actually landed instead of guessing.
            try:
                queue = read_queue()
            except NewStarError:
                queue = []
            targets = _targets(queue, element_id)
            return ToolResult(
                ok=False,
                status="partial" if targets else "rejected",
                code="PARTIAL_QUEUE" if targets else "NOT_QUEUED",
                retryable=True,
                before_post=False,
                message=f"{action}请求没有完全生效：{exc}",
                requested=requested,
                actual={"target_levels": targets},
            )
        except NewStarError as exc:
            result = translate(exc)
            result.requested = requested
            return result

        targets = _targets(queue, element_id)
        return ToolResult(
            ok=True,
            status="ok",
            message=f"{action}请求已入队",
            requested=requested,
            actual={"target_levels": targets},
        )

    def _tool_build_ships(self, args: Mapping[str, Any]) -> ToolResult:
        ships = args["ships"]
        mode = str(args.get("mode", "fleet"))
        category = CATEGORY_SHIP if mode == "fleet" else CATEGORY_DEFENSE
        resolved: Dict[int, int] = {}
        for key, value in dict(ships).items():
            element_id = int(resolve_element(key, category))
            resolved[element_id] = resolved.get(element_id, 0) + int(value)

        try:
            queue = self.client.build_ships(resolved, mode=mode, precheck=False, verify=False)
        except NewStarError as exc:
            result = translate(exc)
            result.requested = {"ships": resolved, "mode": mode}
            return result

        actual: Dict[str, int] = {}
        for entry in queue:
            element_id = _field(entry, "element_id")
            amount = _field(entry, "amount")
            if element_id is None or amount is None:
                continue
            actual[str(int(element_id))] = actual.get(str(int(element_id)), 0) + int(amount)

        missing = {str(key): value for key, value in resolved.items() if actual.get(str(key), 0) < value}
        status = "ok" if not missing else "partial"
        return ToolResult(
            ok=not missing,
            status=status,
            code="" if not missing else "PARTIAL_SHIPYARD",
            retryable=bool(missing),
            message="船坞请求已提交" if not missing else "船坞只排入了部分数量",
            requested={"ships": resolved, "mode": mode},
            actual={"queued": actual},
        )

    def _tool_send_fleet(self, args: Mapping[str, Any]) -> ToolResult:
        mission = args["mission"]
        try:
            dispatch = self.client.send_fleet(
                args["target"],
                args["ships"],
                mission,
                speed=int(args.get("speed", 10)),
                resources=args.get("resources"),
                stay_time=int(args.get("stay_time", 0)),
            )
        except NewStarError as exc:
            result = translate(exc)
            result.requested = {"target": args["target"], "mission": mission}
            return result

        evidence = dispatch.as_dict() if hasattr(dispatch, "as_dict") else dict(dispatch)
        confirmed = bool(evidence.get("mission") or evidence.get("target_coordinates"))
        return ToolResult(
            ok=confirmed,
            status="ok" if confirmed else "partial",
            code="" if confirmed else "NO_CONFIRMATION",
            retryable=False,  # a half-sent fleet must be re-observed, never retried
            message="舰队已发送（确认页已解析）" if confirmed else "没有拿到确认页",
            requested={"target": args["target"], "ships": args["ships"], "mission": mission},
            actual={"dispatch": evidence},
        )

    def _tool_recall_fleet(self, args: Mapping[str, Any]) -> ToolResult:
        fleet_id = int(args["fleet_id"])
        fleets = self.client.recall_fleet(fleet_id)
        still_flying = any(int(fleet.id) == fleet_id and not fleet.returning for fleet in fleets)
        return ToolResult(
            ok=not still_flying,
            status="ok" if not still_flying else "partial",
            code="" if not still_flying else "RECALL_UNCONFIRMED",
            retryable=not still_flying,
            message="召回已生效" if not still_flying else "召回后舰队仍在飞行",
            requested={"fleet_id": fleet_id},
            actual={"fleets": len(fleets)},
        )

    def _tool_set_production_ratios(self, args: Mapping[str, Any]) -> ToolResult:
        ratios = {
            int(resolve_element(key, CATEGORY_BUILDING)): int(value)
            for key, value in dict(args["ratios"]).items()
        }
        report = self.client.set_production_ratios(ratios)
        actual = {str(key): value for key, value in report.ratios.items()}
        missing = {
            key: value
            for key, value in {str(k): v for k, v in ratios.items()}.items()
            if actual.get(key) != value
        }
        return ToolResult(
            ok=not missing,
            status="ok" if not missing else "partial",
            code="" if not missing else "RATIO_MISMATCH",
            retryable=bool(missing),
            message="生产比例已按页面回读确认" if not missing else "部分比例没有生效",
            requested={"ratios": ratios},
            actual={"ratios": actual},
        )

    def _tool_claim_daily_bonus(self, args: Mapping[str, Any]) -> ToolResult:
        bonus = self.client.claim_daily_bonus()
        claimed = bool(getattr(bonus, "claimed", False))
        return ToolResult(
            ok=True,
            status="ok" if claimed else "skipped",
            code="" if claimed else "ON_COOLDOWN",
            retryable=False,
            message="签到奖励已领取" if claimed else "今天已领取或处于冷却",
            actual={"claimed": claimed},
        )

    def _tool_destroy_building(self, args: Mapping[str, Any]) -> ToolResult:
        from newstar_sdk.constants import resolve_element

        element_id = int(resolve_element(args["element"], CATEGORY_BUILDING))
        queue = self.client.destroy_building(element_id)
        targets = _targets(queue, element_id)
        return ToolResult(
            ok=bool(targets),
            status="ok" if targets else "rejected",
            code="" if targets else "NOT_QUEUED",
            retryable=not targets,
            message="拆除已入队" if targets else "拆除没有出现在队列里",
            requested={"element_id": element_id},
            actual={"target_levels": targets},
        )

    def _tool_cancel_queue(self, args: Mapping[str, Any]) -> ToolResult:
        kind = str(args["kind"])
        position = int(args.get("position", 0))
        if kind == "building":
            queue = self.client.cancel_building_queue(position)
        elif kind == "research":
            queue = self.client.cancel_research_queue(position)
        elif kind == "shipyard":
            queue = self.client.cancel_ship_queue(position, mode=str(args.get("mode", "fleet")))
        else:
            raise ValueError(f"unknown queue kind: {kind}")
        return ToolResult(
            ok=True,
            status="ok",
            message=f"{kind} 队列已取消 position={position}，剩余 {len(queue)} 项",
            requested={"kind": kind, "position": position},
            actual={"remaining": len(queue)},
        )


__all__ = [
    "READ_TOOLS",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "translate",
]
