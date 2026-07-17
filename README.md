# fangtaiRobot

方太人工智能专项赛：个性化膳食规划 Agent。

## Project Goal

构建一个“健康约束可验证”的个性化膳食规划 Agent。系统采用官方菜谱库检索、用户健康档案约束、规则校验和自然语言解释生成，避免直接让大模型幻觉生成不存在的菜品。

## Implemented Capabilities

- API 服务，用于官方测评调用。
- 网页演示界面，用于答辩展示。
- 官方菜谱库检索。
- 用户健康档案约束读取。
- 过敏、口味、健康需求校验。
- 对话健康特征识别与多轮会话状态管理。
- 完整历史重放和 `session_id` 双模式最小菜单修改。
- 离线营养估算：每道菜、整桌、人均及健康目标分项评分。
- 营养来源、重量来源、缺失食材和可信度说明。
- 固定评委场景、随机组合压力测试和覆盖率报告。
- 约束评分卡。
- 部署文档、技术方案文档、演示材料。

精确表达“荤素一比二”“两荤四素”或“再加两道素菜”时，系统会把整桌结构作为可验证约束执行；模糊搭配、多人冲突和高风险健康表达会在响应中标记需要进一步确认。

## Data

官方数据文件不提交到 GitHub。请按 `data/README.md` 放置本地数据。

## Run Locally

本项目第一版只依赖 Python 标准库。

```powershell
python server.py
```

可通过环境变量指定地址和端口：

```powershell
$env:HOST="0.0.0.0"
$env:PORT="8010"
python server.py
```

打开：

```text
http://127.0.0.1:8000
```

健康检查：

```text
http://127.0.0.1:8000/api/health
```

推荐接口：

```http
POST /api/recommend
Content-Type: application/json
```

请求示例：

```json
{
  "user_id": 3,
  "messages": [
    "中午这顿饭你帮我安排一下。",
    "两个人吃，最近在减脂。"
  ]
}
```

继续同一会话：

```json
{
  "session_id": "首轮响应返回的会话 ID",
  "menu_version": 1,
  "message": "不要虾，其他菜保留"
}
```

接口详情见 [docs/api.md](docs/api.md)。

## Nutrition Accuracy

营养结果是基于菜谱食材用量和离线食物成分数据的工程估算，不是实验室检测或医疗诊断。当前 2000 道菜的实测食材营养条目匹配率为 56.88%，重量可解析率为 88.24%；未匹配食材和默认用量会降低可信度并在响应中列出。基础数据来源和派生规则见 `data/nutrition/README.md`。

## Test

运行核心逻辑自测：

```powershell
python tests/test_agent.py
```

运行全套单元与评委压力测试：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tests/audit_recommendations.py
python tests/nutrition_coverage.py
```

### 持续评测

确定性评测覆盖健康档案、过敏/忌口、菜谱真实性、荤素结构、烹饪方式、营养计算、多轮上下文和最小修改。自主 cycle 会按轮次递增 seed、归档每轮报告并把重复的 blocking 失败合并到 issue registry：

```powershell
# quick：每轮 120 个场景，适合修改后的快速检查
python scripts/run_autonomous_cycle.py --mode quick --rounds 2 --seed 20260716 --cycle-id quick-20260716

# daily：每轮 2000 个场景，适合每日回归
python scripts/run_autonomous_cycle.py --mode daily --rounds 1 --seed 20260716 --cycle-id daily-20260716

