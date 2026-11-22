# 路线图

按「先看得见，再想得清，最后才动手」推进。状态：✅ 完成　🔧 进行中　⬜ 未开始　❓ 待核实。

## P0 观测层：让状态可读、可回放 ✅

| # | 任务 | 状态 | 验收 |
| --- | --- | --- | --- |
| P0-1 | 页面普查，保存真实 HTML 样本 | ✅ | 64 条路由可枚举，异常路由有说明（见 `p0-page-census.md`） |
| P0-2 | 消息解析（AJAX 列表 + 分类计数） | ✅ | 分类、时间、发送方、正文、坐标可读；离线 fixture 测试 |
| P0-3 | 加成总表解析 | ✅ | 65 项标签→百分比/倍率可查 |
| P0-4 | 生产控制解析 | ✅ | 基础产能、建筑贡献、加成、仓容、日/周产量、生产比例 |
| P0-5 | 状态快照 | ✅ | 一次调用产出可 JSON 序列化的完整状态；单节失败不丢整条 |
| P0-6 | 事件 diff | ✅ | 消息、队列、能源、仓容、星球、科研、断档可识别 |
| P0-7 | 趋势报表 | ✅ | 时间窗内资源变化、产能、队列占用率、事件统计、断档 |

## P1 自动化：从计划到执行

| # | 任务 | 状态 | 验收 |
| --- | --- | --- | --- |
| P1-1 | 阶段识别 | ✅ | 资源处理单元/探险技术等级 → 阶段 |
| P1-2 | 依赖展开 + ROI 排序 | ✅ | 读页面前置条件生成 blocker；前置满足后阶段目标本身也进入计划；可负担性优先用页面 `costOverflow`；产出建筑按回本排序 |
| P1-3 | 只读计划 CLI | ✅ | `scripts/plan.py` 输出文字/JSON，不写游戏 |
| P1-4 | 单步执行器 + 安全闸 | ✅ | 默认 dry-run；执行需 allow-list；队列感知；日志落盘 |
| P1-5 | 常驻循环 | ✅ | 单进程锁、定时 plan→execute、指数退避、快照节流；`--once` 冒烟通过 |
| P1-6 | 事件驱动反应 | ✅ | 每轮对比消息计数；间谍/战斗/能源/断档触发 alert，`--pause-on-alert` 可暂停执行（只告警不改支出） |
| P1-7 | journal / 事件轮转 | ✅ | 按大小切分（默认 10MB，保留 `.1`，见 ADR-010）；历史用 jq / state_report 查 |
| P1-8 | TUI 看板 | ✅ | `scripts/tui.py` 只读展示资源、阶段、告警、事件、日志；`--once` 可回归 |
| P1-9 | 看板值班化 | ✅ | 心跳健康栏、队列明细/ETA、候选与缺口、探险/舰队/消息、滚动与 JSON 详情、轮转与旧 schema 兼容；真实实例 dry-run 下 `--once` 帧回归 |
| P1-10 | 排行榜看板 | ✅ | SDK 解析 `page=statistics`；守护采集玩家/联盟积分榜；TUI 第二 tab 切换/滚动；真实实例 55 名玩家 + 2 个联盟 |
| P1-11 | 消息阅读 tab | ✅ | 守护采集收件箱到 `state/messages.json`；TUI 第三 tab 列表 + 正文阅读；不维护已读状态；真实实例 4 页 / 33 条 |
| P1-12 | 战役名人堂 tab | ✅ | SDK 解析 `page=battleHall`；守护采集单位积分/日期两种排序到 `state/battles.json`；TUI 第四 tab 列表/详情/切换排序；真实实例 3 场 |
| P1-13 | 宇宙记录 tab | ✅ | SDK 解析 `page=records`；守护采集四个记录块到 `state/records.json`；TUI 第五 tab 分组列表/详情；真实实例 142 项 / 4 类 |

