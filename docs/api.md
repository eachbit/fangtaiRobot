# 推荐 API

## `POST /api/recommend`

请求和响应均为 UTF-8 JSON。旧版完整消息调用保持兼容：

```json
{
  "user_id": 3,
  "messages": ["晚餐推荐4道菜", "最近在减脂"]
}
```

`messages` 表示完整用户消息历史。消息至少两轮时，服务会重放前缀菜单，并对最后一轮执行最小修改。

首轮响应新增：

- `session_id`：内存会话 ID；
- `menu_version`：菜单/约束快照版本，从 1 开始；
- `changes`：首次生成、最小修改或全部重做的审计记录；
- `nutrition`：整桌总量、人均值、目标区间和分项状态；
- `nutrition_score`：0 到 100 分、总体判断和宏量供能比；
- `confidence`：营养估算可信度和原因；
- `menu[].nutrition`：每道菜的食材级营养明细与来源。

## 增量会话

```json
{
  "user_id": 3,
  "session_id": "...",
  "menu_version": 1,
  "message": "不要虾，其他菜保留"
}
```

单数 `message` 表示本轮增量。服务器在会话锁内验证版本、合并历史、只替换冲突菜并将版本加一。若用户 ID 与会话不一致，则创建新会话，避免健康档案串用。

明确表达“全部换掉、重新推荐、换一桌”时返回 `changes.mode = "full_regeneration"`，并排除上一轮菜谱。局部表达“第二道换掉”只替换对应位置。

## 版本冲突

显式提交过期版本返回 HTTP 409：

```json
{
  "error": "menu_version_conflict",
  "menu_version": 2,
  "retryable": true
}
```

客户端应重新提交当前完整 `messages`。非法 `user_id`、`session_id`、`menu_version` 或消息结构返回 HTTP 400。

## 营养字段说明

营养素包括 `kcal`、`protein_g`、`fat_g`、`carbohydrate_g`、`fiber_g`、`sugar_g`、`sodium_mg`。食材明细包含：

- 原始和标准食材名称；
- 用量、单位和换算克数；
- `amount_source`：`explicit`、`estimated`、`default` 或 `unknown`；
- 营养来源和来源 ID；
- 该食材营养贡献。

使用派生数据、缺失条目、默认用量或自然单位换算时，结果不会获得最高可信等级。低可信结果的 `assessment` 不会标记为 `balanced`。

## 会话生命周期

会话仅保存在服务器内存，不写入磁盘，默认两小时过期并有容量上限。Docker 重启后会话清空；完整历史模式仍可重放前一轮菜单，不依赖进程持久化。
