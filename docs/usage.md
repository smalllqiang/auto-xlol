# 使用说明

本文面向要基于 `newstar-sdk` 编写自动化程序的开发者。快速安装与第一个例子见 [README](../README.md)；内部实现和数据来源见 [架构与设计](architecture.md)。

## 1. 客户端与配置

`NewStarClient` 是一个同步客户端，内部持有一个 `requests.Session`。同一个客户端只应服务一个游戏账号；不要并发地对同一账号执行写操作。

核心配置项：

- `base_url`：游戏根地址，例如 `https://ol.lstyxl.com/`。
- `username` / `password`：登录凭证。只在需要登录或自动重登时使用。
- `universe`：宇宙 ID 或登录页上显示的宇宙名。省略时使用登录页第一个宇宙。
- `verify_ssl`：默认 `True`；实例证书过期时传 `False`。
- `planet_id`：初始星球 ID；之后也可以用 `select_planet()` 切换。
- `request_interval`：两次请求之间的最小间隔，默认 0.25 秒。
- `max_retries` / `retry_backoff`：网络错误和 5xx 的重试策略。
- `auto_relogin`：会话失效时是否用已有账号密码自动重登。

### 宇宙解析

登录页的宇宙下拉框通常是“值=数字 ID，文本=宇宙名”。SDK 会读取它：

- `universe=None`：使用第一个宇宙。
- `universe=1` 或 `"1"`：使用 ID 为 1 的宇宙。
- `universe="CQBX8573"`：按显示名匹配。
- 无法匹配时抛出 `ConfigurationError`，错误信息会列出可选宇宙。

### 会话生命周期

```python
client = NewStarClient(base_url, username, password, universe="CQBX8573", verify_ssl=False)
client.login()          # 显式登录
client.ensure_login()   # 已登录则直接返回，否则尝试使用现有 Cookie / 登录
```

- 大多数高层方法内部会调用 `ensure_login()`，所以第一次调用 `get_overview()` 也会自动登录。
- `login()` 会先访问登录页解析宇宙，然后 `POST index.php?page=login`。
- 登录成功后游戏通过 `2Moons` Cookie 维持会话；SDK 不硬编码 Cookie 名。
- 会话失效时，如果客户端有账号密码且 `auto_relogin=True`，会重登并重试当前请求。**舰队三步发送过程中的 PHP session token 不能跨会话恢复**，因此长时间任务应确保会话有效，或重新从头发送。
- `logout()` 会请求退出并清空 Cookie。
- `set_credentials(username, password)` 可在运行中替换凭证；它会清空旧 Cookie 并令下一次 `login()`/`ensure_login()` 重新登录。
- `save_session()` / `load_session()` 只保存 Cookie、用户名、宇宙和 base URL，不保存密码；文件权限在支持的平台上为 `0600`。`load_session()` 会验证会话，返回 `False` 表示已失效。

### TLS

默认验证证书。遇到证书过期/自签名实例：

```python
client = NewStarClient(..., verify_ssl=False)
```

这会触发 `urllib3` 的 `InsecureRequestWarning`。CLI 在 `--no-verify-ssl` 时会抑制该警告；库使用者可自行处理：

```python
import warnings
from urllib3.exceptions import InsecureRequestWarning

warnings.simplefilter("ignore", InsecureRequestWarning)
```

## 2. 读取状态

```python
overview = client.get_overview()
```

`Overview` 主要字段：

- `username` / `universe` / `game_name` / `engine_version` / `language`
- `server_time`：服务器页面生成的本地时间（无时区），来自页面内嵌的 `serverTime`
- `resources`：`Resources`，含 `amounts`、`capacities`、`production` 三个以资源 ID 为键的字典
- `planet`：当前星球，含坐标、直径、面积、温度、积分/排名
- `planets`：星球选择器中的所有星球
- `status`：研究/建筑/船坞活动卡片文本；`research_idle` 等属性用于判断是否空闲

资源 ID 与便捷属性：

| ID | 含义 | 属性 |
| --- | --- | --- |
| 901 | 星际合金 | `resources.metal` |
| 902 | 高能水晶 | `resources.crystal` |
| 903 | 重氢 | `resources.deuterium` |
| 911 | 幽能 | `resources.energy` |
| 921 | 暗物质 | `resources.dark_matter` |

主资源的精确值来自页面里的 `resourceTicker`；容量来自其中的 `limit`。其他资源从顶部资源栏的 `data-real` 读取。

### 切换星球

```python
planets = client.get_planets()
current = next(p for p in planets if p.is_current)
client.select_planet(current.id)

# 切换后，后续 game.php 请求会自动带 cp=<planet_id>
```