**P1-9 看板值班化记录（2026-11-14）**：真实实例上以 `run_forever --interval 30
--snapshot-every 1`（dry-run）运行，`scripts/tui.py --once` 能看到心跳/数据年龄/下轮、
资源、队列、阶段与 5 条候选（含缺口）、探险/舰队/消息、告警和折叠后的日志；交互模式
在 pty 里验证了 `G`/`d`/`d`/`q`（详情打开/关闭/退出，退出码 0）。`run_forever` 新增
`--status-file state/daemon.json` 心跳，journal cycle 增加 `plan_top` / `queue_items` /
`messages` / `evidence`；看板读取 `.1` 轮转文件并容忍缺 `kind` 的旧条目。

**P1-10 排行榜记录（2026-11-14）**：真实实例上 `get_statistics(who=1/2, sort=1)`
分别解析出 55 名玩家和 2 个联盟；联盟榜第 1 名总积分 605.35B、成员 7。
守护首轮采集写入 `state/statistics.json`（0 错误），TUI `--once --tab leaderboard
--stats-who 2` 可离线回归；交互模式在 pty 里验证 `2`→`w`→`1`→`q`，退出码 0。
SDK 解析对玩家榜（联盟列）和联盟榜（成员/成员积分列）分开处理。

**P1-11 消息阅读记录（2026-11-14）**：真实实例首轮采集 4 页、33 条消息、0 错误；
`--once --tab messages` 可列出时间/发送方/主题，`--message-index 0` 可打印正文。
交互模式在 pty 里验证 `3`→`j`→`Enter`→`d`→`2`→`1`→`q`，退出码 0。消息时间按
服务器墙上时间显示（naive 值不按 UTC 偏移）。采集只调用 `get_messages`，不标记
已读/归档/删除。

**P1-12 战役记录（2026-11-14）**：真实实例 `get_battle_hall(order="units"/"date")`
各解析出 3 场战役，单位积分 581,500、胜败颜色与战报 `raport` id 都能读出；
`run_forever --once --battles-every 1` 写入两份榜、0 错误；`tui.py --once --tab
battles` 与 `--battles-order date` 可离线回归，`--battle-index 0` 打印战报链接。
交互模式在 pty 里验证 `4`→`o`→`j`→`Enter`→`d`→`q`，退出码 0。

**P1-13 宇宙记录（2026-11-14）**：真实实例 `get_universe_records()` 解析出建筑 23、
科研 19、舰只 61、防御 39 共 142 项（空记录行被跳过），页面更新时间和
`danyi` 等保持者都能读出；`run_forever --once --records-every 1` 写入
`state/records.json`、0 错误；`tui.py --once --tab records` 与 `--record-index 1`
可离线回归，记录值（48/21、44%）与保持者均正确。

**P1 完成的定义**：能无人值守连续跑（已由 20h38m + 57min 两次运行验证）；
dry-run 与 execute 可随时切换；中途崩溃重启不重复下单；每条动作都能在
journal 里回放。

### P1 冒烟记录（2026-11-13）

- 命令：`run_forever --interval 45 --execute --allow 14,108,113,31,1,2,4,3 --snapshot-every 2`
- 5 分钟内 5 轮：能源技术 0→1、自律工厂 1→2、能源技术 1→2、自律工厂 2→3、计算机技术 1→2
- 建造/研究各自排队，队列忙时自动落到另一条；journal 中无重复下单
- 快照每 2 轮写入，事件（research_completed、queue_item_*）正常落库
- 冒烟中发现：研究进行中升级科研实验室会被拒 → 执行器改为跳过该动作（有单测）
- 计划器验收：前置满足后阶段目标本身进入 blocker；`expedition_prep` 可展开到探险技术 124→9（`tests/test_planner.py`）

### P1 soak 记录（2026-11-14）

