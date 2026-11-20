# scripts/ 命令入口

这一层是**面向人的薄封装**：解析参数 → 调 `autoplay/` 或 `newstar_sdk/` → 打印结果。
它不放核心逻辑——改策略去 `autoplay/`，改客户端或解析去 `newstar_sdk/`。

本文是索引：说明每个脚本干什么、日常要用的参数是什么意思、哪些参数会真的写游戏。
**参数的完整清单以各脚本的 `--help` 为准**，用法细节见 [使用说明 §12 起](../docs/usage.md)，
本实例的坑见 [AGENTS.md](../AGENTS.md)。

所有脚本都从**仓库根目录**运行；默认路径（`state/`、`live_pages/`）都相对仓库根。

```bash
.venv/bin/python scripts/run_forever.py --help
```

## 1. 共用参数

连游戏的脚本共用下面这组参数（个别脚本少一两个，以 `--help` 为准）：

| 参数 | 含义 |
| --- | --- |
| `--config PATH` | 指定 `.env` 或 `passwd.txt`；默认自动探测。优先级：**命令行 > 环境变量 > `.env` > `passwd.txt`** |
| `--base-url` / `--username` / `--password` / `--universe` | 临时覆盖对应配置项，不用改文件 |
| `--no-verify-ssl` | 跳过 TLS 校验。本实例证书过期，**通常必须加** |
| `--interval SEC` | 两次**请求**之间的最小间隔（秒），默认 0.3–0.5 |
| `--session-file PATH` | 共享会话文件，默认 `state/session.json`（存在就复用；不存在时按策略登录并写入） |
| `--relogin` | 兼容保留：现在默认就允许会话失效时用账号密码重建（等于默认行为） |
| `--no-relogin` | 浏览器优先：会话失效时不自动登录，打印提示并以退出码 4 结束 |
| `--no-save-session` | 密码登录后不写共享会话文件（仅本次进程有效）。临时账号/一次性运行用 |
| `--json` | 输出机器可读 JSON，便于管道和脚本消费 |

几个容易误解的点：

- `--interval` 在 `run_forever.py` 里是**两轮之间**的间隔（默认 300 秒），不是请求间隔；
  要跑一轮就用 `--once`。
- 默认是**脚本优先**：文件有效就复用；失效/不存在就自动用账号密码重建，并**把结果
  写回** `state/session.json`。一个账号只有一份会话，所以这会踢掉浏览器；想保浏览器
  就加 `--no-relogin`（或 `NEWSTAR_RELOGIN=0`）。
- `--relogin` 只是兼容保留，不再需要。`--no-relogin` 下会话失效会以退出码 4 结束。
- 启动时 stderr 会打印一行「会话：复用 …」或「会话：账号密码登录 …」；
  `--no-save-session` 可关闭回写。

## 2. 安全闸：什么时候才会真的写游戏

执行类脚本**默认全部 dry-run**，只报告「我会做什么」。真实写入要显式叠两个开关：

- `--execute`：真的动手；
- `--allow ID|NAME`：允许操作的对象白名单（可重复）。可以是元素 id 或中文名；
  探险类脚本是**舰船 id**，威胁召回是**舰队 id**，生产比例是**元素 id**。
- `--max-cost`（可选）：单个动作的金属等价预算上限。

任何执行路径都受 `state/STOP` 约束：文件存在就在下一步/下一轮拒绝动手；被游戏拒绝的
动作**不会自动重试**。每次结果写一条 `state/journal.jsonl`。

退出码方便脚本判断：`0` 正常（`run_forever` 遇到 `state/STOP` 也按正常退出）；`2` 参数
被安全闸拒绝（如 `--execute` 缺 `--allow`、`--balance-power` 缺 `--balance-allow`）；
`3` 拒绝启动（`run_once` 遇到急停文件，或 `agent` 发现已有写者在跑）；
`4` 会话失效且不允许自动重登（`--no-relogin` / `NEWSTAR_RELOGIN=0`）。