切换会验证服务器是否真的选中了目标星球；无效 ID 会抛出 `GameError` 并恢复之前的星球。

### 消息

消息列表由 AJAX 渲染：`page=messages` 里只有各分类的未读/总数，正文来自
`page=messages&mode=view&messcat=..&site=..&ajax=1`。SDK 已封装：

```python
counters = client.get_message_counters()   # {分类 id: MessageCounter}
counters[15].unread, counters[15].total    # 探险报告

page = client.get_messages("expedition")   # 分类可用 id / 中文名 / 别名
for message in page:
    print(message.id, message.time, message.sender, message.unread)
    print(message.body)
    print(message.coordinates)             # 正文里的 [G:S:P]

if page.has_next_page:
    page2 = client.get_messages("all", page=2)
```

分类 id：0 间谍、1 玩家、2 盟友、3 战斗、4 系统、5 运输、15 探险、50 游戏、
99 建造、100 全部、200 归档、999 发件箱。常用别名（`"spy"`、`"cr"`、`"exp"`、
`"outbox"` 等）见 `constants.py` 的 `MESSAGE_CATEGORY_ALIASES`。

`get_message_counters()` 只发一次请求，适合做"有没有新消息"的事件触发，不必
每次都拉列表。

### 加成（乘数）

`page=infobonus` 给出全部加成项。标签就是页面上的文案（本实例是中文），SDK 不做
翻译：

```python
bonuses = client.get_bonuses()
bonuses.percent("全资源生产加成")     # 3.0 表示 3%
bonuses.multiplier("合金采集加成")    # 1.03
bonuses.as_dict()                     # {名称: 倍率}
```

找不到时 `percent()` 返回 `0.0`、`multiplier()` 返回 `1.0`。

## 3. 写操作与验证

建筑、研究、船坞方法的共同约定：

1. 先读取当前页面，解析元素、等级/数量、成本、前置条件、`levelToBuild`。
2. 本地检查前置条件与资源缺口；明显不满足时直接抛 `ActionRejected`，不发送请求。
3. 发送与游戏表单一致的 POST。
4. 解析响应页面，确认队列里出现对应条目，或等级/可用数量已经增加。
5. 如果服务器返回成功页面但操作没有生效，抛出 `ActionRejected`。

这意味着：**方法返回并不只是“HTTP 200”，而是“SDK 能在页面上确认操作生效”**。

写方法都提供 `verify=True` 参数。除非你在调试页面变化，否则不建议关闭；关闭后只要 HTTP 请求成功就会返回，可能把游戏的静默拒绝当成成功。

### 建筑

```python
queue = client.upgrade_building("metal", levels=1)
queue = client.upgrade_building(1, target_level=10)
queue = client.destroy_building("metal")
queue = client.cancel_building_queue(0)
```

- `levels` 加到“当前已排队等级”上，而不是已完成等级上。
- `target_level` 是绝对目标等级，优先级高于 `levels`。
- 多级升级会尽量按等级逐条入队；如果资源只够前几级，游戏会少排，SDK 因未达到目标等级而抛 `ActionRejected`，此时可能已经排入部分等级。需要精确控制时请一次升一级。
- 队列位置从 `0` 开始。`cancel_building_queue(0)` 使用游戏的 `cmd=cancel`；其他位置使用 `cmd=remove&listid=position+1`。
- 拆除是不可逆操作，SDK 不额外确认；请在你自己的程序中确认。

### 研究

```python
queue = client.research("computer", levels=1)
queue = client.research(108, target_level=5)
queue = client.cancel_research_queue(0)
```

研究队列是账号级的；实验室、研究网络等前置条件由游戏页面给出。方法和建筑一样做本地检查与结果验证。

### 船坞

```python
queue = client.build_ships({"small_cargo": 2})
queue = client.build_ships({"light_laser": 5}, mode="defense")
queue = client.cancel_ship_queue(0, mode="fleet")
```

- `mode` 只接受 `"fleet"` 或 `"defense"`。
- `build_ships` 会检查 `max_buildable`；请求数量超过当前资源允许值时直接抛 `ValueError`，避免游戏静默截断。
- 船坞队列位置同样是 `0` 开始；取消时发送 `action=delete&auftr[]=position`。
- 舰船和防御的 ID 在同一个 `ShipInfo` 模型中，用 `category`/`is_defense` 区分。

### 等待队列

```python
client.wait_for_building_queue(timeout=600, poll_interval=2)
client.wait_for_research_queue(timeout=3600)
client.wait_for_ship_queue(timeout=600, mode="fleet")
```

