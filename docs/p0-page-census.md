# P0 观测性 · 第 1 步：页面普查

> 状态：已完成（2026-11-13）
> 脚本：[`scripts/page_census.py`](../scripts/page_census.py)
> 产物：`live_pages/census-2026-11-13/`（本地，已 gitignore，不入库）

## 1. 目的

SDK 的解析器目前只覆盖 2Moons 的核心页面（overview / buildings / research /
shipyard / galaxy / fleet）。在补 `messages`、`resources`、`infobonus` 之前，先把
**这个实例实际渲染的页面**抓下来：

- 上游 New-Star 的模板只是参考，这个服是二次魔改版（大量自定义建筑/舰船/系统）；
- 抓下来的 HTML 是后续写解析器和测试样本的唯一可靠依据；
- 顺便确认哪些系统在本服被砍掉或改名。

脚本只发 GET，不提交表单、不点任何操作按钮，不改变账号状态。

## 2. 实例指纹

| 项 | 值 |
| --- | --- |
| 游戏名 | 战争宇宙：猛犸 |
| 引擎 | New-Star 2.8.1 |
| 主题 | `nsc`（自定义 UI，非上游默认模板） |
| 路由前缀 | `game.php?page=<name>`，类名 `Show<Name>Page` |
| 页面总数 | 64 条路由（含消息分类变体），60 条正常返回 |

**重要**：游戏名与 `guide/新版本ol一本通攻略(1.0.0.2).docx` 标注的纪元
「战争宇宙：猛犸」一致，说明这份攻略描述的机制大概率仍然适用（域名不同，
应该是同一套 mod 迁移过服务器）。攻略可以当先验用，但具体数值仍以页面为准。

## 3. 怎么跑

```bash
# 默认抓 READ_ONLY_PAGES 列表
.venv/bin/python scripts/page_census.py --config passwd.txt --no-verify-ssl

# 全量 + 跟随导航发现新路由
.venv/bin/python scripts/page_census.py --all --discover --out live_pages/census

# 只抓几个页面（调解析器时最常用）
.venv/bin/python scripts/page_census.py --only messages,resources,infobonus
```

产物：

- `NNN_<label>.html`：原始响应字节（不做解码/清洗，保真）
- `manifest.json`：URL、状态码、大小、sha256、标题、marker 计数、错误页文本
- `index.md`：人读的汇总表

凭据解析顺序：命令行参数 > 环境变量 > `.env` > `passwd.txt`。
本实例证书过期，需要 `--no-verify-ssl`（等价于 `NEWSTAR_VERIFY_SSL=0`）。

## 4. 抓取结果

60/64 正常。4 条"异常"里只有 2 条是真问题：

| 路由 | 结果 | 判断 |
| --- | --- | --- |
| `fair` | 信息页「模块已禁用」 | 本服未启用，忽略 |
| `market` | 信息页「模块已禁用」 | **本服未启用**，攻略里的"市场寄存/转移"玩法不可用 |
| `phalanx` | 「超出感应距离！」 | 正常业务错误（没有月球/等级不够），不是页面问题 |
| `information` | HTTP 503 | 服务端异常，重试仍失败；此页不可用，不需要处理 |

`scan`、`notes`、`board` 返回极短页面（1 B / 4.7 KB / "网站维护中"），是空实现。

### 页面分组

| 组 | 路由 | 价值 |
| --- | --- | --- |
| 核心 | `overview` `buildings` `research` `shipyard` `fleetTable` `galaxy` | SDK 已覆盖 |
| 状态富矿 | `control` `infobonus` `resources` | **P0 要补** |
| 事件 | `messages`（需 AJAX 变体） | **P0 要补** |
| 行星管理 | `planet` `bonus` `createMoon` `phalanx` | P1 |
| 玩法系统 | `race` `party` `ideologies` `ethics` `minerals` `arsenal` `conteiner` `store` `premium` `officier` `bon` `bonus` `blueprints` `artifact` `auction` `band` `fleetDealer` `development` `details` | 逐步按需 |
| 信息 | `techtree` `statistics` `records` `achievements` `battleHall` `battleSimulator` `search` `questions` `ticket` `banList` `buddyList` `alliance` `board` `notes` `settings` `playerCard` `scan` `fair`(禁用) `market`(禁用) `information`(503) | 低频/参考 |

`techtree` 单页 630 KB，是前置依赖图的可视化，适合"按需抓一次、离线建图"，
不适合轮询。

## 5. 关键发现

### 5.1 消息列表是 AJAX 渲染的

`page=messages` 的 HTML 里只有分类计数（间谍/战斗/探险/运输…… 的未读/总数），
**没有消息正文**。正文来自：

```
game.php?page=messages&mode=view&messcat=<分类>&site=<页码>&ajax=1
```

分类 ID（本服）：`0` 间谍、`1` 玩家、`2` 盟友、`3` 战斗、`4` 系统、`5` 运输、
`15` 探险、`50` 游戏、`99` 建造、`100` 全部、`200` 归档、`999` 发件箱。

片段结构很规整，适合直接解析：

```
.messagestable
  .head_row_msg
    #message_<id>.message_head[.mes_unread]
      .message_time       "2026年11月13日, 15:25:59"
      .message_title .message_recipient_name   主题
      .messages_body .message_text             正文（含 <a> 坐标链接）
```

分类可以从 `msgDel(<id>, <cat>)` 的第二个参数拿到。

