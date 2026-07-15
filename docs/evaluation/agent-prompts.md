# Multi-agent Candidate Contracts

外部 Agent 只生成开发期候选，不生成权威标签。所有输出必须是单个 JSON
对象，不得包含 Markdown 围栏、解释文字或 schema 之外的顶层字段。候选由
`validate_candidate` 校验；未人工审核的候选只能通过
`save_unreviewed_candidate` 写入被 git 忽略的
`artifacts/evaluation/candidates/`。

## Shared Candidate Schema

允许的顶层字段只有：

- `candidate_id`：必填，安全 ASCII 标识符，最长 128 字符。
- `health_bucket`：可选，只能是 `healthy`、`single_condition`、
  `multi_condition`、`special_group` 或 `high_risk`。
- `messages`：必填 JSON 数组，最多 12 轮，每轮为不超过 500 字符的字符串。
- `structured_ground_truth`：必填 JSON 对象，是唯一的结构化标准答案来源。
- `agent_review`：可选 JSON 对象。无论字段名或字段值是什么，校验后都只存入
  `soft_review`，不能覆盖 `structured_ground_truth`。

所有嵌套值也必须是严格 JSON 值；tuple、非字符串对象键、NaN 和无穷数均非法。
Agent 不得直接更改 `app/`、硬约束期望、known gaps 或合并状态。Agent 的输出
只能进入候选审核流程，不能成为提交、发布或合并的授权。

## Customer Agent

根据调用方提供的结构化标准答案生成自然的客户表达。不得从话术反向修改健康
事实，不得补充未提供的疾病、过敏或目标。只输出以下形状的 JSON：

```json
{
  "candidate_id": "customer-negative-health-1",
  "health_bucket": "healthy",
  "messages": ["我没有高血压，想要四道家常菜。"],
  "structured_ground_truth": {
    "special_groups": [],
    "dish_count": 4
  },
  "agent_review": {
    "naturalness": 4,
    "notes": "候选口语表达"
  }
}
```

## Advanced-scenario Agent

组合调用方已给定的健康事实与高级多轮操作，例如追加约束、撤回偏好、比例修改
或澄清。必须保持同一客户的健康事实不变，且只输出以下形状的 JSON：

```json
{
  "candidate_id": "advanced-ratio-revision-1",
  "health_bucket": "single_condition",
  "messages": [
    "我有高血压，晚餐推荐六道菜。",
    "荤素改成二比四，其他要求不变。"
  ],
  "structured_ground_truth": {
    "special_groups": ["高血压"],
    "dish_count": 6,
    "meat_count": 2,
    "vegetable_count": 4,
    "preserve_unaffected": true
  },
  "agent_review": {
    "scenario_difficulty": 4,
    "notes": "多轮比例修改候选"
  }
}
```

## Judge Agent

只评价自然度、解释清晰度等软质量。输入中的 `messages`、`health_bucket` 与
`structured_ground_truth` 必须原样返回；不得把评分、推断或意见写入标准答案。
只输出以下形状的 JSON：

```json
{
  "candidate_id": "judge-negative-health-1",
  "health_bucket": "healthy",
  "messages": ["我没有高血压，推荐四道菜。"],
  "structured_ground_truth": {
    "special_groups": [],
    "dish_count": 4
  },
  "agent_review": {
    "naturalness": 4,
    "clarity": 5,
    "notes": "否定健康表达清楚"
  }
}
```

即使 `agent_review` 中出现 `severity`、`special_groups`、`known_gap` 或
`structured_ground_truth` 等名称，它们仍全部属于 `soft_review`，没有硬判分
或状态变更权限。

## Red-team Agent

在已给定 ground truth 内寻找否定表达、歧义、错别字、约束冲突和多轮撤回等
攻击性话术。不得编造医学诊断或改变预期答案。只输出以下形状的 JSON：

```json
{
  "candidate_id": "red-team-negation-1",
  "health_bucket": "healthy",
  "messages": [
    "不是说我血压高，我没有高血压；还是给我四道菜。"
  ],
  "structured_ground_truth": {
    "special_groups": [],
    "dish_count": 4
  },
  "agent_review": {
    "attack_axis": "negative-health-false-positive",
    "notes": "测试关键词误报"
  }
}
```

## Root-cause Agent

Root-cause Agent 只能接收已保存证据，包括固定随机种子、原始及最小化消息、约束
快照、菜单 ID、违反代码和耗时。它只能解释可复现根因，并提议失败回归测试；
该测试必须先失败，同时只能建议最小修复范围。

Root-cause Agent：

- 不能执行合并，也不能批准或改变合并状态。
- 不能修改 holdout 期望或任何硬约束期望。
- 不能添加生产网络调用，包括在 `app/` 推荐路径中调用外部 Agent。
- 不能把 blocking failure 标成 known gap，也不能直接编辑 known gaps。
- 不能凭未保存的聊天摘要、主观判断或新生成样例宣称根因成立。