超时抛内置 `TimeoutError`。等待期间每个轮询周期都会重新请求页面；请按游戏规模和请求频率调整 `poll_interval`。

## 4. 舰队发送

`send_fleet()` 复现游戏网页的三步流程：

1. `POST game.php?page=fleetStep1`：目标坐标、任务、舰船数量；服务器在 PHP session 中创建 token。
2. `POST game.php?page=fleetStep2`：token、目标、速度、舰队组；服务器校验目标并返回可用任务。
3. `POST game.php?page=fleetStep3`：token、任务、要运输的资源、驻留时间；服务器创建舰队并返回确认页。

示例：

```python
result = client.send_fleet(
    target="3:505:9",                  # 或 Coordinates(3, 505, 9, type=1)
    ships={"small_cargo": 10},
    mission="transport",               # 3
    speed=10,
    resources={"metal": 1000, "crystal": 500, "deuterium": 0},
    stay_time=0,
    target_type=1,                     # 1 星球 / 2 残骸 / 3 月球
)

print(result.mission_name)
print(result.from_coordinates, "->", result.target_coordinates)
print(result.distance, result.consumption, result.arrival_time)
print(result.ships)
```

要点：

- `ships` 的键可以是 ID、中文名、英文名或常用缩写；只接受舰船，不接受防御。
- 任务可以是 ID 或名称。常用：`1 攻击`、`3 运输`、`4 派遣`、`6 侦察`、`7 殖民`、`8 回收`、`15 远征`。
- 运输任务（3）必须至少携带一种资源，否则 SDK 在本地直接拒绝。
- 目标坐标与当前星球相同（且类型相同）会被游戏拒绝。
- 燃料从出发星球的重氢扣除；目标距离、速度、舰船组合都会影响消耗。SDK 不在本地计算燃料，交给游戏校验。
- 派遣/运输等任务的目标类型通常是 1；回收残骸用 2；月球用 3。
- `stay_time` 用于驻留/远征类任务，取值来自第二步页面的下拉选项；默认 `0`，可用时请按页面选项传值。
- `fleet_group` 用于联合攻击/ACS；普通单人任务保持 0。
- `max_wave` 和 `sectors` 会原样提交，默认 `1` 和 `3`；只在对应任务下生效。
- 如果第二步返回的可用任务不包含请求的任务，SDK 会抛 `ActionRejected` 并列出可用任务。
- 第三步没有到达确认页时，SDK 认为发送失败并抛 `ActionRejected`（可能原因：燃料不足、舰队槽满、目标无效、任务规则不满足）。

召回：

```python
for fleet in client.get_fleets():
    if not fleet.returning and fleet.can_recall:
        client.recall_fleet(fleet.id)
```

`get_fleets()` 能解析在途和返航中的舰队。返航舰队没有召回表单，`can_recall=False`、`id=None`；在途舰队有 `id`，可直接传给 `recall_fleet()`。

## 5. 错误处理、重试与限速

异常层次：

```
NewStarError
├── ConfigurationError
├── AuthenticationError
├── SessionExpired
├── TransportError
├── GameError
│   └── ActionRejected
└── ParseError
```

- GET 请求的网络超时、连接失败、5xx 会按 `max_retries` 重试；POST 默认不自动重试，避免重复入队或使舰队 token 失效；HTTP 4xx 直接抛 `TransportError`。
- 游戏错误页统一解析为 `GameError`；错误文本可能是未翻译的键（例如 `NFCoosen`），SDK 原样保留。
- HTTP 成功但操作未生效抛 `ActionRejected`。
- 页面结构无法识别抛 `ParseError` 或返回空列表/`None`（取决于具体方法），不会伪造数据。
- 默认请求间隔 0.25 秒。提高并发或缩短间隔前，请先确认游戏规则和服务器承受能力。

## 6. 实例差异与排错

SDK 以“页面结构 + 元素 ID + `DatatList`”为主要依据，不依赖中文/英文/俄文文案。但私有实例仍可能修改模板。遇到问题时按下面顺序排查：

