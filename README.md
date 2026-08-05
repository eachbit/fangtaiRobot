# fangtaiRobot

方太人工智能专项赛：个性化膳食规划 Agent。

## Project Goal

构建一个“健康约束可验证”的个性化膳食规划 Agent。系统采用官方菜谱库检索、用户健康档案约束、规则校验和自然语言解释生成，避免直接让大模型幻觉生成不存在的菜品。

## Planned Deliverables

- API 服务，用于官方测评调用。
- 网页演示界面，用于答辩展示。
- 官方菜谱库检索。
- 用户健康档案约束读取。
- 过敏、口味、健康需求校验。
- 多轮对话状态管理。
- 约束评分卡。
- 部署文档、技术方案文档、演示材料。

## Data

官方数据文件不提交到 GitHub。请按 `data/README.md` 放置本地数据。

## Run Locally

本项目第一版只依赖 Python 标准库。

```powershell
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

返回结果会包含 `session_id`、`menu_version`、`history`、`changes`、`nutrition`、`score_card`、`warnings` 和 `answer`。

多轮继续对话：

```json
{
  "user_id": 3,
  "session_id": "上一次响应返回的 session_id",
  "messages": [
    "我不吃虾，其他菜尽量别动。"
  ]
}
```

不带 `session_id` 时，也可以提交完整历史。系统会按轮次重建菜单：

```json
{
  "messages": [
    "4个人吃午餐，推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}
```

恢复历史菜单版本：

```json
{
  "session_id": "上一次响应返回的 session_id",
  "messages": [],
  "rollback_to": 1
}
```

也可以直接输入“撤销刚才修改”或“回到第1版”。回滚不会删除历史，而是创建一个新的当前版本。查看历史版本：

```http
GET /api/sessions/{session_id}/history
```

## Test

运行核心逻辑自测：

```powershell
python tests/test_agent.py
```

## Docker

本项目提供最小离线 Docker 镜像配置，镜像内只运行本地 Python 服务，不依赖外部大模型或联网 API。

构建镜像前，请确认本机 `data/` 目录已经放入官方菜谱、用户健康档案和对话用例文件。它们不会提交到 GitHub，但会在本地构建 Docker 镜像时被复制进镜像。

```powershell
docker build -t fangtai-robot:latest .
```

本地运行：

```powershell
docker run --rm -p 8000:8000 fangtai-robot:latest
```

健康检查：

```powershell
curl http://127.0.0.1:8000/api/health
```

如果组委会要求提交镜像文件，可以导出为 tar：

```powershell
docker save fangtai-robot:latest -o fangtai-robot.tar
```

## Repository

Remote target:

```text
https://github.com/eachbit/fangtaiRobot.git
```
