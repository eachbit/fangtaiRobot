# 方太个性化膳食规划 Agent API 接口文档

版本：2026-08-22  
协议：HTTP/1.1  
数据格式：JSON  
默认端口：8000

## 一、服务地址

公网服务：

```text
http://47.116.110.131:8000
```

本地服务：

```text
http://127.0.0.1:8000
```

## 二、接口约定

- 请求体和响应体均使用 UTF-8 编码；
- 请求体格式为 `application/json`；
- 推荐结果中的菜品名称均来自官方菜谱库；
- 过敏和明确忌口属于硬约束；
- 营养数值由本地计算模块生成；
- 外部模型仅用于可选的结构化辅助和营养评审；
- 外部模型超时或不可用时，服务自动使用本地规则继续处理。

## 三、健康检查

### 请求

```http
GET /api/health
```

### 示例

```bash
curl.exe http://47.116.110.131:8000/api/health
```

### 响应

```json
{
  "status": "ok",
  "recipe_count": 2000,
  "user_count": 50
}
```

## 四、膳食推荐

### 请求

```http
POST /api/recommend
Content-Type: application/json
```

### 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | integer | 否 | 用户健康档案编号 |
| `messages` | string[] | 是 | 按时间顺序排列的对话消息 |
| `message` | string | 否 | 单条消息快捷字段 |
| `session_id` | string | 否 | 已有会话编号 |
| `rollback_to` | integer | 否 | 要恢复的菜单版本号 |

### 请求示例

```json
{
  "messages": [
    "4个人吃午餐，先推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}
```

### 响应字段

| 字段 | 说明 |
| --- | --- |
| `menu` | 推荐菜单及菜谱信息 |
| `constraints` | 结构化用户约束 |
| `nutrition` | 整桌和人均营养估算 |
| `nutrition_review` | 营养风险提示和建议 |
| `changes` | 菜单版本变化和替换信息 |
| `score_card` | 推荐结果评分信息 |
| `warnings` | 风险、冲突和数据完整性提示 |
| `answer` | 中文自然语言说明 |
| `session_id` | 当前会话编号 |
| `menu_version` | 当前菜单版本号 |
| `history` | 菜单历史版本摘要 |

### 重点验收结果

对于上述请求，系统应满足：

- 返回 4 道菜；
- 识别用餐人数为 4；
- 识别菜品数量为 4；
- 将“不吃鸡蛋”识别为忌口；
- 不将“不吃鸡蛋”识别为过敏；
- 推荐菜单不命中鸡蛋相关食材；
- 菜单修改模式为 `minimal_revision`；
- 尽量保留上一轮未受影响的菜品；
- 营养结果按 4 人计算。

## 五、多轮会话

### 请求

```json
{
  "user_id": 3,
  "session_id": "上一轮返回的 session_id",
  "messages": [
    "我不吃虾，其他菜尽量别动。"
  ]
}
```

系统继承已有菜单和约束，仅替换不再满足条件的菜品。

## 六、完整历史重放

```json
{
  "messages": [
    "4个人吃午餐，推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}
```

系统按消息顺序重建菜单版本。

## 七、历史查询和回滚

### 查询历史

```http
GET /api/sessions/{session_id}/history
```

### 指定版本回滚

```json
{
  "session_id": "上一轮返回的 session_id",
  "messages": [],
  "rollback_to": 1
}
```

支持的自然语言操作：

- `撤销刚才修改`
- `恢复上一版`
- `回到第1版`

回滚会创建新的当前版本，不删除原有历史，也不能绕过过敏和明确忌口校验。

## 八、审计接口

```http
GET  /api/users
GET  /api/cases
GET  /api/audit/jobs
POST /api/audit/jobs
GET  /api/audit/jobs/{job_id}
POST /api/audit/jobs/{job_id}/cancel
```

审计接口用于批量测试和网页审计展示。

## 九、错误码

| HTTP 状态 | 错误信息 | 说明 |
| --- | --- | --- |
| `400` | `messages must be a string list` | 消息字段格式错误 |
| `400` | `rollback_version_not_found` | 菜单版本不存在 |
| `404` | `session_not_found` | 会话不存在 |
| `404` | `audit_job_not_found` | 审计任务不存在 |
| `404` | `not_found` | 请求路径不存在 |
| `500` | `internal_error` | 服务内部异常 |

## 十、模型服务说明

系统支持外部模型作为可选增强能力，由服务部署方在服务器内部完成配置。外部模型不参与菜品生成、硬约束裁决和营养数值计算；未配置或暂时不可用时，系统仍可使用本地规则完成推荐服务。
