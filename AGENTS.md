# AGENTS.md

给在本仓库里工作的 AI / 协作者的操作手册。它只写**约束、入口和陷阱**；具体实现
看源码，接口用法看 `docs/usage.md`。

## 项目是什么

New-Star（2Moons 系）页游的 Python 自动化：`newstar_sdk` 是只读/写操作的客户端，
`autoplay` 是确定性策略层（阶段识别、规划、执行）。当前目标是让一个账号持续发展，
所有策略都是代码，AI 不参与运行时决策。

运行实例是魔改服「战争宇宙：猛犸」（New-Star 2.8.1，自定义主题），与上游模板有差异。

## 先读什么

1. `README.md` — 定位、安装、命令入口。
2. `docs/architecture.md` — 分层、数据来源、错误模型、已知脆弱点。
3. `docs/usage.md` — 每个 API 怎么用。
4. `docs/decisions.md` — 为什么这样选（改架构前必读）。
5. `docs/roadmap.md` — 做到哪了、下一步是什么、验收标准。

代码入口：`newstar_sdk/client.py`（HTTP + 高层动作）、`newstar_sdk/parsers.py`
（纯解析）、`autoplay/planner.py`（计划）、`autoplay/executor.py`（执行）、
`autoplay/expedition.py`（探险只读报告）。

## 环境与命令

```bash
uv pip install -e ".[dev]"          # 或 .venv/bin/pip install -e ".[dev]"

.venv/bin/ruff check .              # 提交前必须全绿
.venv/bin/python -m pytest -q       # 离线测试，必须全过

# 只读 live 测试（需要环境变量；没有就自动跳过）
NEWSTAR_BASE_URL=... NEWSTAR_USERNAME=... NEWSTAR_PASSWORD=... \
NEWSTAR_UNIVERSE=... NEWSTAR_VERIFY_SSL=0 \
.venv/bin/python -m pytest -q -m live
```

脚本都从仓库根目录运行，凭据按命令行 > 环境变量 > `.env` > `passwd.txt` 解析：

```bash
.venv/bin/python scripts/page_census.py --all --discover   # 抓页面样本
.venv/bin/python scripts/collect_state.py                  # 一次快照 + 事件
.venv/bin/python scripts/state_report.py --hours 24        # 趋势报表
.venv/bin/python scripts/plan.py                          # 只读计划
.venv/bin/python scripts/expedition.py --no-verify-ssl     # 探险只读报告
.venv/bin/python scripts/run_once.py                      # 影子模式（不执行）
.venv/bin/python scripts/tui.py                           # 只读看板（--once 打印一帧）
```

本实例证书过期，脚本通常要加 `--no-verify-ssl`。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `newstar_sdk/` | 稳定客户端：HTTP、解析、数据模型、异常、CLI。不写策略。 |
| `autoplay/` | 易变策略：阶段、经济计算、计划、执行。可以频繁改。 |
| `scripts/` | 面向人的命令行入口，薄封装，不放核心逻辑。 |
| `tests/` | 离线样本测试；`fixtures/` 是脱敏后的真实页面。 |
| `docs/` | 结构、用法、决策、路线图。 |
| `state/` `live_pages/` `newstar/` | 本地数据/上游源码，已忽略，**不要提交**。 |

## 硬性规则

1. **不提交敏感或本地数据**：`passwd.txt`、`.env`、`*.session.json`、`state/`、
   `live_pages/`、`newstar/`、`guide/*.docx` 都在 `.gitignore` 里。新增样本必须脱敏
   （用户名、邮箱、坐标）。
2. **策略进 `autoplay`，不进 SDK**。SDK 的公开契约要稳；策略可以推翻重写。
3. **解析器是纯函数**：`parsers.py` 只做 HTML → dataclass，不发请求。
   `client.py` 只做请求 + 编排 + 结果验证，不写选择器细节。
4. **写操作必须验证**：以页面证据判断成功（队列出现目标项或等级变化），
   不能把 HTTP 200 当成功；失败抛 `ActionRejected`，不要静默。
5. **执行默认 dry-run**：`execute_plan` 只有显式 `dry_run=False` + allow-list 才动手；
   一次一个动作；队列忙就跳过对应类型；被拒不要自动重试；预检查拒绝
   （`ActionRejected.before_post`）可让本轮换下一条候选。保留 `state/STOP` 急停。
6. **单写者**：同一账号不要并发写。SDK 只保证单实例内串行；守护进程必须单进程。
7. **改代码同步文档**：新增能力更新 `docs/usage.md`；改变取舍更新 `docs/decisions.md`；
   推进任务更新 `docs/roadmap.md`。文档只描述当前状态，历史交给 git。

## 提交与推送

- **可以随时 `git commit`，不需要先询问**；**除非用户明确要求，否则不要 `git push`**。
- 分支 `main`，远端 `origin`（GitHub，SSH）。
- 提交信息用中文 Conventional Commits：`feat(sdk): ...`、`fix: ...`、`test: ...`、
  `docs: ...`；一个提交只做一件事，标题祈使句、不加句号。
- 提交前跑 `ruff` + `pytest`；涉及解析器再加一次只读 live 测试。
- 本机系统 ssh 配置有权限问题，仓库已设 `core.sshCommand`；换机器需要重新配置。

## 本实例的已知坑

- **消息列表是 AJAX**：`page=messages` 只有计数，正文在
  `mode=view&messcat=..&site=..&ajax=1`。
- **错误页判定**：只认带「信息」标题的容器；`.ally_contents` 在正常页面也会出现，
  不要退回旧逻辑。
- **仓容字段不可信**：概览里的 capacity 与资源量严重不符（资源远超容量但产能仍在跑），
  不要把 capacity 当硬约束，语义待核实。
- **部分模块禁用**：`market`、`fair` 返回「模块已禁用」，`information` 固定 503。
- **证书过期**：`verify_ssl=False`，脚本加 `--no-verify-ssl`。
- **风格**：页面是 CSS 栅格（`rexx1/rexx2`），不是 `<table>`；定位靠 class 与位置。
- **研究进行中不能升级科研实验室**：会被 `ActionRejected` 拒绝（实测），执行器已
  在 `research_queue` 非空时跳过该动作。
- **幽能概览不是净可用**：概览的 `current_energy` / `energy.available` 是毛产能；
  研究/建筑页的 `costOverflow` 才是游戏实际检查的净缺口，判断可负担性时以它为准。

## 扩展一个新页面

1. `scripts/page_census.py --only <page>` 抓原始 HTML，确认结构。
2. 在 `tests/fixtures/` 放脱敏样本，先写失败的解析测试。
3. `parsers.py` 加纯解析函数，`models.py` 加数据结构。
4. `client.py` 加高层方法（必要时验证），`__init__.py` 导出。
5. 跑 `ruff` + `pytest`；再对真实实例跑一次只读 live 测试。
6. 更新 `docs/usage.md`（用法）和 `docs/roadmap.md`（如果推进了任务）。

## 验收清单

- [ ] `ruff check .` 通过
- [ ] `pytest -q` 全过（live 可跳过）
- [ ] 新增/修改的解析器有离线 fixture 测试
- [ ] 写操作路径在真实实例上至少 dry-run 验证过
- [ ] 没有文件进入 `.gitignore` 覆盖的范围
- [ ] 相关文档已同步