- 20h38m：250 次尝试、56 次执行、172 次 `ActionRejected`、1 次 `paused`；服务无重启，退出 0
- 服务层正常：21 次网络错误退避后恢复，114 份快照、31 条事件、0 份快照有分节错误，无重复成功下单
- 策略层问题：概览幽能 308,289 是毛产能，研究页实际缺口 102,456；规划器误判引力子可负担，执行器每轮只试第一条，约 15h 空转，阶段停在 `pre_gravity`
- 已修：规划器改用页面 `costOverflow`/`missing_resources`；执行器对预检查拒绝回退下一条候选（ADR-012）

### P1 修复短跑（2026-11-14）

- 57m28s：11 轮，9 次执行，2 次 `paused`，0 次 `error` / `ActionRejected`；干净退出并清理锁
- 2 次暂停都来自 `--pause-on-alert`：1 次消息计数请求超时，1 次收到间谍报告
- 规划器不再重复尝试引力子；幽能发电站 19→21，引力子缺口 102,456→35,811，执行器回退与缺口判断已生效
- 阶段仍停在 `pre_gravity`，资源处理单元尚未入队；按计划暂缓实测，先进入后续开发

**P1 收尾（2026-11-18）**：不再要求 24 小时连续 soak；以上 20h38m 与 57min 两次运行
作为 P1 验收。

## P2 策略扩展

| # | 任务 | 状态 | 说明 |
| --- | --- | --- | --- |
| P2-1 | 探险阶段规划 | ✅ | 航道/积分规划、建船建议与前置链、编队拆分、单队/多队预览与发送、自动补船闭环已支持；本机验证发送/召回 |
| P2-1b | 探险调度接入守护循环 | ✅ | `run_forever --expedition` 按轮次节流调用 `expedition_cycle`，独立舰船 allow-list/上限；本机 `--once` 实测发送并召回 |
| P2-1c | 每日签到 | ✅ | 概览导航判断可用性（不碰 `page=bonus`），`--execute` 下守护循环自动领取；本机 2026-11-16 实测领取 +24h 冷却 |
| P2-1d | 常驻探险调度 | ✅ | `expedition_farm` 维护返航时间表，按返航 +5s 补下一波，每 10h 重查最大航道；离线单测覆盖时间解析/时间表/填满/舰船不足，未跑 live（避免替换浏览器会话） |
| P2-2 | 舰队与威胁响应 | ✅ | 间谍/战斗报告 + 舰队表 → `calm`/`watch`/`danger`；`--threat-every` / `--pause-on-threat`；召回支持预览 + 舰队 id allow-list + 页面验证（资源转移/多星球仍属 P2-3） |
| P2-3 | 多星球调度 | ⬜ | 只读解析已就绪（`control` 全局面板 + `scripts/empire.py`）；跨星调度、切星、分星计划未做 |
| P2-6 | 自动殖民与按条件留球 | ✅ | `autoplay/colonize.py` + `scripts/colonize.py`：两种范围写法、面积/最高/最低温度条件、min(用户上限, 游戏剩余殖民位)、航道预留探险队、发船/到达对账/不达标放弃、在途撤回、开始/报错/停止飞书通知 |
| P2-4 | AI 参谋（只读建议） | ⬜ | 低频复盘、异常归因；决策边界见 ADR-006 |
| P2-5 | 影子模式评估 | ⬜ | 记录「AI 建议 vs 实际执行」，用数据决定是否采纳 |

P2-1b 实测（2026-11-14）：`run_forever --once --execute --allow 4 --expedition --expedition-allow 202 --expedition-every 1` 在同一轮执行资源动作并派出探险队，随后立即召回成功。

P2-1c 实测（2026-11-16）：概览导航显示 `每日签到` 链接时用一次 GET 领取，获得暗物质 160 / 反幽能 95 / 宝箱 3（资源条 921 +160、922 +95、924 +3 对照一致）；随后导航变为 `17.11.2026 22:20:25`，冷却期内 `claim_daily_bonus()` 返回 `claimed=False` 且不访问签到页。解析样本 `tests/fixtures/bonus_claim.html`，测试 `tests/test_daily_bonus.py`；脚本 `scripts/daily_bonus.py`，守护循环 `--no-bonus` 可关，journal `kind=bonus`。