| 脚本 | 默认行为 | 真实写入需要 |
| --- | --- | --- |
| `plan.py` | 只读计划 | 无（不写） |
| `run_once.py` | 预览一个动作 | `--execute --allow` |
| `run_forever.py` | 循环跑，但不动手 | `--execute --allow` |
| `agent.py` | 一轮 AI 规划，写工具只登记 | `--execute`（**无 allow-list**，ADR-030） |
| `dispatch_expedition.py` | 预览舰队第一、二步 | `--execute --allow <舰船 id>` |
| `build_expedition_ships.py` | 预览造船计划 | `--execute --allow <舰船 id>` |
| `expedition_cycle.py` | 预览第一队 | `--execute --allow <舰船 id>` |
| `expedition_farm.py` | 预览当前航道与下一队 | `--execute --allow <舰船 id>` |
| `threat.py` | 只读威胁报告 | `--recall --execute --allow <舰队 id>` |
| `production.py` | 只读比例 | `--set N=比例 --execute --allow <元素 id>` |
| `daily_bonus.py` | 只读签到状态 | `--claim`（访问即领取，没有白名单） |
| `collect_state.py` / `page_census.py` / `empire.py` / `expedition.py` / `state_report.py` / `tui.py` | 只读 | 无（不写游戏） |
| `session.py login` | （登录子命令） | 显式执行即登录并保存会话（会踢浏览器） |
| `notify.py` | 发一条飞书通知 | 无（不写游戏；需 `NEWSTAR_FEISHU_WEBHOOK`） |
| `live_smoke.py` | 只读冒烟 | `--mutate`（排队后立刻取消） |

## 3. 按任务选脚本

| 我想… | 命令 | `docs/usage.md` |
| --- | --- | --- |
| 检查 `.env` 有没有填对 | `scripts/config.py --validate` | §1 |
| 和浏览器共用一份会话 | `scripts/session.py import '2Moons=...'` | §1 多端共用会话 |
| 抓某个页面的原始 HTML | `scripts/page_census.py --only messages` | AGENTS.md 扩展新页面 |
| 采一次快照、看变化 | `scripts/collect_state.py` | §9–10 |
| 看一段时间趋势 | `scripts/state_report.py --hours 24` | §11 |
| 只看下一步该建什么 | `scripts/plan.py` | §12 |
| 改战略目标（阶段/约束） | `scripts/directive.py --write-default` | §12 directive |
| 影子跑一轮（不动手） | `scripts/run_once.py` | §13 |
| 无人值守挂机 | `scripts/run_forever.py --execute --allow ...` | §14 |
| 看板 | `scripts/tui.py`（`--once` 打印一帧） | §15 |
| 探险够不够格、建什么船 | `scripts/expedition.py` | §16 |
| 发一队探险 | `scripts/dispatch_expedition.py` | §17 |
| 造推荐的探险船 | `scripts/build_expedition_ships.py` | §18 |
| 多航道循环探险 | `scripts/expedition_cycle.py` | §19 |
| 常驻探险、自动填满所有航道 | `scripts/expedition_farm.py --execute --allow 207` | §19.1 |
| 被侦察/被打了 | `scripts/threat.py` | §20 |
| 一屏看所有星球 | `scripts/empire.py` | §21 |
| 调生产比例、缺电 | `scripts/production.py` | §22 |
| 让模型规划一轮 | `scripts/agent.py` | §23 |
| 每日签到 | `scripts/daily_bonus.py --claim` | §2 每日签到 |
| 测试飞书消息渠道 | `scripts/notify.py --test` | §1 消息上报 |
| SDK 冒烟 / 演示 | `scripts/live_smoke.py` | — |

`local_config.py` 不是入口，是上面这些脚本共用的配置/会话/建客户端辅助模块；
`utils/` 是更下层的基础设施（`.env` 解析、飞书消息上报），不依赖游戏层。

### 消息上报（飞书）

`notify.py` 不连游戏，只把一条消息发到 `.env` 配好的飞书群：

```bash
.venv/bin/python scripts/notify.py --test
echo "cycle failed" | .venv/bin/python scripts/notify.py --title 告警
.venv/bin/python scripts/notify.py --test --json
```

配置 `NEWSTAR_FEISHU_WEBHOOK`（可选 `NEWSTAR_FEISHU_SECRET`）见
[`docs/usage.md` §1「消息上报」](../docs/usage.md)。退出码：`0` 成功，`1` 发送失败，
`2` 未配置或缺少正文。Python 侧调用 `utils.notify.notify()`，未配置时返回
`skipped=True` 而不是抛异常。

## 4. 常用参数按主题

### 执行范围

| 参数 | 含义 |
| --- | --- |
| `--execute` | 真的写游戏；不加只预览 |
| `--allow ID` | 可重复的白名单，写了才允许碰这个对象 |
| `--max-cost N` | 单个动作的金属等价上限 |
| `--once` | 只跑一轮就退出（`run_forever` / `tui`） |
| `--max-cycles N` | 跑 N 轮后退出，0 = 不停 |
| `--max-failures N` | 连续失败 N 次后退出（默认 10） |

### 常驻循环的节奏（`run_forever.py`）