# deep：每轮 10000 个场景，适合发布前的公开语料深度回归
python scripts/run_autonomous_cycle.py --mode deep --rounds 1 --seed 20260716 --cycle-id deep-20260716
```

`--rounds N` 的第 1 轮使用 `--seed`，之后每轮 seed 加 1。中断或失败后，使用完全相同的 `--mode`、`--rounds`、`--seed` 和 `--cycle-id` 重跑同一命令即可 resume；已完成轮次会被跳过，未完成轮次继续执行。已完成的 cycle 再次运行会直接复用原状态。相同 cycle ID 不得改换 mode、轮数或基础 seed，否则命令返回 2；新的修复验证应使用新的 cycle ID。

所有持久化评测状态都固定在仓库内已忽略的 `artifacts/evaluation/`，不使用额外的 root 参数：

```text
artifacts/evaluation/
├── cycles/<cycle-id>/
│   ├── cycle.json                 # 可恢复的逐轮状态
│   ├── summary.json               # 机器可读汇总
│   ├── summary.md                 # 人工阅读汇总
│   └── rounds/0001-<seed>/        # 每轮报告、覆盖率和失败证据
├── issues/
│   ├── index.json                 # issue ID、状态和文件路径索引
│   ├── open/
│   ├── verifying/
│   ├── resolved/
│   └── observations/
└── candidates/regressions/        # 导出的待人工审核回归候选
```

自主 cycle 的退出码含义如下：

- `0`：所有轮次完成，且没有 blocking issue。
- `1`：所有轮次完成，但有 blocking issue；失败证据和 issue 已正常归档，可进入人工处理流程。
- `2`：参数、状态、I/O 或轮次执行异常，或者 cycle 未完整完成；必须调查，不能按普通评测失败放行。

`blocking` 表示必须修复的失败；`known_gap` 只能登记精确的“场景 ID + 违反代码”，且必须有阶段负责人和到期阶段。当前 Phase 1 known gap 清单为空，已修复项仍由退役回归测试持续检查。

#### Issue 处理闭环

从 `artifacts/evaluation/issues/index.json` 或 cycle 汇总取得 `<issue-id>`。开始修复时把 issue 从 `open` 移到 `verifying`；若验证不通过可退回 `open`。修复后必须运行一个没有再次出现该 issue 的、完整完成的 `daily` 或 `deep` cycle，才能用其 cycle ID 标记为 `resolved`：

```powershell
python scripts/manage_evaluation_issue.py <issue-id> --status verifying
python scripts/manage_evaluation_issue.py <issue-id> --status open
python scripts/manage_evaluation_issue.py <issue-id> --status resolved --cycle-id <daily-or-deep-cycle-id>
```

公开语料 issue 可以先导出最小化回归候选：

```powershell
python scripts/manage_evaluation_issue.py <issue-id> --export-regression
```

该命令只写入 `artifacts/evaluation/candidates/regressions/<issue-id>.json`。候选必须由人工核对消息、期望、健康约束和稳定性，并实际运行回归确认；审核通过后才可手工移入 `tests/corpus/regressions/` 并提交。禁止把未审核候选直接写入永久回归语料。

私有 holdout 必须放在仓库和 `artifacts/evaluation/` 之外的受限目录，不提交、不导出回归候选，也不向修复流程暴露明文。自主 cycle CLI 不接收 holdout 参数；私有盲测使用独立的确定性 deep 命令，输出仍写入忽略目录，并只保留脱敏聚合与哈希证据：

```powershell
python scripts/run_evaluation.py --mode deep --seed 20260716 --include-holdout --holdout-dir <仓库外私有目录> --output artifacts/evaluation/holdout-20260716
```

这套流程用于评测、发现回归和改进确定性规则，不是模型参数训练。自主评测工具与可选外部 Agent 仅属于开发环境；正式 API 和 Docker 镜像不依赖该工具，也不调用外部模型，断网且没有任何 Agent 配置时仍可完整运行。

### 开发与提交边界

- Phase 1：确定性评测、健康 Persona、覆盖率报告和开发期 Agent 候选契约。
- Phase 2：精确荤素/烹饪结构、主动澄清、多轮结构合并及最小菜单修改。
- Phase 3：后续扩展每日多 Agent 探索、失败最小化和候选审核闭环。

外部 Agent 只在开发期生成测试候选或软评审，不能修改硬约束标准答案、known gaps 或生产推荐结果。正式 API 与 Docker 镜像不调用外部模型，断网且没有任何 Agent 配置时仍可完整运行。

## Repository

Remote target:

```text
https://github.com/eachbit/fangtaiRobot.git
```
