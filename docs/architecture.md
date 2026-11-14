# 架构与设计

## 1. 目标与非目标

**目标**：把 New-Star / 2Moons 网页游戏的常用操作封装成稳定的 Python 接口，让上层程序只关心“做什么”，不关心登录表单、页面结构、Cookie 和 POST 字段。

**非目标**：

- 不实现游戏服务器，也不修改游戏数据。
- 不依赖官方 API；New-Star 没有面向自动化的稳定 API。
- 不模拟浏览器 DOM/JS；只处理服务器返回的 HTML 和表单。
- 不保证对任意魔改实例 100% 兼容；解析器对结构变化是容忍的，但模板大改仍会失败。

## 2. 分层

`newstar_sdk` 只负责与游戏通信和执行单步操作；策略层（`autoplay/`）在它之上做
阶段识别、规划与执行编排，见 `docs/usage.md` 第 12–13 节和 ADR-001。

```
上层程序 / CLI
      │
NewStarClient (client.py)
      │  HTTP 会话、登录、动作编排、结果验证
      ├── parsers.py      纯 HTML -> dataclass / 基础类型
      ├── models.py       返回给调用方的数据结构
      ├── constants.py    元素/任务/资源 ID 与静态名称
      ├── utils.py        数值、JS 对象、坐标、HTML 文本
      └── exceptions.py   异常层次
```

原则：

- `parsers.py` 不发起请求，只做 HTML -> Python。
- `client.py` 不实现页面细节解析，只调用解析器。
- `models.py` 是公开契约；解析器返回这些类型，而不是裸字典。
- 静态 `constants.py` 只用于名称解析和兜底显示；页面上的实时名称优先。

## 3. 数据来源

### 3.1 服务端渲染 HTML

游戏是 PHP 服务端渲染，页面里已有：

- 元素容器：`div.build_box#build_<id>`、`#research_<id>`、`#s_<id>`。
- 成本、时间、等级：多数页面在 `DatatList = {...};` 中直接输出精确数值。
- 队列：`#buildlist .element_row`，包含元素 ID（背景图 URL）、剩余秒数、结束时间、目标等级/数量。
- 星图：`div.gal_user` 行，含位置、星球名、玩家名、月球/残骸图标、任务链接。
- 舰队：9 列的飞行舰队表；返航舰队没有召回表单，因此不能靠表单定位行。
- 错误页：带「信息」标题的容器（`#ally_content > .gray_stripe + .ally_contents`），或标题为「信息/Information」的 `.card-text` 页面。

### 3.2 DatatList

建筑/研究/船坞页面内嵌：

```js
DatatList = {
  "1": {"level":"3", "costResources":{"901":202.5,...}, "elementTime":1, ...},
  ...
};
```

它不是严格 JSON（有尾逗号，偶尔单引号/未加引号的键），所以 `utils.extract_datatlist()` 会：

1. 定位 `DatatList =` 后的平衡花括号块（忽略字符串里的括号）。
2. 去掉尾逗号、`undefined` -> `null`。
3. 先按 JSON 解析；失败后再尝试单引号转双引号、给未加引号的键加引号。
4. 失败时返回 `{}`，解析器退回 HTML 成本/等级解析。

`elementTime` 是秒；`costResources` 在研究中拼写为 `costRessources`（游戏原有拼写），SDK 两种都接受。

### 3.4 消息列表是 AJAX 片段

`page=messages` 只渲染分类标签和未读/总数；真正的消息列表由
`game.php?page=messages&mode=view&messcat=<id>&site=<page>&ajax=1` 返回一段
HTML 片段。`parse_messages()` 解析该片段，`parse_message_counters()` 解析外层
页面的计数。两者都不涉及写操作。

### 3.5 运行状态文件（只读看板的数据源）

常驻循环把可回放的状态写到 `state/`，看板只读这些文件：