P2-2 记录（2026-11-18）：`autoplay/threat.py` 从间谍报告（分类 0）与战斗报告
（分类 3）合成威胁等级：60 分钟内有侦察、或战斗正文指向我方损失 → `danger`；
24 小时内 → `watch`；其余 `calm`。真实实例上 `scripts/threat.py` 读到了 2026-11-14
那次侦察（`母星[3:494:3] → 母星[3:505:8]`），当前判为 `calm`（已过期）。守护用
`--threat-every N`（默认关闭）周期评估、`--pause-on-threat` 在 danger 时只暂停当轮；
journal 有 `kind=threat` 与 cycle 的 `threat` 字段，TUI 健康栏显示「威胁 平静/关注/危险」。
召回走 `autoplay/threat_executor.py`：默认预览、一次一支、需要 `--execute` + 舰队 id
allow-list，成功后必须看到舰队消失或返航标记；真实召回未执行（当前账号在途舰队为 0，
预览返回 skipped）。敌方强度、资源转移、自动召回都不做，见 ADR-026。

P2-3 前置记录（2026-11-18）：`page=control` 解析与 `scripts/empire.py` 已完成
（见「核实与技术债」），守护按 `--empire-every` 采集 `state/empire.json`，看板主面板
新增「帝国」块（殖民位 + 每星面积/资源/舰防，无文件时隐藏）。**跨星调度、切星、
分星计划仍未做**，所以 P2-3 保持 ⬜——当前账号只有 1 颗星，多星路径只能离线构造。

P2-1 当前能力：`autoplay.expedition` 提供航道、每队积分、目标坐标、建船建议、前置 blocker 链和多航道编队拆分；`scripts/expedition.py` 只读输出。`scripts/dispatch_expedition.py` 默认预览，`--execute --allow` 可发送单队；`scripts/build_expedition_ships.py` 默认预览，`--execute --allow` 可建造推荐舰船；`scripts/expedition_cycle.py` 每轮重读报告并逐队发送，支持 `--auto-build` 单轮补船，受 `--max-dispatch` 限制。测试见 `tests/test_expedition.py`、`tests/test_expedition_executor.py`、`tests/test_ship_build.py`、`tests/test_expedition_cycle.py`。本机实测：1 艘轻型运输舰 → `3:505:16`，燃料 3，任务 15/18；`stay_time=0` 被拒绝，改为 `1` 后发送成功并立即召回；强制 `--ship 202` 建造 1 艘后舰船数 1→2；2 艘小船通过循环发送并召回。自动补船分支因当前实例只有 1 条航道、无法构造“有空闲航道但无船”的 live 场景，暂由单测覆盖；守护循环接入见 P2-1b。`scripts/expedition_farm.py` 是独立的常驻调度：固定编队（默认 8621 战列舰）填满所有航道，按舰队表返航时间 +5s 补下一波，时间表写 `state/expedition_timetable.json`，默认每 10h 重查最大航道；它与 `run_forever` 共用写者锁。测试见 `tests/test_expedition_scheduler.py`、`tests/test_expedition_farm_args.py`。

P2-6 记录（2026-11-22）：`autoplay/colonize.py` 放范围解析（`1:2:3 4:5:6`
与 `1-4:2-5:3-6`）、条件（面积/最高温度/最低温度，未指定不检查，`accept=false` 全拒）、
航道数学（`舰队航道 - 在飞 - max(0, 最大探险队 - 在飞探险队)`）、
`min(用户上限, 殖民位上限 - 已用)`、扫描游标/候选/账本/在途时间表的持久化；
`scripts/colonize.py` 按轮读概览 + 舰队表 + `control`，殖民成功后切星读真实面积/温度，
不达标走 `page=overview&mode=delete` 放弃（需账号密码），到上限则撤回在途殖民船。
SDK 新增 `parse_fleet_slots` / `parse_fleet_table` / `client.get_fleet_table()` /
`client.abandon_planet()`。离线测试 `tests/test_fleet_table.py`、`tests/test_colonize.py`、
`tests/test_colonize_script.py`（fake client 覆盖发船、抢位、硬失败、对账、上限撤回、
无船/无燃料/已扫完/上限不可读等停止条件）。live：2026-11-22 预览跑过一次
`--range '1:1:1 1:1:15'`，确认 `control` 殖民位、舰队航道计数与概览解析与真实页面一致；
随后实例 `game.php` 持续超时，真实发船/放弃未在本机验证。