1. **登录失败**：确认 `universe` 是登录页上的值或显示名；确认密码；确认实例需要 `verify_ssl=False`。
2. **一访问就跳回登录页**：会话过期或 Cookie 未保存。调用 `login()` 或检查 `save_session()` / `load_session()`。
3. **元素列表为空**：实例模板可能改了 `div.build_box` 等结构。用 `client.get_page("buildings")` 保存 HTML，对照 `newstar_sdk/parsers.py` 的 selector。
4. **成本/等级是 0 或不对**：检查页面是否仍有 `DatatList = {...}`；没有时会退回 HTML 解析，精度可能下降。
5. **`ActionRejected` 但游戏网页能操作**：通常是 POST 字段或页面确认结构变了。保存请求前后的 HTML，检查 `client.py` 中对应的 data 字段。
6. **模块页面报错**：该模块可能被运营方禁用；`get_page()` 也会抛 `GameError`，这是预期行为。
7. **舰队发送失败**：先看 `ActionRejected` 的文本；再检查燃料、舰队槽、目标类型、任务限制和 `stay_time`。

不要在公开 issue 中粘贴账号、Cookie、session 文件或未脱敏的战报。

## 7. 原始访问

当 SDK 还没有封装某个页面时：

```python
html = client.get_page("messages")
html = client.get_page("galaxy", galaxy=3, system=505)

response = client.request("GET", "game.php?page=overview")
response = client.request(
    "POST",
    "game.php?page=somePage",
    data={"foo": "bar"},
    check_error=True,   # 解析游戏错误页并抛 GameError
)
print(response.status_code, response.url, response.text)
```

`request()` 会自动带上当前星球的 `cp` 参数，并复用登录会话；`check_error=False` 时原样返回游戏错误页，便于自己解析。

## 8. 扩展新页面

新增一个页面支持通常只需要：

1. 在 `newstar_sdk/parsers.py` 写一个纯函数，输入 HTML，输出 `models.py` 中的 dataclass 或列表。
2. 在 `newstar_sdk/client.py` 增加一个高层方法，负责取页面、必要时 POST、调用解析器、做结果验证。
3. 把真实页面保存为 `tests/fixtures/*.html`，补一个离线测试；不要只依赖实时实例。
4. 如果涉及新元素类别，更新 `constants.py` 的 ID/名称表和 `resolve_element` 的类别映射。

保持“解析”和“HTTP/业务动作”分离，能显著降低实例改版时的维护成本。

## 9. 状态快照

长驻程序通常不想发十几次零散请求，而想一次拿到"决策所需的全部状态"：

```python
from newstar_sdk.snapshot import build_snapshot

snapshot = build_snapshot(client)      # dict，可直接 json.dumps
snapshot["resources"]["amounts"]["901"]
snapshot["research"]["expedition_slots"]
snapshot["messages"]["unread"]
```

`build_snapshot()` 只读，包含 `account` / `planet` / `planets` / `resources` /
`energy` / `activity` / `research` / `bonuses` / `queues` / `fleets` / `messages`
各节。某一节失败时错误写进 `errors`，其余章节照常返回；可以用 `include_*`
参数跳过不需要的页面。

命令行采集器把每次快照追加成一行 JSON：

```bash
.venv/bin/python scripts/collect_state.py --no-verify-ssl --out state/snapshots.jsonl
```

适合放进 cron / systemd timer（例如每 5 分钟一次），之后用 jq / duckdb /
pandas 读。`--print` 直接输出快照，`--no-queues` 等可以按需减负。

采集器默认会把本次快照和上一次做 diff，把变化追加到同目录的
`events.jsonl`（可用 `--events-out` 改，`--no-events` 关闭）。

生产控制页也有结构化读取：

```python
production = client.get_production()
production.get("metal").total          # 当前总产能/小时
production.get("metal").daily          # 日产量
production.levels                      # {建筑 id: 等级}
production.ratio("metal")              # 当前生产比例（100 表示 100%）
```


## 10. 事件

`newstar_sdk.events.diff_snapshots(previous, current)` 把两次快照的差异变成事件，
是"观测"到"反应"的桥梁。纯函数，不读文件、不发请求。

```python
from newstar_sdk.events import diff_snapshots

for event in diff_snapshots(previous_snapshot, current_snapshot):
    print(event.kind, event.severity, event.data)
```

目前识别：

| kind | 触发条件 | severity |
| --- | --- | --- |
| `message_new` | 某分类未读数增加 | 间谍/战斗为 `notice`，其余 `info` |
| `queue_empty` | 某队列从有到空（已含"最后一项完成"） | `notice` |
| `queue_item_finished` | 队列里某项消失（非整体清空） | `info` |
| `queue_item_added` | 队列里出现新项 | `info` |
| `energy_deficit` / `energy_recovered` | 可用幽能跨过 0 | `warning` / `info` |
| `storage_full` | 某资源占用率跨过阈值（默认 95%） | `notice` |
| `planet_added` / `planet_lost` | 星球列表变化 | `notice` / `warning` |
| `planet_switched` | 当前星球切换 | `info` |
| `research_completed` | 科技等级上升 | `info` |
| `snapshot_gap` | 两次快照间隔超过阈值（默认 1 小时） | `warning` |