| 文件 | 写入方 | 内容 |
| --- | --- | --- |
| `daemon.json` | `run_forever.py`，每轮原子写 | 心跳、pid、模式、间隔、轮次、失败数、上轮/下轮时间、阶段与目标 |
| `journal.jsonl` | 每轮一条 | cycle（动作、队列明细、候选、消息摘要、告警、备注）、`kind=error`、`kind=expedition` |
| `snapshots.jsonl` | 每 `--snapshot-every` 轮 | 完整账号快照（资源、队列、舰队、消息、研究、加成） |
| `events.jsonl` | 快照同拍 | 快照 diff 出的事件 |
| `statistics.json` | 每 `--statistics-every` 轮（首轮先采） | 排行榜：玩家/联盟积分榜、采集时间、逐榜错误 |
| `messages.json` | 每 `--messages-every` 轮（首轮先采） | 收件箱缓存：id、发送方、主题、正文、服务器时间、分类、坐标、逐页错误 |
| `daemon.lock` | 启动时创建、退出删除 | pid，单写者锁 |
| `STOP` | 操作者创建 | 急停开关，循环在下一拍退出 |

看板以 `daemon.json.updated_at` 判断守护是否存活，因为容器/PID 命名空间会让
`os.kill(pid, 0)` 看不到其他命名空间里的进程。journal 的 cycle 条目是看板的
主要信息源；新增字段必须保持向后兼容，旧条目缺少 `plan_top` / `queue_items` /
`messages` 时看板退回最小布局。轮转后的 `.1` 文件也会被读取。

### 3.3 为什么不用 JSON 接口

`json.php` 只返回攻击/侦察/消息计数，不能操作游戏。高层操作仍然必须走 `game.php` 的页面和表单。SDK 只是把这些页面请求封装起来，并在必要时解析返回页确认结果。

## 4. 登录与会话

登录流程：

1. `GET index.php`，解析 `select[name=uni]` 的宇宙选项。
2. `POST index.php?page=login`，字段 `uni`、`username`、`password`。
3. 成功时服务器返回 302 到 `game.php` 并设置 `2Moons` Cookie；失败时回到 `index.php?code=...`。

客户端用 `requests.Session` 保存 Cookie。会话检测的依据是最终 URL 是否回到 `index.php`，而不是某个固定 Cookie 名，因此私有实例改 Cookie 名也能工作。

会话过期时：

- 有账号密码且 `auto_relogin=True`：重登并重试一次。
- 否则抛 `SessionExpired`。

**重要约束**：舰队三步发送的 token 存在 PHP session 里。自动重登会创建新 session，旧 token 失效。因此重登后重试第三步会失败；上层如果需要强一致，应在发送前确保会话有效，失败时从头重新发送。当前实现不在 `send_fleet` 内部自动重启整个三步流程。

`save_session()` 只写 Cookie、用户名、宇宙和 base URL，不写密码；这是有意的安全取舍。需要自动重登时，调用方仍要在构造客户端时提供密码。

## 5. 元素识别与本地化

- 元素 ID 是稳定的，名称随语言包/私服改动。SDK 以 ID 为主键。
- 名称只用于便捷输入（`"metal"`、`"合金冶炼厂"`）和页面没有名称时的兜底显示。
- 静态表覆盖 New-Star 的标准建筑/科技/舰船/防御；私有实例新增的 ID 在页面没有提供名称时会以 `Element <id>` 显示，但仍可通过 ID 操作。
- 页面解析出的名称优先于静态表，所以同一 ID 在私服改名后，返回的 `name` 是页面上的名字。
- 任务名称映射同时包含中英文；`Mission` 枚举提供常用任务 ID。

## 6. 动作验证

SDK 不把 HTTP 200 当作成功。每个写操作都做“页面证据”验证：

- 建筑/研究：响应队列中出现目标元素且目标等级 >= 请求等级，或页面等级已 >= 目标。
- 拆除：队列中出现该元素且目标等级低于当前等级，或页面等级已下降。
- 船坞：队列中出现请求数量，或可用数量较操作前增加。
- 舰队：第三步返回确认页，解析出任务、起止坐标、距离、燃料、舰船。

