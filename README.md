# New-Star 自动化

本项目的目标是**自动游玩 [New-Star](https://github.com/Yaro2709/New-Star)（2Moons 系）网页游戏**：
让一个账号在无人值守下持续发展，从读状态、定计划到执行动作全部由代码完成，AI 不参与
运行时决策。当前运行实例是魔改服「战争宇宙：猛犸」（New-Star 2.8.1）。

`newstar_sdk` 只是这个目标的一个组成部分——与游戏通信的客户端，不是项目本身。除它之外，
项目还包括确定性策略层、执行器、常驻守护和只读看板。

> 非官方项目，与 New-Star 上游及游戏运营方无隶属关系。使用前请确认游戏规则允许自动化操作，并控制请求频率。

## 组成

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 客户端 | `newstar_sdk/` | HTTP 会话、页面解析、数据模型、异常、`newstar` 命令行。稳定契约，不写策略。 |
| 策略层 | `autoplay/` | 阶段识别、经济估算、依赖展开 + ROI 排序、执行编排、探险/造船/签到。可以频繁改。 |
| AI 规划层 | `autoplay/brief.py`、`tools.py`、`runner.py`、`agent.py` | 事实 → 简报 → 模型规划 → 按前提执行；模型不碰算术、不直接写、合法性由游戏裁决。 |
| 入口 | `scripts/` | 面向人的命令：计划、执行、常驻循环、探险、采集、看板。 |
| 本地状态 | `state/` | 快照、事件、日志、心跳与急停开关；不入库。 |

## 自动化能力

- 只读规划：识别当前阶段，展开阶段目标的依赖链，按回本时间排序产出建筑
- 单步执行（确定性路径）：默认 dry-run；需 `--execute` + allow-list；队列感知、单步预算、结果验证
- AI 规划（可选）：简报喂模型，模型输出带前提的类型化步骤，runner 逐条执行；写工具不做
  本地预检，合法性由游戏裁决（ADR-030 / ADR-031）
- 常驻循环：单实例锁、指数退避、`state/STOP` 急停、日志/快照轮转、告警暂停
- 探险：只读航道报告、建船建议、单队预览/发送、多航道循环（独立舰船 allow-list）
- 每日签到：先读概览导航判断可领取，冷却期不消耗当天机会
- 观测：全量状态快照、diff 事件、趋势报表、只读 TUI 看板（概览/排行榜/消息/战役/宇宙记录）

## 快速开始

支持 Python 3.9–3.14，推荐 3.14（仓库的 `.python-version` 已固定为 3.14，
`uv venv` 会据此选解释器；3.15 及以上未验证、不在支持范围内）。凭据按
命令行 > 环境变量 > `.env` > `passwd.txt` 解析；本实例证书过期，脚本通常要加
`--no-verify-ssl`。

```bash
uv venv --python 3.14
uv pip install -e ".[dev]"
source .venv/bin/activate

# 0. 一份 .env 放全部配置（游戏凭证 + 模型端点）
cp .env.example .env
.venv/bin/python scripts/config.py --validate

# 1. 先看计划，不碰游戏
.venv/bin/python scripts/plan.py --no-verify-ssl
.venv/bin/python scripts/run_once.py --no-verify-ssl        # 影子模式

# 1.5 想一边用浏览器玩、一边跑脚本？导入浏览器的 2Moons Cookie
#     一个账号只有一份会话，脚本会复用它而不是重新登录（详见 docs/usage.md）
.venv/bin/python scripts/session.py --no-verify-ssl import '2Moons=...'

# 2. 小步授权执行（一轮）
.venv/bin/python scripts/run_forever.py --no-verify-ssl --once \
    --execute --allow 自律工厂

# 3. 无人值守
.venv/bin/python scripts/run_forever.py --no-verify-ssl \
    --execute --allow 自律工厂

# 4. AI 规划一轮（可选；先配置 NEWSTAR_AGENT_BASE_URL / NEWSTAR_AGENT_MODEL）
.venv/bin/python scripts/agent.py --no-verify-ssl            # 预览，不写游戏
.venv/bin/python scripts/agent.py --no-verify-ssl --execute  # 真实执行

# 5. 看板只读展示 state/，不连游戏
.venv/bin/python scripts/tui.py
```

每轮会检查队列、资源和单步预算，一次只做一个动作；`state/STOP` 存在即拒绝运行；被拒
不会自动重试。探险相关脚本默认只预览，真实发送需 `--execute` + 舰船 allow-list。

每个脚本干什么、常用参数是什么意思见 [scripts/README.md](scripts/README.md)；
完整命令与 API 用法见 [使用说明](docs/usage.md)；策略为什么这样定见
[架构与设计](docs/architecture.md) 和 [设计决策](docs/decisions.md)。

## SDK 组件

`newstar_sdk` 可以脱离本项目的策略层单独使用：它直接走游戏自身的 HTTP 页面和表单，把
登录、资源读取、建筑、研究、船坞、星图、舰队等基础操作封装为可复用的 Python 接口，供
自动化脚本、工具或后续服务调用。它提供但不限于：

- 登录、会话保存/恢复、会话过期后自动重登（需提供密码）
- 读取账号、资源、产能、星球、活动状态
- 读取消息（分类/分页/未读计数）、加成总表、生产控制明细、排行榜、战役名人堂与宇宙记录
- 每日签到（先读概览导航，可领取才访问 `page=bonus`；冷却期不消耗机会）
- 全量状态快照、快照 diff 事件、趋势统计
- 建筑升级、拆除、建造队列取消
- 科技研究、研究队列取消
- 船坞造舰/造防御、船坞队列取消
- 星图读取
- 舰队发送（运输、派遣、侦察、回收、殖民、远征等）与召回
- 队列等待
- 原始页面/HTTP 请求逃生通道
- 命令行工具 `newstar`

安装方式与上面相同。最小用法：

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
print(client.get_planets())

client.close()
```

`with NewStarClient(...) as client:` 也可用，但 `with` 不会自动登录，仍需显式调用
`login()`（或依赖各方法内部的 `ensure_login()`）。

SDK 只负责单步操作，不做策略：写操作是否成功以页面证据判断（队列出现目标项或等级变化），
失败抛 `ActionRejected`；同一账号不要并发写，SDK 只保证单实例内串行。

### 私有实例的注意事项

New-Star 私有实例常见的适配点是：

- 证书已过期：需要 `verify_ssl=False`，或者设置 `--no-verify-ssl` / `NEWSTAR_VERIFY_SSL=0`。
- 界面为中文，且有自定义元素名；SDK 通过元素 ID、CSS 结构和 `DatatList` 数据识别，不依赖界面文案。
- 部分模块可能被运营方禁用；访问时会得到游戏错误页并抛出 `GameError`。

不要把账号、密码或 `.session.json` 提交到版本库。

### 元素 ID

元素 ID 来自 New-Star / 2Moons 的通用编号，实际游戏可能增加或改名。SDK 会优先读取页面上的名称，静态表只用于“名称 → ID”的便捷解析。

| 类别 | 常用 ID |
| --- | --- |
| 建筑 | 1 合金冶炼厂、2 水晶催化厂、3 重氢精炼厂、4 幽能发电站、14 自律工厂、15 帝国主机、21 星际船坞、31 科研实验室 |
| 科技 | 106 间谍技术、108 计算机技术、109 武器技术、110 护盾技术、111 装甲技术、113 能源技术、115 工质推进器 |
| 舰船 | 202 轻型运输舰、203 重型运输舰、204 轻型战机、205 重型战机、206 巡洋舰、207 战列舰、208 殖民舰、209 回收舰、210 间谍探针、214 死星 |
| 防御 | 401 导弹发射器、402 轻型激光塔、403 重型激光塔、404 高斯炮、405 离子炮、406 等离子炮、407 小型护盾、408 大型护盾 |

完整映射见 [`newstar_sdk/constants.py`](newstar_sdk/constants.py)。方法中的元素参数同时接受 ID、中文名、英文名和常用缩写（如 `"metal"`、`"small_cargo"`）。

### 命令行

`newstar` 是 SDK 自带的命令行，面向临时查询和单步操作。以下命令假设已激活 `.venv`
（或把 `.venv/bin` 加入 `PATH`），全局参数放在子命令之前：

```bash
export NEWSTAR_BASE_URL=https://ol.lstyxl.com/
export NEWSTAR_USERNAME=your-user
export NEWSTAR_PASSWORD=your-password
export NEWSTAR_UNIVERSE=CQBX8573
export NEWSTAR_VERIFY_SSL=0

newstar overview
newstar buildings
newstar messages --category expedition
newstar bonus --claim
newstar queue build
newstar --json overview

# 写操作默认要求确认；非交互场景加 --yes
newstar build metal --levels 1 --yes
newstar send-fleet --target 3:505:9 --mission transport --ship small_cargo=1 --metal 100 --yes
```

查看全部命令：

```bash
newstar --help
newstar send-fleet --help
```

### 错误处理

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

## 当前范围

自动化链路目前覆盖：登录/会话、状态读取、建筑升级、研究、造船、探险、每日签到、单步
执行器、常驻守护与看板。策略只做单账号持续发展，不做多账号调度，也不做战斗决策。

SDK 不封装：战斗报告详情、市场/交易、联盟、生产比例写入、殖民/月球管理、聊天、军官/
商店等页面。这些页面仍可通过 `get_page()` / `request()` 访问。本实例另有部分模块被运营
方禁用（`market`、`fair` 返回「模块已禁用」，`information` 固定 503）。

## 示例

```bash
.venv/bin/python examples/read_state.py
.venv/bin/python examples/manage_queue.py --yes   # 入队后立即取消
.venv/bin/python examples/send_fleet.py --help
```

## 测试

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
  daily.py        # 每日签到（先查导航，可领取才访问 page=bonus）
  expedition.py   # 探险航道 / 积分 / 建船建议（只读）
  expedition_executor.py  # 单队探险预览 / 发送
  ship_build.py   # 推荐舰船建造（默认 dry-run）
  expedition_cycle.py  # 有上限的多航道发送循环
  executor.py     # 队列感知 + 安全闸
  brief.py        # 事实 → 决策简报（AI 路径的输入）
  tools.py        # 动词工具（建造/研究/造船/发船…）
  runner.py       # 按 when/until 执行类型化步骤
  agent.py        # 模型决策循环（失败回退 planner）
  prompt.py       # 提示词语料
  llm.py          # Provider 协议 + OpenAI 兼容实现
  memory.py       # 从 journal 读回最近 N 轮，补 history/delta
  runtime.py      # 常驻循环的 pid 锁、心跳、陈旧锁接管
newstar_sdk/      # 客户端组件（稳定契约）
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
scripts/          # 普查、采集、报表、计划、执行、看板
examples/         # 可运行示例
AGENTS.md         # 协作与代理规则
```

## 文档

- [使用说明](docs/usage.md)
- [架构与设计](docs/architecture.md)
- [设计决策（ADR）](docs/decisions.md)
- [路线图与验收](docs/roadmap.md)
- [页面普查](docs/p0-page-census.md)
- [协作与代理规则](AGENTS.md)

## 许可

MIT。New-Star 上游同样使用 MIT 许可；归属说明见 [NOTICE](NOTICE)。