## P3 AI 战略层（仅 AI-0 / AI-1 已立项）

目标是把“大方向策略”从代码里的固定阶段目标和 2023 攻略中解耦出来。分两步走：
AI-0/1 只外置知识与目标；AI-2/3 让模型做规划，但事实、执行与合法性判定仍在代码侧
（ADR-030 / ADR-031）。

| # | 任务 | 状态 | 交付物 | 验收 |
| --- | --- | --- | --- | --- |
| AI-0 | 知识外置：doctrine + playbooks + 默认策略 | ✅ | `docs/doctrine.md`、`docs/playbooks.md`、默认 directive（doctrine 里 v1 草案） | doctrine 覆盖现有 3 个阶段、2 条核心机制事实和 8 条原则；playbooks 覆盖 9 个危机场景；默认策略由 `tests/test_planner_golden.py` 逐字段冻结 |
| AI-1 | Directive 抽象：planner 接受可注入目标 | ✅ | `autoplay/directive.py`（`Directive` + 校验 + 原子落盘）、`state/directive.json`、journal/TUI `directive` 字段 | 默认 directive 由 golden 测试证明逐字段一致；原子写入（temp + fsync + rename）；旧 journal 缺字段时看板退回原布局；executor 零改动 |
| AI-2 | 事实 → 简报、动词工具、按前提执行 | ✅ | `autoplay/brief.py`、`autoplay/tools.py`、`autoplay/runner.py`；`tests/test_brief.py` / `test_tools.py` / `test_runner.py` | 简报只含代码算好的数；工具按动词划分（不按元素爆炸）；写工具 `precheck=False`；结果区分 `requested`/`actual`；runner 遇阻即停 |
| AI-3 | 决策循环与模型接入 | ✅ | `autoplay/agent.py`、`autoplay/prompt.py`、`autoplay/llm.py`、`scripts/agent.py`；`tests/test_agent.py` | 写工具只登记、由 runner 统一执行；模型失败回退确定性规划器；`state/STOP` 优先；默认 dry-run；journal `kind=agent` |

**AI-0 知识外置（不接模型）**

- 把 2023 攻略里的机制、阶段、胜负条件、原则和常见死法整理成 `docs/doctrine.md`；
  明确标注“原则 / 假设 / 待页面验证”，页面事实优先，避免把过期攻略当常量。
- 把被侦察、来袭、资源枯竭、队列空转、探险损失、能源转负等场景整理成
  `docs/playbooks.md`；本期只写文档和确定性应对，不接 AI。
- 定义默认 directive / 默认策略，内容等价于当前 `phases.py` 的三个阶段目标。
- 验收：默认策略在 `tests/test_planner.py` 的样本上产出与当前一致的计划；文档
  可被人直接执行，不依赖模型解释。

**AI-0 记录（2026-11-18）**：`docs/doctrine.md` 标注了 ✅/📘/❓ 三种事实来源，
覆盖三个阶段的进入/完成条件、8 条原则和「假设与待验证」表；幽能语义按 2026-11-18
的核实结果写（速率资源、毛产是门槛比较值）；`docs/playbooks.md` 写了 9 个危机场景，
每个都给了触发信号、代码当前行为、人工动作和缺口。默认 directive 以 JSON 草案形式
放在 doctrine 第二节，字段与 `phases.py` 一一对应；`tests/test_planner_golden.py`
冻结了 fixture 上的阶段、目标、8 条候选（顺序/等级/回本有无/优先级）与 notes，
AI-1 直接用它证明「默认 directive 下计划逐字段一致」。