验证失败抛 `ActionRejected`。这能挡住：

- 资源不足；
- 前置建筑/科技不满足；
- 队列已满；
- 页面结构变化导致 POST 字段失效；
- 游戏静默截断数量。

代价是每个写操作需要多解析一次响应（通常不需要额外请求），以及多级操作可能出现“部分入队后抛错”。文档中明确提示优先一次一级。

`ActionRejected.before_post` 区分两种来源：SDK 在发送 POST 前用页面数据挡住的动作
标记为 `True`，可以确定没有入队；POST 之后验证失败标记为 `False`，可能已经部分
生效。执行器只对前者尝试本轮的下一条候选，后者直接报错。

## 7. 错误模型

- 网络/超时/5xx：`TransportError`。GET 按 `max_retries` 重试；POST 默认不自动重试，避免重复执行写操作或使舰队 token 失效。
- 登录被拒：`AuthenticationError`。
- 会话失效：`SessionExpired`。
- 游戏错误页：`GameError`（带标题的错误容器，见 §3.1）。
- 动作未生效：`ActionRejected`（继承 `GameError`）；`before_post=True` 表示 SDK
  在发请求前就拒绝了，`False` 表示 POST 后验证失败。
- 页面结构无法解析：`ParseError`，或返回空列表/`None` 由调用方判断。

错误页文本可能是未翻译的键（如 `NFCoosen`），SDK 不做翻译，避免把猜测写进错误信息。

## 8. 设计取舍

SDK 内部的取舍见下表；项目层面的决策（分层、数据存储、执行安全等）见
[`decisions.md`](decisions.md)。

| 取舍 | 选择 | 原因 |
| --- | --- | --- |
| 同步/异步 | 同步 | 游戏操作有顺序和会话状态，异步并发容易触发竞态；上层可用线程池包装 |
| 浏览器自动化 | 不用 | HTML 请求更轻、更稳定，适合长期运行；代价是模板改版要改解析器 |
| TLS 默认 | 验证 | 安全默认；过期证书实例显式 `verify_ssl=False` |
| 凭证持久化 | 只存 Cookie | 避免明文密码落盘；需要自动重登时由调用方提供密码 |
| 请求频率 | 默认 0.25s 间隔 | 降低触发风控/限流的风险；可配置 |
| 元素名称 | ID 优先 | 私服改名/多语言不影响操作 |
| 写操作 | 页面证据验证 | 避免把静默失败当成成功 |

## 9. 已知脆弱点

- CSS 类名和容器 ID 是模板契约；主题大改会导致选择器失效。
- `DatatList` 缺失时，成本/时间解析精度下降，且多语言时间文本可能无法解析。
- 队列结束时间是本地化字符串，SDK 保留原文，同时尽量提供 `data-time` 剩余秒数。
- 返航舰队没有召回表单；如果私服把返航舰队也渲染成带表单，`can_recall` 可能为真，但游戏仍会拒绝。
- 舰队任务可用性由第二步页面给出；如果私服不渲染任务 radio，SDK 跳过本地可用性检查，交给第三步服务器判断。
- 错误页判定要求容器带「信息」标题（`.ally_contents` 在设置等正常页面也会出现）；如果私服把正常内容也放进同样结构，仍可能误报 `GameError`。
- 同一账号并发写操作没有分布式锁；SDK 只保证单个客户端实例内的请求串行。

## 10. 测试与扩展

测试策略：

- `tests/fixtures/` 保存真实页面（脱敏后的 HTML），离线单元测试解析器。
- `tests/test_client.py` 用假 `_request` 验证登录、舰队三步字段和会话文件逻辑。
- `tests/test_live.py` 只在提供 `NEWSTAR_*` 环境变量时运行，且只读；写操作靠 `scripts/live_smoke.py` 手工验证。
- 解析器改动必须先跑离线测试，再跑一次真实实例只读测试。

新增页面支持的步骤见 [使用说明](usage.md#8-扩展新页面)。
