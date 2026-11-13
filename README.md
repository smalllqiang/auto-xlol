# New-Star Python SDK

`newstar-sdk` 是一个用于 [New-Star](https://github.com/Yaro2709/New-Star)（2Moons 系）网页游戏的 Python SDK。它直接使用游戏自身的 HTTP 页面和表单，把登录、资源读取、建筑、研究、船坞、星图、舰队等基础操作封装为可复用的 Python 接口，供自动化脚本、工具或后续服务调用。

> 非官方项目，与 New-Star 上游及游戏运营方无隶属关系。使用前请确认游戏规则允许自动化操作，并控制请求频率。

## 特性

- 登录、会话保存/恢复、会话过期后自动重登（需提供密码）
- 读取账号、资源、产能、星球、活动状态
- 读取消息（分类/分页/未读计数）、加成总表、生产控制明细
- 全量状态快照、快照 diff 事件、趋势统计
- 建筑升级、拆除、建造队列取消
- 科技研究、研究队列取消
- 船坞造舰/造防御、船坞队列取消
- 星图读取
- 舰队发送（运输、派遣、侦察、回收、殖民、远征等）与召回
- 队列等待
- 原始页面/HTTP 请求逃生通道
- 命令行工具 `newstar`

## 安装

需要 Python 3.9 或更高版本。

```bash
uv venv
uv pip install -e .
source .venv/bin/activate
# 或者：uv run newstar ...

# 不使用 uv 时：
python3 -m venv .venv
.venv/bin/pip install -e .
```

验证安装：

```bash
newstar version
# 未激活虚拟环境时：
.venv/bin/newstar version
```

## 快速开始

```python
import os

from newstar_sdk import NewStarClient

client = NewStarClient(
    os.environ["NEWSTAR_BASE_URL"],          # 例如 https://ol.lstyxl.com/
    os.environ["NEWSTAR_USERNAME"],
    os.environ["NEWSTAR_PASSWORD"],
    universe=os.getenv("NEWSTAR_UNIVERSE"),  # 例如 CQBX8573；省略则使用登录页第一个宇宙
    verify_ssl=os.getenv("NEWSTAR_VERIFY_SSL", "1").strip().lower() not in {"0", "false", "no", "off"},
)

client.login()

overview = client.get_overview()
print(overview.username, overview.planet.coordinates)
print(
    overview.resources.metal,
    overview.resources.crystal,
    overview.resources.deuterium,
)

for building in client.get_buildings():
    print(building.id, building.name, building.level, building.cost)

client.close()
```

也可以使用上下文管理器（`with` 不会自动登录，需显式调用 `login()` 或依赖各方法内部的 `ensure_login()`）：

```python
with NewStarClient(base_url, username, password, verify_ssl=False) as client:
    client.login()
    print(client.get_resources().metal)
```

### 私有实例的注意事项

New-Star 私有实例常见的适配点是：

- 证书已过期：需要 `verify_ssl=False`，或者设置 `--no-verify-ssl` / `NEWSTAR_VERIFY_SSL=0`。
- 界面为中文，且有自定义元素名；SDK 通过元素 ID、CSS 结构和 `DatatList` 数据识别，不依赖界面文案。
- 部分模块可能被运营方禁用；访问时会得到游戏错误页并抛出 `GameError`。

不要把账号、密码或 `.session.json` 提交到版本库。

## 常用任务

### 读取资源与星球

```python
overview = client.get_overview()
resources = overview.resources

print(resources.metal, resources.metal_per_hour)
print(resources.crystal, resources.deuterium)
print(resources.dark_matter)

print(overview.status.research, overview.status.building, overview.status.shipyard)
print(client.get_planets())

# 切换当前星球（多星球账号）
# client.select_planet(planet_id)
```

### 消息与加成

```python
# 消息列表是 AJAX 渲染的，SDK 已经封装好
counters = client.get_message_counters()
print(counters[15].unread, counters[15].total)   # 探险报告

page = client.get_messages("expedition")         # 也支持 all/system/battle/outbox...
for message in page:
    print(message.time, message.sender, message.body, message.coordinates)

# 加成总表：所有成本/产能计算的乘数来源
bonuses = client.get_bonuses()
print(bonuses.percent("全资源生产加成"), bonuses.multiplier("合金采集加成"))
```

### 建筑

```python
buildings = {item.id: item for item in client.get_buildings()}
metal_mine = buildings[1]
print(metal_mine.level, metal_mine.cost, metal_mine.requirements_met)

# 升 1 级；也可以指定目标等级 target_level=5
queue = client.upgrade_building("metal", levels=1)
print(queue)

# 取消当前正在建造的项目（0 表示第一项）
client.cancel_building_queue(0)

# 拆除一级（危险操作）
# client.destroy_building("metal")
```

### 研究

```python
techs = {item.id: item for item in client.get_research()}
print(techs[108].name, techs[108].cost, techs[108].requirements_met)

queue = client.research("computer", levels=1)
print(queue)
client.cancel_research_queue(0)
```

### 船坞

```python
ships = {item.id: item for item in client.get_ships()}
print(ships[202].name, ships[202].max_buildable)

queue = client.build_ships({"small_cargo": 1})
print(queue)

# 防御
client.build_ships({"light_laser": 2}, mode="defense")
client.cancel_ship_queue(0, mode="defense")
```

`build_ships` 会在本地先检查前置条件和 `max_buildable`，避免明显无效的请求；资源不足或前置条件缺失时抛出 `ActionRejected`。

### 星图

```python
view = client.get_galaxy(3, 505)
for slot in view.slots:
    print(
        slot.position,
        slot.planet_name,
        slot.player_name,
        slot.is_own,
        slot.has_moon,
        slot.actions,
    )
```

### 舰队

```python
available = client.get_available_ships()
print(available)

result = client.send_fleet(
    "3:505:9",                    # 目标坐标；也支持 Coordinates 或 (g, s, p)
    {"small_cargo": 1},           # 舰船 ID/名称 -> 数量
    mission="transport",          # 任务 ID 或名称
    speed=10,
    resources={"metal": 100},     # 运输任务至少需要一种资源
)
print(result.mission_name, result.target_coordinates, result.arrival_time)

for fleet in client.get_fleets():
    print(fleet.id, fleet.mission, fleet.returning)

# 召回
# client.recall_fleet(fleet_id)
```

### 队列

```python
print(client.get_building_queue())
print(client.get_research_queue())
print(client.get_ship_queue(mode="fleet"))

# 阻塞等待队列清空
client.wait_for_building_queue(timeout=600)
```

### 会话

```python
client.save_session("my.session.json")

# 之后：
client = NewStarClient(base_url, username, password, verify_ssl=False)
if not client.load_session("my.session.json"):
    # 会话已失效，用构造时提供的账号密码重新登录
    client.login()
```

会话文件只保存 Cookie 和非敏感状态，不保存密码；在支持的平台上权限会设置为 `0600`。

### 原始访问

SDK 未覆盖的页面可以直接取 HTML，或者发原始请求：

```python
html = client.get_page("messages")
response = client.request("GET", "game.php?page=overview")
response = client.request("POST", "game.php?page=...", data={"key": "value"})
```

## 策略层（规划器）

`autoplay` 包在 SDK 之上做只读规划：识别当前阶段、展开目标依赖链、按回本时间
排序产出建筑。

```python
from autoplay import build_plan

plan = build_plan(client)
print(plan.phase_name, plan.goal_status)
for action in plan.actions:
    print(action.category, action.name, action.current_level, "→", action.target_level)
```

```bash
.venv/bin/python scripts/plan.py --no-verify-ssl   # 文字计划
.venv/bin/python scripts/plan.py --json            # 机器可读
.venv/bin/python scripts/run_once.py --no-verify-ssl  # 影子模式，只说不做
```

执行需要显式授权：`--execute --allow <元素>`。执行前会检查队列、资源和单步预算，
只做一步，并把结果写进 `state/journal.jsonl`；`state/STOP` 存在时拒绝运行。

无人值守用常驻循环（默认也是 dry-run）：

```bash
.venv/bin/python scripts/run_forever.py --no-verify-ssl --once   # 跑一轮
.venv/bin/python scripts/run_forever.py --no-verify-ssl \
    --execute --allow 自律工厂                                     # 常驻
```

单实例锁、`state/STOP` 急停、失败指数退避都内建；默认每 12 轮写一次快照。
每轮会比对消息计数，间谍/战斗报告触发告警（`--pause-on-alert` 可暂停执行）；
日志与快照超过 `--rotate-mb` 自动切成 `.1`。

## 元素 ID

元素 ID 来自 New-Star / 2Moons 的通用编号，实际游戏可能增加或改名。SDK 会优先读取页面上的名称，静态表只用于“名称 → ID”的便捷解析。

| 类别 | 常用 ID |
| --- | --- |
| 建筑 | 1 合金冶炼厂、2 水晶催化厂、3 重氢精炼厂、4 幽能发电站、14 自律工厂、15 帝国主机、21 星际船坞、31 科研实验室 |
| 科技 | 106 间谍技术、108 计算机技术、109 武器技术、110 护盾技术、111 装甲技术、113 能源技术、115 工质推进器 |
| 舰船 | 202 轻型运输舰、203 重型运输舰、204 轻型战机、205 重型战机、206 巡洋舰、207 战列舰、208 殖民舰、209 回收舰、210 间谍探针、214 死星 |
| 防御 | 401 导弹发射器、402 轻型激光塔、403 重型激光塔、404 高斯炮、405 离子炮、406 等离子炮、407 小型护盾、408 大型护盾 |

完整映射见 [`newstar_sdk/constants.py`](newstar_sdk/constants.py)。方法中的元素参数同时接受 ID、中文名、英文名和常用缩写（如 `"metal"`、`"small_cargo"`）。

## 命令行

以下命令假设已激活 `.venv`（或把 `.venv/bin` 加入 `PATH`）。全局参数放在子命令之前：

```bash
export NEWSTAR_BASE_URL=https://ol.lstyxl.com/
export NEWSTAR_USERNAME=your-user
export NEWSTAR_PASSWORD=your-password
export NEWSTAR_UNIVERSE=CQBX8573
export NEWSTAR_VERIFY_SSL=0

newstar overview
newstar buildings
newstar research
newstar ships
newstar galaxy --galaxy 3 --system 505
newstar fleets
newstar messages --category expedition
newstar bonuses
newstar queue build
newstar --json overview

# 写操作默认要求确认；非交互场景加 --yes
newstar build metal --levels 1 --yes
newstar research computer --yes
newstar build-ship small_cargo 1 --yes
newstar send-fleet --target 3:505:9 --mission transport --ship small_cargo=1 --metal 100 --yes
```

查看全部命令：

```bash
newstar --help
newstar send-fleet --help
```

## 错误处理

SDK 自定义异常都继承自 `NewStarError`；参数校验错误会抛 `ValueError`，队列等待超时会抛内置 `TimeoutError`。

| 异常 | 场景 |
| --- | --- |
| `ConfigurationError` | 缺少 URL/账号/密码，或宇宙名不存在 |
| `AuthenticationError` | 登录被拒绝 |
| `SessionExpired` | 会话失效且无法自动重登 |
| `TransportError` | 网络错误、超时、HTTP 4xx/5xx |
| `GameError` | 游戏返回错误页（如模块禁用、资源不足、没有选择舰船） |
| `ActionRejected` | HTTP 请求成功，但游戏没有执行该操作 |
| `ParseError` | 页面结构与 SDK 预期不符（通常意味着实例改动较大） |

```python
from newstar_sdk import ActionRejected, GameError, NewStarError

try:
    client.upgrade_building("metal")
except ActionRejected as exc:
    print("操作未被接受:", exc)
except GameError as exc:
    print("游戏错误:", exc)
except NewStarError as exc:
    print("SDK 错误:", exc)
```

## 示例

```bash
.venv/bin/python examples/read_state.py
.venv/bin/python examples/manage_queue.py --yes   # 入队后立即取消
.venv/bin/python examples/send_fleet.py --help
```

## 测试

开发依赖（pytest / ruff）：

```bash
uv pip install -e ".[dev]"
```

```bash
.venv/bin/python -m pytest -q          # 单元测试，使用离线 HTML 固定样本
.venv/bin/ruff check .
```

针对真实实例的只读集成测试默认跳过；设置环境变量后运行：

```bash
NEWSTAR_BASE_URL=... NEWSTAR_USERNAME=... NEWSTAR_PASSWORD=... \
NEWSTAR_UNIVERSE=... NEWSTAR_VERIFY_SSL=0 \
.venv/bin/python -m pytest -q -m live
```

手工冒烟脚本：

```bash
.venv/bin/python scripts/live_smoke.py
.venv/bin/python scripts/live_smoke.py --mutate   # 额外测试“入队→取消”
```

## 目录

```
autoplay/         # 策略层（易变）
  phases.py       # 阶段识别
  economy.py      # 等价换算 / 增产估算 / 回本
  planner.py      # 依赖展开 + ROI 排序
  executor.py     # 队列感知 + 安全闸
newstar_sdk/      # 客户端（稳定契约）
  client.py       # HTTP 会话 + 高层操作
  parsers.py      # 页面解析（纯函数）
  models.py       # 返回的数据结构
  snapshot.py     # 全量状态快照
  events.py       # 快照 diff 事件
  report.py       # 趋势统计
  constants.py    # 元素/任务/资源 ID 与名称
  exceptions.py   # 异常类型
  utils.py        # 数值、JS 对象、坐标等工具
  cli.py          # newstar 命令行
docs/
  usage.md        # API 与脚本用法
  architecture.md # 结构、数据来源、取舍与排错
  decisions.md    # 设计决策记录（ADR）
  roadmap.md      # 任务与验收标准
  p0-page-census.md # 实例页面普查
tests/            # 离线样本测试 + 可选 live 测试
scripts/          # 普查、采集、报表、计划、执行
examples/         # 可运行示例
AGENTS.md         # 协作与代理规则
```

## 当前范围

当前版本覆盖登录/会话、账号与资源读取、建筑、研究、船坞、星图、舰队发送/召回、队列、
消息、加成、生产控制、状态快照/事件与原始请求。

当前不提供：战斗报告详情、市场/交易、联盟、生产比例写入、殖民/月球管理、聊天、军官/商店等页面级封装。这些页面仍可通过 `get_page()` / `request()` 访问。

## 文档

- [使用说明](docs/usage.md)
- [架构与设计](docs/architecture.md)
- [设计决策（ADR）](docs/decisions.md)
- [路线图与验收](docs/roadmap.md)
- [页面普查](docs/p0-page-census.md)
- [协作与代理规则](AGENTS.md)

## 许可

MIT。New-Star 上游同样使用 MIT 许可；归属说明见 [NOTICE](NOTICE)。