**AI-1 Directive 抽象（不接模型）**

- 把 `phases.py` 的固定 `Phase` 改为“默认 directive + 可注入 directive”；
  planner 仍从页面读事实、展开依赖、算 ROI，只是目标来源可替换。
- directive 只描述目标、约束、风险姿态和失效条件，不描述具体动作；所有数字由
  代码注入，AI 不参与（本期尚无 AI）。
- `state/directive.json` 原子落盘；journal 记录来源、年龄、理由和校验结果；
  TUI 在现有面板上增加最小展示，旧条目缺字段时退回原布局。
- 验收：`executor` 零改动；默认 directive 下计划与当前逐字段一致；directive 损坏
  或校验失败时回退默认策略；`ruff` + `pytest` 全绿。

**AI-1 记录（2026-11-18）**：`autoplay/directive.py` 提供 `Directive`、
`parse_directive` / `load_directive` / `save_directive` / `default_directive`；
`detect_phase` 改为「按 directive 顺序找第一个未满足的阶段」，默认 `PHASES` 下与旧
逻辑等价。`run_forever --directive`（默认 `state/directive.json`）每轮读一次，
journal 的 cycle 条目新增 `directive{source,version,path,age_seconds,risk_posture,
expires_when,phases,errors}`，TUI 健康栏显示「默认策略 / 策略 N 分前 / 策略文件有误，
已用默认」。校验覆盖版本、空列表、重复 key、goal_kind、element/level、milestone
必须最后；任何错误都回退默认并写 `errors`，不中断循环。新增
`scripts/directive.py`（查看 / `--json` / `--write-default` / `--validate`）与
`tests/test_directive.py`（24 个用例，含注入后 planner 换目标、坏文件回退、原子写）；
`executor` 未改动。

**边界与后续（P4）**

AI-2/3 已接模型：ADR-006 被 ADR-031 取代；写操作 `precheck=False`、由游戏裁决（ADR-030），
执行层保留 dry-run 默认、页面证据与 `state/STOP`。P4-2、P4-6 与 P4-7 已完成（见下方
记录）；下列按优先级排列，下一轮长跑前优先 P4-1 与 P4-3。

| # | 要做什么 | 状态 | 触发它的问题 | 验收 |
| --- | --- | --- | --- | --- |
| P4-1 | 去掉旧确定性脚本的 allow-list | ⬜ | `run_forever`、探险、生产、召回仍需人预先授权 | `--execute` 不再要求 `--allow`；脚本与测试同步，行为无其它变化 |
| P4-2 | agent journal 可回放 | ✅ | 读工具结果（`expedition_report` 等）未入库；无提示词/工具版本哈希，事后无法复原模型看到了什么 | 每条含 `prompt_hash`/`tools_hash` 与 `tool_events`；同一 brief 能复现决策 |
| P4-6 | 船坞剩余槽位进简报 | ✅ | 本实例只有 5 个队列槽，模型一次提交 6 种舰必然撞上限 | 简报给出剩余槽位；`build_ships` 按槽位拆单或在提交前拒绝 |
| P4-7 | 新增 `ships_at_least` 条件 | ✅ | 模型拿 `queue_empty: shipyard` 代理"船造好了没"，多次空等 | `load_facts` 读可用舰数；舰队就绪才放行 `send_fleet` |
| P4-3 | 工具参数枚举随页面生成 | ⬜ | 模型可能报出本服不存在的元素/舰种 | schema 每次由当前页面生成；非法值在 dry-run 即 `INVALID_ARGS` |
| P4-4 | 离线评估脚本 `scripts/eval_agent.py` | ⬜ | 只有实跑，没有回归基线 | 固定快照集上输出 JSON 合法率、与 planner 意图一致率、拒绝率 |
| P4-5 | `run_forever` 的模型路由 | ⬜ | 每轮都叫模型，多数轮次无事可做 | 常规轮走 planner，告警/阶段切换才调模型，并记录路由原因 |
| P4-8 | 探险编队只用可派遣舰种 | ⬜ | 编队里出现过殖民舰、探测器与未知 mod 舰 | 编队模板排除非战斗/运输舰，或提示词明确禁止 |
| P4-9 | 看板显示 agent 状态 | ⬜ | TUI 只读 `daemon.json`，看不到 agent 心跳与 failed 比例 | TUI 增加 agent 块：cycles / status / failed 率 |

