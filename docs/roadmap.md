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
| P1-5 | 常驻循环 | ✅ | 单进程锁、定时 plan→execute、指数退避、快照节流；`--once` 冒烟通过（24h soak 待跑） |
| P1-6 | 事件驱动反应 | ✅ | 每轮对比消息计数；间谍/战斗/能源/断档触发 alert，`--pause-on-alert` 可暂停执行（只告警不改支出） |
| P1-7 | journal / 事件轮转 | ✅ | 按大小切分（默认 10MB，保留 `.1`，见 ADR-010）；历史用 jq / state_report 查 |
| P1-8 | TUI 看板 | ✅ | `scripts/tui.py` 只读展示资源、阶段、告警、事件、日志；`--once` 可回归 |
| P1-9 | 看板值班化 | ✅ | 心跳健康栏、队列明细/ETA、候选与缺口、探险/舰队/消息、滚动与 JSON 详情、轮转与旧 schema 兼容；真实实例 dry-run 下 `--once` 帧回归 |
| P1-10 | 排行榜看板 | ✅ | SDK 解析 `page=statistics`；守护采集玩家/联盟积分榜；TUI 第二 tab 切换/滚动；真实实例 55 名玩家 + 2 个联盟 |
| P1-11 | 消息阅读 tab | ✅ | 守护采集收件箱到 `state/messages.json`；TUI 第三 tab 列表 + 正文阅读；不维护已读状态；真实实例 4 页 / 33 条 |
| P1-12 | 战役名人堂 tab | ✅ | SDK 解析 `page=battleHall`；守护采集单位积分/日期两种排序到 `state/battles.json`；TUI 第四 tab 列表/详情/切换排序；真实实例 3 场 |

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

**P1 完成的定义**：能无人值守连续跑 24 小时；dry-run 与 execute 可随时切换；
中途崩溃重启不重复下单；每条动作都能在 journal 里回放。

### P1 冒烟记录（2026-11-13）

- 命令：`run_forever --interval 45 --execute --allow 14,108,113,31,1,2,4,3 --snapshot-every 2`
- 5 分钟内 5 轮：能源技术 0→1、自律工厂 1→2、能源技术 1→2、自律工厂 2→3、计算机技术 1→2
- 建造/研究各自排队，队列忙时自动落到另一条；journal 中无重复下单
- 快照每 2 轮写入，事件（research_completed、queue_item_*）正常落库
- 冒烟中发现：研究进行中升级科研实验室会被拒 → 执行器改为跳过该动作（有单测）
- 计划器验收：前置满足后阶段目标本身进入 blocker；`expedition_prep` 可展开到探险技术 124→9（`tests/test_planner.py`）
- **24 小时连续 soak 尚未跑**；跑法见 `usage.md` 第 14 节，建议挂 systemd/nohup

### P1 soak 记录（2026-11-14）

- 20h38m：250 次尝试、56 次执行、172 次 `ActionRejected`、1 次 `paused`；服务无重启，退出 0
- 服务层正常：21 次网络错误退避后恢复，114 份快照、31 条事件、0 份快照有分节错误，无重复成功下单
- 策略层问题：概览幽能 308,289 是毛产能，研究页实际缺口 102,456；规划器误判引力子可负担，执行器每轮只试第一条，约 15h 空转，阶段停在 `pre_gravity`
- 已修：规划器改用页面 `costOverflow`/`missing_resources`；执行器对预检查拒绝回退下一条候选（ADR-012）

### P1 修复短跑（2026-11-14）

- 57m28s：11 轮，9 次执行，2 次 `paused`，0 次 `error` / `ActionRejected`；干净退出并清理锁
- 2 次暂停都来自 `--pause-on-alert`：1 次消息计数请求超时，1 次收到间谍报告
- 规划器不再重复尝试引力子；幽能发电站 19→21，引力子缺口 102,456→35,811，执行器回退与缺口判断已生效
- 阶段仍停在 `pre_gravity`，资源处理单元尚未入队；按计划暂缓实测，先进入后续开发；完整 24h soak 待以后补跑

## P2 策略扩展

| # | 任务 | 状态 | 说明 |
| --- | --- | --- | --- |
| P2-1 | 探险阶段规划 | ✅ | 航道/积分规划、建船建议与前置链、编队拆分、单队/多队预览与发送、自动补船闭环已支持；本机验证发送/召回 |
| P2-1b | 探险调度接入守护循环 | ✅ | `run_forever --expedition` 按轮次节流调用 `expedition_cycle`，独立舰船 allow-list/上限；本机 `--once` 实测发送并召回 |
| P2-2 | 舰队与威胁响应 | ⬜ | 被侦察/来袭时收缩或转移；需要消息与舰队状态联动 |
| P2-3 | 多星球调度 | ⬜ | 依赖 `control` 全局面板解析（见待核实） |
| P2-4 | AI 参谋（只读建议） | ⬜ | 低频复盘、异常归因；决策边界见 ADR-006 |
| P2-5 | 影子模式评估 | ⬜ | 记录「AI 建议 vs 实际执行」，用数据决定是否采纳 |

P2-1b 实测（2026-11-14）：`run_forever --once --execute --allow 4 --expedition --expedition-allow 202 --expedition-every 1` 在同一轮执行资源动作并派出探险队，随后立即召回成功。

P2-1 当前能力：`autoplay.expedition` 提供航道、每队积分、目标坐标、建船建议、前置 blocker 链和多航道编队拆分；`scripts/expedition.py` 只读输出。`scripts/dispatch_expedition.py` 默认预览，`--execute --allow` 可发送单队；`scripts/build_expedition_ships.py` 默认预览，`--execute --allow` 可建造推荐舰船；`scripts/expedition_cycle.py` 每轮重读报告并逐队发送，支持 `--auto-build` 单轮补船，受 `--max-dispatch` 限制。测试见 `tests/test_expedition.py`、`tests/test_expedition_executor.py`、`tests/test_ship_build.py`、`tests/test_expedition_cycle.py`。本机实测：1 艘轻型运输舰 → `3:505:16`，燃料 3，任务 15/18；`stay_time=0` 被拒绝，改为 `1` 后发送成功并立即召回；强制 `--ship 202` 建造 1 艘后舰船数 1→2；2 艘小船通过循环发送并召回。自动补船分支因当前实例只有 1 条航道、无法构造“有空闲航道但无船”的 live 场景，暂由单测覆盖；守护循环接入见 P2-1b。

## 待核实 / 技术债

| 项 | 说明 | 处理方式 |
| --- | --- | --- |
| ❓ 仓容语义 | 概览 capacity 与资源量严重不符，但产能仍在跑 | 对照游戏内仓库页/建筑页确认；确认前不当硬约束 |
| ❓ `control` 全局面板 | 单请求全量状态，但是纵向栅格布局 | 需要按列位置解析；多星球前再做 |
| ⬜ 生产比例写入 | 只读已支持，写入（`prod[]` 表单）未封装 | 需要先确认写入语义与风险 |
| ⬜ `market` / `fair` | 本实例禁用 | 不投入；若换实例再评估 |
| ⬜ SQLite | 当前 JSONL 够用 | 触发条件见 ADR-002 |
| ❓ 攻略时效 | 攻略为 2023 年纪元 | 以页面数据为准；机制疑点靠页面普查确认 |

## 更新方式

完成一项就把状态改成 ✅ 并补一行验收证据（命令或结论）；新任务追加到对应阶段，
不要另开一份计划文档。历史变更看 `git log`。
