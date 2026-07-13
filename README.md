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

## Repository

Remote target:

```text
https://github.com/eachbit/fangtaiRobot.git
```