**P4-2 记录（2026-11-20）**：`autoplay/agent.py` 的 `Decision` 记录
`prompt_hash` / `tools_hash` / `brief_hash`（系统提示词、工具 schema、渲染后 user
消息的 sha256）与 `max_chars`，以及按顺序的 `tool_events`（`round` / `id` / `tool` /
`kind` / `args` / `result`）：读工具（`observe` / `plan` / `expedition_report` /
`messages`）的完整结果直接入库，写工具的 staged 回复也留档；`decision.transcript`
从「工具名摘要」升级为每轮 `content` + `tool_calls`（含 `arguments`）。模型在回合中途
失败时，已发生的读调用和哈希仍保留（`_carry_trace` 把 trace 带到回退决策）。
验收测试 `tests/test_agent.py::test_journal_entry_is_enough_to_replay_the_decision`：
从一条 journal 记录重建系统提示词/工具 schema/简报并校验三个哈希，再把 `transcript`
转成 provider 回复喂回 `decide()`，计划与 `tool_events` 逐字段相同；
`test_read_tool_results_land_in_the_journal_trace` 覆盖读结果入库，
`test_provider_failure_keeps_the_partial_trace` 覆盖失败时的 trace 保留。

**P4-6 记录（2026-11-20）**：`autoplay/limits.py` 固定本实例船坞上限 5 槽（页面不
暴露这个数字）；简报 `queues.shipyard` 新增 `limit` / `free`，`render_brief` 显示
「剩余槽位 N/5」。`build_ships` 提交前读一次船坞队列：槽位不足时按模型给的顺序只提交
前 `free` 种，其余放进 `actual.skipped` 并返回 `QUEUE_SLOTS_LIMIT`
（`retryable=True`）；槽位为 0 时整单不 POST；队列读不到则不本地拦，仍由游戏裁决。
`runner` 把 `QUEUE_SLOTS_LIMIT` 与 `QUEUE_FULL` 一样记为 `blocked`，cycle 不判
`failed`。上限可用 `--shipyard-slots` 覆盖。测试：`tests/test_brief.py`（槽位与渲染）、
`tests/test_tools.py`（拆单/满槽/读不到）、`tests/test_runner.py`（blocked）、
`tests/test_agent_args.py`（CLI 默认值）。

**P4-7 记录（2026-11-20）**：`autoplay/runner.py` 的 `Facts` 新增 `ships` /
`ships_ok`，`check` 支持 `{"kind": "ships_at_least", "ships": {"202": 4}}`：读船坞页
`ShipInfo.available`（在港舰数，不含在途舰队与建造队列），缺任一舰种或数量不足即不
成立，读不到时 fail-closed。`load_facts(client, directive, steps=...)` 只在计划里有
步骤的 `when` / `until` 用到该条件时才多读一次船坞页；写后刷新只按当前及剩余步骤判断。
提示词把发船前检查改为 `ships_at_least`，并说明 `until` 只是立刻复查、不会等待。
测试 `tests/test_runner.py`：在港数判定、缺失舰种、读不到时 fail-closed、按需读取
（`ship_reads`）、`send_fleet` 未就绪时 blocked、就绪后放行。

## 核实与技术债