每轮都执行计划；下面这些 `--*-every N` 是**每 N 轮**顺手做的采集，`0` 表示关掉：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--interval` | 300 | 两轮之间的秒数 |
| `--snapshot-every` | 12 | 写全量快照并 diff 事件 |
| `--statistics-every` | 120 | 排行榜 → `state/statistics.json` |
| `--messages-every` / `--messages-pages` | 60 / 10 | 收件箱 → `state/messages.json`，取最新几页 |
| `--battles-every` | 120 | 战役名人堂 → `state/battles.json` |
| `--records-every` | 120 | 宇宙记录 → `state/records.json` |
| `--empire-every` | 120 | 全局面板 → `state/empire.json` |
| `--threat-every` | 0 | 威胁评估，默认关闭 |
| `--rotate-mb` | 10 | journal/快照/事件超过该大小就轮转，0 关闭 |
| `--pause-on-alert` / `--pause-on-threat` | 关 | 出现告警/危险威胁时**跳过本轮执行** |
| `--no-bonus` | 关 | 关掉每日签到检查；默认会检查，`--execute` 模式下顺带领取 |
| `--balance-power` / `--balance-allow` | 关 | 净电率为负时下调最大耗电户比例，需配合 allow 列表 |

探险集成在循环里，默认关：`--expedition` 打开（还必须同时有 `--execute` 与
`--expedition-allow`，否则只打印一条警告并关闭探险调度）、`--expedition-allow ID`（舰船
白名单）、`--expedition-every N`（默认 12）、`--expedition-max-dispatch N`（默认 1）、
`--expedition-auto-build` + `--expedition-build-count` / `--expedition-build-timeout` 负责空闲航道补船。

如果只想常驻探险、不想让 `run_forever` 反复重读整份报告，用独立的
`scripts/expedition_farm.py`：它按返航时间 +5s 补下一波，默认每 10 小时重查一次最大航道数。
两者共用 `state/daemon.lock`，**不要同时运行**。加 `--notify` 可把每轮状态/错误发到飞书
（默认关闭；还需 `.env` 的 `NEWSTAR_FEISHU_WEBHOOK`，没配则只提示不上报），消息以
`[探险]` 开头。

### AI 规划（`agent.py`）

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `--execute` | 关 | 让 planner 的步骤真的落到账号上 |
| `--every SEC` | 0 | 按该间隔持续跑；0 = 只跑一轮就退出 |
| `--max-rounds` / `--max-steps` | 6 / 8 | 模型往返次数 / 每轮允许的步骤数 |
| `--max-chars` | 6000 | 喂给模型的简报字符预算 |
| `--history` | 8 | 简报里带上最近几条 agent 记录，作为跨轮记忆 |
| `--shipyard-slots` | 5 | 船坞队列容量（页面不显示），用于计算剩余槽位 |
| `--agent-base-url` / `--agent-model` / `--agent-api-key` | 来自 `.env` | 模型端点覆盖 |
| `--agent-temperature` / `--agent-timeout` | 配置默认 | 采样温度 / 单次模型请求超时 |
| `--lock-file` / `--daemon-lock` | `state/agent.lock` / `state/daemon.lock` | 自己的写者锁 / 检查确定性守护的锁；与 `run_forever.py` 不能同时跑 |

### 本地文件与凭证（不连游戏，或只写本地）

| 参数 | 属于 | 含义 |
| --- | --- | --- |
| `--reveal` / `--validate` / `--json` | `config.py` | 显示明文密钥 / 缺必填项时退出码非 0 / JSON 输出 |
| `--path` / `--write-default` / `--validate FILE` | `directive.py` | 策略文件位置 / 写入内置默认策略（**覆盖**）/ 只校验某个文件 |
| `import <cookie>` / `show` / `status` / `login` | `session.py` | 从浏览器导入 `2Moons`（可带前缀或整行 Cookie 头）/ 打印 Cookie / 检查是否仍有效 / 用账号密码登录 |

### 观测与采集

| 参数 | 属于 | 含义 |
| --- | --- | --- |
| `--out PATH` | `collect_state` | 追加到哪个 JSONL，默认 `state/snapshots.jsonl` |
| `--print` | `collect_state` | 直接打印快照而不是摘要行 |
| `--no-events` / `--events-out` | `collect_state` | 不和上一条快照 diff / 事件文件位置 |
| `--no-research` `--no-queues` `--no-fleets` `--no-messages` `--no-bonuses` | `collect_state` | 跳过对应页面，请求更便宜 |
| `--out DIR` | `page_census` | 输出目录，默认 `live_pages/census-<时间戳>` |
| `--only a,b` / `--all` / `--discover` | `page_census` | 指定页面 / 全部已知只读页 / 顺带跟随概览里的导航链接 |
| `--save-session PATH` | `page_census`、`live_smoke` | 顺手把会话 Cookie 存到文件（含凭证，注意别提交） |
| `--hours H` / `--gap-seconds` | `state_report` | 统计窗口（默认 24 小时，**以最新快照为终点**）/ 覆盖采集断档阈值 |
| `--file` / `--events` | `state_report` | 快照与事件文件，默认 `state/snapshots.jsonl` 及同级 `events.jsonl` |

### 看板（`tui.py`）

`--state-dir`（默认 `state`）、`--refresh`（3 秒）、`--width`（`--once` 时的宽度，110）、
`--log-lines` / `--event-lines`（显示多少行）、`--tab {dashboard,leaderboard,messages,battles,records}`、
`--stats-who {1,2}`（1=玩家，2=联盟）、`--battles-order {units,date}`、
`--message-index` / `--battle-index` / `--record-index`（`--once` 时打印某条详情而不是列表）。
`--once` 走纯文本、不用 curses，适合日志和测试。

### 探险

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `--group N` | 1 | 第几个探险编队（从 1 开始） |
| `--target X:Y:Z` | 当前星系位置 16 | 目标坐标 |
| `--speed 1..10` | 10 | 舰队速度 |
| `--stay-time N` | 1 | 停留选项，对应第二步页面 `staytime` 的**第几项**（1..探险技术等级）；`0` 会被游戏以「无效返回时间」拒绝 |
| `--target-score N` | 500000 | 期望的无重氢舰队积分，**只影响报告给出的建议**，不改变实际舰队 |
| `--ship ID` | 报告推荐 | 强制指定舰船 id |
| `--count` / `--max-count` | 1 / 100 | 造几艘 / 单次调用上限 |
| `--max-dispatch N` | 1 | 单轮最多派几队 |
| `--auto-build` / `--build-count` / `--build-timeout` | 关 / 1 / 300 秒 | 有空闲航道却没编队时补造一批，并等船坞队列 |

上表混合了探险相关的几个脚本：发送类（`dispatch_expedition`、`expedition_cycle`、
`expedition_farm`、`run_forever --expedition-*`）用 `--group` / `--target` / `--speed` / `--stay-time`；
造船用 `--ship` / `--count` / `--max-count`；`--max-dispatch` / `--auto-build` 只在
多航道循环里。

### 生产与威胁

- `production.py --set ELEMENT=RATIO`：比例是**百分数 0–100、步长 10**，可重复；
  写的是整张 `prod[]` 表，SDK 会先读当前值再合并，成功后按页面回读校验。
- `threat.py --recall`：只预览召回；真实召回要再加 `--execute --allow <舰队 id>`，
  一次一队。

## 5. 输出写到哪里

| 路径 | 内容 | 谁写 |
| --- | --- | --- |
| `state/journal.jsonl` | 每轮/每个动作的结果与证据 | `run_once`、`run_forever`、`agent` |
| `state/snapshots.jsonl` | 全量状态快照，一行一条 | `collect_state`、`run_forever` |
| `state/events.jsonl` | 快照 diff 出的事件/告警 | 同上 |
| `state/daemon.json`、`state/agent.json` | 心跳与状态，看板存活判断的权威依据 | `run_forever`、`agent` |
| `state/statistics.json`、`messages.json`、`battles.json`、`records.json`、`empire.json` | 看板读的各类快照 | `run_forever` |
| `state/directive.json` | 战略目标（阶段/约束/风险姿态） | `directive.py` |
| `state/session.json` | 共享会话 Cookie（**含凭证，不要提交**） | `session.py`、任意脚本密码登录后自动回写 |
| `state/STOP` | 急停开关：存在即拒绝执行 | 手动创建/删除 |
| `state/*.lock` | 单实例锁 | `run_forever`、`agent` |
| `live_pages/` | `page_census` 抓的原始页面样本 | `page_census` |

`state/`、`live_pages/` 都在 `.gitignore` 里。

## 6. 常见坑

- **别用 `--interval 0` 加速**：它只改请求/轮询间隔，不会减少一轮要做的事；单轮用 `--once`。
- **一个账号只能有一个写者**：`run_forever.py` 和 `agent.py` 都抢 `state/daemon.lock`，
  不要同时跑；脚本和浏览器共享会话时也不要两端同时发舰队。
- **默认会踢掉浏览器**：脚本优先，会话失效会自动用账号密码重建。和浏览器共用时
  加 `--no-relogin`（或 `NEWSTAR_RELOGIN=0`）并走 `session.py import`；临时账号加
  `--no-save-session`。判据看 stderr 的「会话：…」那一行。
- **签到页 GET 即领取**：判断签到状态用 `daily_bonus.py`，不要用
  `page_census.py --only bonus` 去「看一眼」，那会消耗当天唯一的机会。
- **`--target-score` 只影响报告**：真正派出去的还是游戏里现有的舰队。
- **管道用 `--json`**：默认输出是给人看的；成功/失败看退出码（见 §2）。
