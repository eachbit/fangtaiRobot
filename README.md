# fangtaiRobot

方太人工智能专项赛：个性化膳食规划 Agent。

## Project Goal

构建一个“健康约束可验证”的个性化膳食规划 Agent。系统采用官方菜谱库检索、用户健康档案约束、规则校验和本地规则解释生成，避免直接让大模型幻觉生成不存在的菜品；最终运行链路不依赖外部模型或外网 API。

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

返回结果会包含 `session_id`、`menu_version`、`history`、`changes`、`nutrition`、`nutrition_review`、`score_card`、`warnings` 和 `answer`。

关键响应字段：

- `menu`：推荐菜品列表，所有 `name` 均来自本地官方菜谱库。
- `constraints`：从健康档案和多轮消息中抽取的餐次、人数、菜数、过敏、忌口、口味、健康目标等结构化约束。
- `nutrition`：整桌和人均热量、蛋白质、脂肪、碳水、钠等离线估算结果，同时保留 `confidence`、`missing_ingredients` 和估算假设。该结果用于竞赛解释和排序，不作为医学诊断。
- `nutrition_review`：基于本地营养数值生成的结构化评审，包含 `summary`、`risk_flags`、`suggestions`、`confidence` 和 `llm_assist`。外部模型开启时只可增强评审话术，不会覆盖 `nutrition` 数值或新增菜单。
- `changes`：多轮修改状态，`mode` 可能为 `new_menu`、`minimal_revision` 或 `rollback`，并给出保留、替换和修改数量。
- `score_card`：菜谱真实性、过敏/忌口、健康、口味、场景、最小修改和营养状态的结构化评分。
- `warnings`：健康风险、约束冲突或营养估算提示。
- `answer`：面向用户的中文推荐说明，由本地规则生成。

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

辅助接口：

```http
GET /api/health
GET /api/users
GET /api/cases
POST /api/audit/jobs
GET /api/audit/jobs
GET /api/audit/jobs/{job_id}
POST /api/audit/jobs/{job_id}/cancel
```

`/api/audit/jobs` 只用于本地开发和演示控制台批测，不是推荐链路的外部依赖。

当前重点验收输入：

```json
{
  "messages": [
    "4个人吃午餐，先推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}
```

验收要点：返回 4 道官方菜谱，`people_count=4`、`requested_dish_count=4`，`avoid_ingredients` 包含鸡蛋相关约束，菜单食材和标签中不得命中鸡蛋，追加忌口时应进入 `minimal_revision` 并尽量只替换必要菜品。

## Test

运行完整本地验证：

```powershell
python tests/test_agent.py
python tests/test_scenario_agents.py
python tests/test_audit_jobs.py
python tests/test_server_api.py
python tests/web_ui_smoke.py
python tests/test_docker_contract.py
python tests/audit_recommendations.py
python -m compileall -q app server.py tests
git diff --check
```

这些测试覆盖官方菜谱真实性、过敏/忌口过滤、菜品数量、多人健康约束、营养估算、多轮菜单保留、上下文回放、版本回滚、网页演示和 Docker 文件契约。

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

## Public API Deployment

服务默认监听 `HOST` 和 `PORT` 环境变量，本地默认为 `127.0.0.1:8000`，Docker 内默认为 `0.0.0.0:8000`。

可选外部模型辅助：

```bash
FANGTAI_LLM_BASE_URL=https://api.example.com/
FANGTAI_LLM_API_KEY=sk-...
FANGTAI_LLM_MODEL=gpt-5.4-mini
FANGTAI_LLM_TIMEOUT=2.5
FANGTAI_LLM_NUTRITION_REVIEW=1
FANGTAI_LLM_NUTRITION_TIMEOUT=3
```

外部模型默认只用于补充自然语言约束抽取。设置 `FANGTAI_LLM_NUTRITION_REVIEW=1` 后，会额外辅助 `nutrition_review` 的营养评审话术和建议；可用 `FANGTAI_LLM_NUTRITION_TIMEOUT` 单独控制营养评审等待时间。官方菜谱真实性、过敏/忌口过滤、菜品数量、营养数值、多轮保留和回滚仍由本地规则校验。未配置密钥、请求超时或中转站不可用时，服务会自动回退到完全离线规则链路。

此前最后已知公网地址：

```text
http://47.116.110.131:8000/
```

交付前必须重新验证公网接口：

```powershell
curl.exe http://47.116.110.131:8000/api/health
curl.exe -X POST http://47.116.110.131:8000/api/recommend -H "Content-Type: application/json" -d "{\"messages\":[\"4个人吃午餐，先推荐4道菜。\",\"我不吃鸡蛋，其他菜尽量别动。\"]}"
```

公网部署建议使用 `systemd` 或等价后台服务管理，避免 SSH 断开后服务退出。不要将服务器密码、API Key 或数据文件提交到公开仓库。

## Repository

Remote target:

```text
https://github.com/eachbit/fangtaiRobot.git
```