**这是 P0 里最重要的一条**：所有"发生了什么"（探险结算、战报、被侦察、运输到货）
都从这里来。只抓 `page=messages` 等于什么都没抓到。

### 5.2 `control`（全局面板）是单请求全量快照

`page=control` 一次返回：行星空位、每颗行星的资源/建造点/研发点/船坞点/防御点/
导弹点/每小时产能、全部建筑等级、全部舰船与防御数量、以及 Σ 汇总。

布局是**纵向表**：左列是标签（`.imper_block_td` 顺序固定），右侧每颗行星是一列
数值，靠位置对齐。解析要按标签顺序映射，比普通 `<table>` 麻烦，但一次请求就够。
当前只有 1 颗星，价值还不明显；殖民之后它就是唯一实用的多星状态源。

### 5.3 `infobonus`（加成总计）给出所有乘数

标签 → 百分比/倍率的干净列表，包含策略层直接需要的项：

`全资源生产加成`、`合金/水晶/重氢采集加成`、`能源生产加成`、`资源仓库储量`、
`建造/科研/舰只建造速度`、`降低建筑/舰只/研究/防御/导弹花费`、
`舰队航道`、`殖民地上限`、`探险队上限`、`探险队收益加成`、`重氢利用率` 等。

没有这些乘数，面板产能 ≠ 实际产能，ROI 排序会整体偏掉。**P0 必抓。**

### 5.4 `resources`（生产控制）是产能明细 + 调参入口

页面给出每颗行星的基础产能、建筑等级、加成、仓库容量、日/周产量，以及四个
下拉框 `prod[1] / prod[2] / prod[4] / prod[31]`（金属/水晶/电站/实验室的生产
百分比）和"所有行星停止/开始生产"按钮。

读取用 GET 就够；写入（调产能比例）是 `POST mode=send`，属于 P1 的执行动作，
不在本次范围内。

### 5.5 其他

- `planet`（天文馆）只操作**当前**行星：改名、加磁场、加直径、传送、放弃殖民地。
  没有"所有行星 + 面积 + 温度"的总表；殖民选点要靠星图 + 逐星切换。
- `bonus` 是当前行星总览（直径、面积、温度、积分/排名），比 `overview` 更聚焦。
- `fleetTable` 当前为空（没有在飞舰队），结构待有舰队时再确认。
- 账户初始状态：1 颗星、四座基础建筑 lv4、研究实验室 lv1，仓库里有大量
  起始资源（朋友服赠送）。这让"前期攒资源"阶段基本跳过，策略重点直接落在
  "怎么把这些资源高效换成产能和科技"。

## 6. 顺带修掉的 SDK 问题

普查时发现 `settings`、`playerCard` 被判成游戏错误页：

```
parse_game_error() 用 .ally_contents 判断错误页
-> 但本主题里 .ally_contents 是通用内容容器
-> 任何含该容器的正常页面都会被 client 当成 GameError 抛出
```

已改为：只认 **带标题的** 错误容器
`#ally_content > .gray_stripe("信息") + .ally_contents`；没有该结构时，
必须页面标题（`<title>` 去掉 `- 宇宙 - 游戏名` 后缀）正好是"信息/Information"
才判定为错误页。回归测试见 `tests/test_galaxy_fleet.py::test_error_page_needs_titled_container`。

修复后重跑普查，`settings` 和 `playerCard` 都正常了。

## 7. 下一步（Step 1：解析器）

按"策略价值 / 实现成本"排序（✅ = 已完成）：

| 优先级 | 解析器 | 数据源 | 关键字段 |
| --- | --- | --- | --- |
| 1 ✅ | `messages` | `mode=view&messcat=..&ajax=1` | id、时间、分类、主题、正文、坐标、未读 |
| 2 ✅ | `infobonus` | `page=infobonus` | 标签 → 百分比/倍率 |
| 3 | `production` | `page=resources` | 三资源基础产能、加成、仓库、日/周产量、当前比例 |
| 4 | `empire`（可选） | `page=control` | 每星资源/点数/建筑/舰船（位置对齐解析） |

建议先做 1 和 2：一个解决"发生了什么"，一个解决"乘数是多少"。
两者都只需要 GET + 纯解析，能直接进单元测试（用本次抓的 HTML 做 fixture，
脱敏后入库）。

### 已完成（2026-11-13）

- `parse_messages()` / `parse_message_counters()` / `parse_bonuses()`
- `client.get_messages(category, page)` / `get_message_counters()` / `get_bonuses()`
- CLI：`newstar messages [--category] [--page]`、`newstar bonuses`
- 回归样本：`tests/fixtures/messages_view.html`、`tests/fixtures/infobonus.html`
- 真实实例验证通过（4 条消息、65 项加成）

### 下一步状态（2026-11-13 更新）

- P0 Step 2/3 的最小版本已落地：`newstar_sdk/snapshot.py` + `scripts/collect_state.py`
- 每次运行输出一行 JSON（`state/snapshots.jsonl`，已 gitignore），包含
  overview / research / bonuses / queues / fleets / messages 计数
- 已补：`page=resources` 生产控制解析（`get_production()`）、快照 diff 事件
  （`newstar_sdk/events.py`，采集器默认写 `events.jsonl`）
- 已补：`scripts/state_report.py` 趋势报表（资源变化、产能、队列占用率、
  事件统计、断档检测），纯函数在 `newstar_sdk/report.py`
- 尚未做：SQLite 存储（目前 JSONL + 报表够用，等需要复杂查询再迁移）
