# 自主评测循环与问题档案设计

## 1. 目标

在不训练或修改模型参数的前提下，为个性化膳食规划 Agent 增加可重复运行的开发期自主评测闭环。系统连续生成不同随机种子的健康场景，调用本地推荐接口，以确定性判分器发现问题，自动最小化失败输入，并把重复失败合并为可追踪的问题档案。

本阶段不自动修改生产代码、不自动登记 known gap、不自动合并分支。正式比赛 API 和 Docker 运行路径不依赖该系统。

## 2. 范围

Phase 3A 包含：

- 可配置轮数、模式和随机种子的自主评测命令；
- 运行中断后的安全续跑；
- 失败文件导入、稳定指纹和重复问题合并；
- `open`、`verifying`、`resolved` 三种问题状态；
- 原始输入、最小复现输入、违反代码、出现次数和版本证据；
- 将人工确认的问题导出为永久回归候选；
- JSON 和 Markdown 汇总报告。

Phase 3A 不包含：

- 网页启动、暂停和实时进度界面；
- 外部 Agent 自动修改源代码；
- 自动提交、推送或合并 Git 分支；
- 自动修改硬约束期望或 known gaps；
- 将私有盲测内容提供给修复 Agent。

网页控制台和受控修复 Agent 属于后续 Phase 3B。

## 3. 存储位置

继续使用现有项目运行目录，不迁移历史文件：

```text
artifacts/evaluation/
├─ <现有评测批次>/
├─ cycles/
│  └─ <cycle-id>/
│     ├─ cycle.json
│     ├─ summary.json
│     ├─ summary.md
│     └─ rounds/
│        └─ <round-index>-<seed>/
├─ issues/
│  ├─ open/
│  ├─ verifying/
│  ├─ resolved/
│  └─ index.json
└─ candidates/
```

该目录由 `.gitignore` 排除，不提交 GitHub。永久回归场景仍存放在 `tests/corpus/regressions/` 并进入版本控制。

私有盲测继续保存在仓库外：

```text
D:\Codex\Lesson\.evaluation-private\fangtaiRobot\holdout\
```

## 4. 自主循环

新增入口：

```powershell
python scripts/run_autonomous_cycle.py --mode quick --rounds 10 --seed 20260715
```

每轮使用 `base_seed + round_index`，调用现有 `EvaluationRunner`，输出到该循环的 `rounds` 子目录。每轮结束后立即原子写入 `cycle.json`，记录：

- cycle ID、模式、起始种子和目标轮数；
- 当前完成轮数；
- 每轮状态、输出目录、通过数、失败数和耗时；
- 创建时间、更新时间和当前 Git commit；
- `running`、`completed`、`stopped` 或 `failed` 状态。

同一 cycle ID 再次运行时读取 `cycle.json`，跳过已完成轮次。单轮异常只记录该轮失败，不损坏前面结果；是否继续由 `--continue-on-error` 控制。

## 5. 问题档案

每个确定性失败生成或更新一个问题 JSON。问题指纹由以下稳定字段计算：

- 违反代码；
- 自动最小化后的消息；
- 健康桶和场景意图；
- 相关硬约束期望。

不使用响应时间、commit SHA、随机种子或自然语言错误说明参与指纹，避免同一根因被拆成多个问题。

问题文件包含：

```json
{
  "schema_version": 1,
  "issue_id": "issue-<fingerprint>",
  "fingerprint": "...",
  "status": "open",
  "severity": "blocking",
  "violation_code": "constraint.forbidden_term",
  "scenario_ids": ["..."],
  "health_buckets": ["..."],
  "intents": ["..."],
  "original_messages": ["..."],
  "minimized_messages": ["..."],
  "expected": {},
  "latest_evidence": {},
  "first_seen_at": "...",
  "last_seen_at": "...",
  "first_seen_commit": "...",
  "last_seen_commit": "...",
  "occurrences": 1,
  "seeds": [20260715]
}
```

数组字段去重并保持稳定排序；`seeds` 和 `scenario_ids` 最多各保留 256 项，超出后保留排序后的最新 256 项，避免长期运行导致单文件无限增长。问题更新采用临时文件加原子替换。

## 6. 状态流转

允许的状态流转：

```text
open -> verifying -> resolved
resolved -> open
verifying -> open
```

- `open`：仍可复现，尚未进入验证；
- `verifying`：已有修复，等待 quick、daily 或 deep 验证；
- `resolved`：至少一次 `daily`（2000 场景）或 `deep`（10000 场景）循环未再出现该指纹；
- `resolved -> open`：后续自主循环再次发现同一指纹时自动重开。

`open -> verifying` 由人工在修复提交后触发；`verifying -> resolved` 必须提供对应 cycle ID，管理命令校验该循环模式、完成状态和问题指纹未复现后才允许流转。`verifying` 阶段再次复现会自动回到 `open`。

状态修改必须通过结构化命令，不允许直接移动文件后遗漏索引：

```powershell
python scripts/manage_evaluation_issue.py <issue-id> --status verifying
```

## 7. 永久回归候选

问题档案不能直接成为权威回归标准。人工确认后，管理命令可将最小复现输入和已有结构化期望导出到：

```text
artifacts/evaluation/candidates/regressions/<issue-id>.json
```

候选通过现有严格 `Scenario` schema、语义完整性测试和人工审查后，才允许移动到 `tests/corpus/regressions/`。导出过程不能根据实际错误响应反向修改期望。

## 8. 安全边界

- 只接受确定性判分器的 blocking 违反作为问题主记录；Agent 意见只保存为软评审附件。
- allergy、forbidden term、authenticity、schema、nutrition arithmetic 和 session continuity 不能降级为 known gap。
- 路径必须保持在仓库的 `artifacts/evaluation/` 内，拒绝 `..`、绝对路径注入、链接和 reparse point 逃逸。
- 私有 holdout 报告只保存哈希和聚合违反代码，不保存原始消息。
- 并发运行使用 cycle 级锁和问题索引锁，避免覆盖或损坏 JSON。
- 任何自动修复都不得直接操作 `main`，不得自动合并。

## 9. 测试与验收

单元测试必须覆盖：

- 固定种子和轮次目录确定性；
- 中断续跑和已完成轮次跳过；
- 相同问题合并、不同问题不误合并；
- `resolved` 问题复现后自动重开；
- 原子写入失败不破坏旧索引；
- 非法状态流转和路径逃逸被拒绝；
- 私有 holdout 不泄露消息；
- 回归候选不能绕过 Scenario schema；
- 单轮失败时 `--continue-on-error` 行为正确。

完成后运行：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/run_autonomous_cycle.py --mode quick --rounds 2 --seed 20260715
python tests/audit_recommendations.py
git diff --check
```

验收标准：两轮循环可完成或安全续跑，问题索引与单轮报告一致，现有 120 场景 quick 和全部历史回归无新增 blocking failure。