事件是普通 dataclass，`as_dict()` 返回扁平字典，方便写 JSONL 或进数据库。

## 11. 趋势报表

`scripts/state_report.py` 读 `snapshots.jsonl`（和同名目录下的
`events.jsonl`），按时间窗给出趋势。窗口以**最新一条快照**为终点，所以对历史
文件同样有效。

```bash
.venv/bin/python scripts/state_report.py --hours 24
.venv/bin/python scripts/state_report.py --file state/snapshots.jsonl --hours 6 --json
```

输出示例：

```
window  2026-11-13T08:00 -> 2026-11-13T09:00  (9 snapshots, median interval 300s)
  metal     100,000,017,609 -> 100,000,020,009  delta +2,400  (+2,400/h)
  crystal   ...
  prod/h   metal 3,600  crystal 2,400  deuterium 0
  queues   building 33% (max 1)  research 0% (max 0)  shipyard 0% (max 0)
  unread   2 (peak 2)
  research 115: 0->1
  errors   0 snapshot(s) with section errors
  gaps     1 (25m)
  events   3 (queue_empty×1, research_completed×1, message_new×1); severity notice:1, info:2
```

统计逻辑在 `newstar_sdk/report.py` 的 `summarize_snapshots()`，是纯函数，方便在
别的程序里复用。断档阈值默认取 `max(3 × 最短采集间隔, 15 分钟)`，也可以用
`--gap-seconds` 指定。

## 12. 规划器（autoplay）

`newstar_sdk` 只回答"现在是什么"；同仓库的 `autoplay` 包回答"下一步做什么"。
两者分开，是为了让 SDK 保持稳定，而策略可以频繁改。

```python
from autoplay import build_plan

plan = build_plan(client)          # 只读：若干次 GET，不执行任何操作
print(plan.phase, plan.goal_status)
for action in plan.actions:
    print(action.category, action.name, action.current_level, "->", action.target_level)
```

`plan.actions` 分两类：

- **blocker**：当前阶段目标的依赖链。递归解析页面上的前置条件（如
  "自律工厂 等级 5 (0/5)"），直到找到现在就能做的项；按依赖深度、再按投入排序。
  每个 blocker 带 `steps`（还差几级）和 `total_cost_equivalent`（用页面给出的
  `factor` 推算整条升级链的总投入）；`affordable` 只表示下一级买得起，
  `affordable_total` 才是整条链买得起。
- **roi**：产出建筑按回本时间排序。回本 = 等价投入 ÷ 每级预计增产量。

每级增产量用 `observed * (1.1 * (L+1) / L - 1)` 自校准：只依赖页面显示的实际
产量，不写死 universe speed 或加成，因此换实例也不会整体算错。

```bash
.venv/bin/python scripts/plan.py --no-verify-ssl
.venv/bin/python scripts/plan.py --json
```

v1 的限制，用之前需要知道：

- 只给"下一步"，不生成多级序列；
- 回本时间是估算，暂不含电量约束、仓容和建造时间；
- 只实现种田/探险两个阶段；其他阶段只输出产能候选；
- 不执行任何操作。

## 13. 执行器与安全闸

`autoplay.execute_plan()` 把计划落成**最多一个**动作，默认只做 dry run：

```python
from autoplay import build_plan, execute_plan

plan = build_plan(client)
result = execute_plan(
    client,
    plan,
    dry_run=False,
    allowed_elements=[14],       # 执行时必须显式允许
    max_cost_equivalent=5000,    # 可选：单步预算（等价）
)
print(result.status, result.reason)
```

安全设计：

- 默认 dry run，只有显式传 `dry_run=False` 才会改游戏；
- 一次调用只执行一个动作，便于逐步核对；
- 执行模式必须提供 allow-list，否则直接跳过；
- 建造队列非空时不排建筑，研究队列非空时不排研究，会自动落到下一条可用动作；
- 资源不足、超过单步预算的动作直接跳过；
- 游戏拒绝（`ActionRejected`）只记录错误，不自动重试。

命令行：

```bash
# 影子模式：只打印计划
.venv/bin/python scripts/run_once.py --no-verify-ssl

# 真正执行；--allow 可重复，接受 id 或中文名
.venv/bin/python scripts/run_once.py --no-verify-ssl --execute --allow 自律工厂

# 紧急停止：文件存在即拒绝执行
touch state/STOP
```

每次运行追加一行到 `state/journal.jsonl`，包含阶段、结果、队列和完整动作快照。