| 项 | 说明 | 处理方式 |
| --- | --- | --- |
| ✅ 会话缓存与策略 | 密码登录只留在进程内存，`state/session.json` 不更新；文件有效时又不允许重登，`expedition_farm` 遇到会话失效会无限退避 | 2026-11-20 完成（ADR-032 / ADR-033）：登录结果回写共享文件；默认脚本优先、`--no-relogin` 切浏览器优先（退出码 4）；共享会话忽略本地 Cookie `expires`（SDK `ignore_cookie_expiry`）；长跑脚本对会话错误 fail-fast。测试 `tests/test_local_config.py`、`tests/test_client.py`、`tests/test_expedition_farm_args.py` |
| ✅ 仓容语义 | 资源远超仓容是朋友赠送所致；爆仓时产量停摆、资源不涨属正常，capacity 可信 | 2026-11-18 确认，不再当异常 |
| ✅ 爆仓期 ROI | 资源超过仓容时产量不累积，产出建筑的 payback 假设失效 | 2026-11-18 修复：用产量表容量与概览资源量判断爆仓，回本置 `null` 并排到末尾（`autoplay/planner.py` 的 `_storage_usage`，`tests/test_planner.py`） |
| ✅ 幽能语义 | 产量表 911 的 `total` 是净产；概览 `capacity` 是毛产（= 净产 + 三厂消耗），页面显示 `净产/毛产`（实测 `392 K/600 K`、65%）；概览 `amounts[911]` 是「毛产 + 净产」的合成值，两天不变而净产 +392,000/h，证明它不是库存。引力子这类科技的幽能需求是门槛、不消耗，游戏用毛产比（实测 765,000 对 600,017） | 2026-11-18 核实：不把概览容量传给规划器做爆仓判断；幽能发电站改为按净产报告与排序，不再给虚假回本（`autoplay/planner.py`、`tests/test_planner.py`） |
| ✅ `control` 全局面板 | 单请求全量状态：每星资源/点数池/每小时产能/建筑/舰只/防御 + Σ + 殖民位；按标签列与 section 顺序对齐 | 2026-11-18 完成：`parse_control` / `client.get_control()` / `scripts/empire.py`，`tests/test_control.py`（10 个用例，含脱敏 fixture）；mod 专有单位列进 `unresolved` |
| ✅ 舰队航道与舰队表 | `page=fleetTable` 的「舰队航道 x / y」和「x / y 探险队」可直接解析；同一页还给出当前星球可派舰船 | 2026-11-22 新增 `FleetSlots` / `FleetTable` / `parse_fleet_table` / `client.get_fleet_table()`，`tests/test_fleet_table.py` |
| ✅ 放弃殖民地 | `page=overview` 的 `mode=delete` POST + 账号密码，返回 JSON；仅标记 `destruyed`，约 24h 后清理 | 2026-11-22 新增 `client.abandon_planet()`（成功回读概览确认），写操作未 live 验证 |
| ✅ 星图面积/温度不可预筛 | 空位 tooltip 的面积/温度来自 `PlanetData.php` 的 `mt_rand`，每次请求都变；殖民后 `createPlanet` 再随机一次 | 殖民脚本只把星图值当展示，判定用殖民后概览的 `fields_max` / `temp_min` / `temp_max` |
| ✅ 生产比例写入 | 只读与写入都已支持：`client.set_production_ratios`（整表提交 + 页面回读验证）、`scripts/production.py`、`autoplay/power.py` 缺电降耗、守护 `--balance-power`/`--balance-allow` | 2026-11-18 实机验证 100%→90%→100% 与缺电预览，见 `docs/usage.md` 第 22 节 |
| ⬜ `market` / `fair` | 本实例禁用 | 不投入；若换实例再评估 |
| ⬜ SQLite | 当前 JSONL 够用 | 触发条件见 ADR-002 |
| ❓ 攻略时效 | 攻略为 2023 年纪元 | 以页面数据为准；机制疑点靠页面普查确认 |

## 更新方式

完成一项就把状态改成 ✅ 并补一行验收证据（命令或结论）；新任务追加到对应阶段，
不要另开一份计划文档。历史变更看 `git log`。
