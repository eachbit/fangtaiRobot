# 方太个性化膳食规划 Agent 项目交接文档

更新时间：2026-08-21（已部署公网 API、可选外部模型辅助、营养评审与速度门控版本）

这份文档用于新开 Codex 线程时快速恢复项目上下文。新线程应先阅读本文，再检查当前仓库状态，不要重复实现已经完成的功能。

## 一、项目目标

本项目参加方太食谱赛道“个性化膳食规划 Agent”。核心目标是：

- 只从官方菜谱库中推荐真实存在的菜品；
- 从用户档案和自然语言对话中识别健康情况、过敏、忌口、口味、人数、餐次和菜品数量；
- 同时处理多人约束和健康目标；
- 支持多轮修改，并尽量保留用户已经确认的菜单；
- 提供可公开访问的 HTTP API 和网页演示；
- 最终能够在不依赖外部模型和外网的 Docker 环境中运行。

## 二、当前代码位置与仓库状态

本机项目目录：

```text
D:\Codex\Lesson\fangtaiRobot
```

远程仓库：

```text
https://github.com/eachbit/fangtaiRobot.git
```

当前已知 Git 状态：

- 分支：`codex/audit-docker-readiness`
- 相对远程状态：2026-08-21 通过 GitHub 网页提交了本文档更新，远端提交为 `8bec6cd docs: refresh project handoff status`；本机 CLI 因代理无法连接 GitHub，暂时保留同内容本地提交 `1044ea2 docs: refresh project handoff status`。继续开发前必须执行 `git status --short --branch`、`git fetch origin codex/audit-docker-readiness` 和 `git log --oneline -8` 确认，再将本地分支对齐到远端；不要覆盖用户已有改动。
- 最近关键提交：
  - `8bec6cd docs: refresh project handoff status`（GitHub 网页远端提交）
  - `1044ea2 docs: refresh project handoff status`（本机同内容提交，待 fetch 后对齐）
  - `4c90370 fix: enforce llm speed gates`
  - `b355a62 feat: add nutrition review timeout setting`
  - `f7c1aed fix: gate nutrition review llm calls`
  - `8eaa244 feat: add optional llm nutrition review`
  - `de06bbf fix: parse family counts and dish units`
  - `dc2e2b2 fix: default to cinlan available llm model`
  - `9a28f03 feat: add optional llm constraint assist`
  - `5b4dc0b fix: tighten public api acceptance readiness`

## 三、已经完成的功能

### 1. 离线推荐核心

- `app/data_loader.py` 读取本地官方菜谱、50份健康档案和对话用例。
- `app/retriever.py` 对官方菜谱进行约束过滤和排序。
- `app/health_rules.py` 执行过敏、忌口和健康风险校验。
- `app/planner.py` 负责菜单数量、荤素/主食/汤等类别搭配、最小修改和营养二次重排。
- 推荐结果始终来自菜谱库，不允许凭空生成菜名。

### 2. 对话约束识别

当前已覆盖或已加入测试的识别范围包括：

- 餐次：早餐、午餐、晚餐；
- 人数：数字和中文数字，例如“4个人”“四个人”；
- 家庭人数表达：例如“四大一小”“两个大人一个孩子”等；
- 菜品数量：例如“推荐4道菜”“安排6道”“整桌来五个菜”；
- 口味：清淡、辣、甜、咸、油等正向和否定表达；
- 健康目标：减脂、增肌、控糖、降压、补钙、补铁、养胃等；
- 过敏与忌口区分：例如“对鸡蛋过敏”和“我不吃鸡蛋”不是同一种约束；
- 常见食材别名扩展，例如虾、海蛎子、花甲等；
- 年龄、性别、孕期、老人、儿童、哺乳期和运动量等对话画像特征；
- 时间、难度、聚餐、便当和夏季清爽等场景要求；
- 多人同时提出的冲突约束，以及需要主动澄清的模糊场景。

已加入可选外部模型辅助：

- `app/llm_assist.py` 使用 OpenAI-compatible Chat Completions 接口补充自然语言约束抽取；
- 仅在本地规则存在明显不确定性时调用外部模型，例如“两个成年人加一个孩子”这类复杂人数表达；
- 本地已经清楚的低不确定性请求会跳过外部模型，`llm_assist.skipped=low_uncertainty`；
- 外部模型只能补充结构化约束，不能生成菜名，不能覆盖本地已识别的硬约束；
- “不吃/忌口/不喜欢”仍归入 `avoid_ingredients`，不能被模型升级成 `allergens`；
- 超时、HTTP 错误或未配置密钥时自动回退到完全离线规则。

### 3. 整桌营养计算和营养重排

`app/nutrition.py` 已提供离线营养估算：

- 整桌总热量、蛋白质、脂肪、碳水、膳食纤维、糖和钠；
- 按人数计算人均营养；
- 根据食材克数、毫升、个、只、勺等单位估算；
- 输出营养覆盖率、缺失食材、估算假设和置信度；
- 输出 `balance_level`，并在规划阶段用于换菜排序。

注意：这是竞赛解释和排序用的离线估算，不是医学诊断。当前数据存在食材营养字段不完整的情况，因此必须同时保留 `confidence` 和 `missing_ingredients`，不能把估算结果包装成临床精确值。

当前还新增了 `app/nutrition_review.py`：

- `nutrition` 仍是本地确定性营养数值来源，外部模型不能覆盖其中任何数值；
- `nutrition_review` 输出结构化营养评审，包含 `summary`、`risk_flags`、`suggestions`、`confidence`、`source` 和 `llm_assist`；
- 默认先生成本地营养评审；设置 `FANGTAI_LLM_NUTRITION_REVIEW=1` 后，才允许外部模型辅助润色营养评审；
- 营养评审有独立超时 `FANGTAI_LLM_NUTRITION_TIMEOUT`，当前公网建议值为 `3` 秒；
- 普通低风险菜单不调用外部营养评审模型，避免影响竞赛响应速度；
- 有健康目标或明显风险时才尝试外部评审，超时则保留本地评审。

### 4. 多轮会话、菜单保留和上下文回溯

当前已经实现：

- 带 `session_id` 的连续对话；
- 不带 `session_id` 但提交完整消息历史时按轮次重建；
- 每轮保存菜单版本；
- 用户追加忌口时尽量保留上一轮不受影响的菜；
- `changes.mode` 支持 `new_menu`、`minimal_revision`、`rollback`；
- `rollback_to` 恢复指定菜单版本；
- “撤销刚才修改”“恢复上一版”“回到第1版”等自然语言回溯；
- `GET /api/sessions/{session_id}/history` 查看版本摘要；
- 回滚不会删除旧历史，回滚后会创建新的当前版本；
- 回滚不能绕过过敏和明确忌口校验。

会话存储当前是进程内存实现，默认最多保存256个会话、每个会话最多32个版本，TTL为2小时。服务进程重启后会话消失，这是当前明确的非目标，不要误称为长期账号历史。

### 5. 自动评测和两个开发期 Agent

- `app/audit_runner.py`：按官方100分体系模拟基础推荐、复杂组合、多轮交互和性能效率。
- `app/audit_jobs.py`：通过 API 异步执行批测任务，查询进度和结果。
- `app/scenario_agents.py`：本地确定性 Customer Scenario Agent 生成不同健康情况、忌口、人数、多轮和多人冲突的测试场景。
- Candidate Review Agent 只做自然度和清晰度软评审，硬判定仍以结构化真值和本地规则为准。
- 这些 Agent 是开发期测试工具，不是最终推荐链路的外部依赖。

### 6. Docker 基础结构

仓库已经有：

- `Dockerfile`：Python 3.12 slim，复制 `app`、`public`、`data` 和 `server.py`；
- `.dockerignore`：忽略测试、文档、缓存和构建噪声，但不忽略 `data/*.csv`、`data/*.json`；
- 服务默认监听 `0.0.0.0:8000`；
- 当前运行时只使用 Python 标准库；
- 外部模型只通过环境变量作为可选增强，不是 Docker 或离线运行的必需依赖。

## 四、当前数据文件

以下文件位于本地 `data/`，已被 Git 忽略，不会进入 GitHub，但 Docker 构建时会复制进去：

```text
D:\Codex\Lesson\fangtaiRobot\data\recipes_sample_2000.csv
D:\Codex\Lesson\fangtaiRobot\data\50个用户健康档案（脱敏）.json
D:\Codex\Lesson\fangtaiRobot\data\对话用例.json
```

当前本地检查结果：

- 菜谱：2000道；
- 用户健康档案：50份；
- 对话用例：20组。

用户档案的唯一准入格式是桌面提供的详细版本，当前加载器会强制检查每条记录都包含 `身高_cm`、`体重_kg`、`BMI`、`体检指标`。旧版简化档案缺少这些字段时会直接报错，不允许静默回退。

本次已完成的档案切换：桌面文件 `C:\Users\32757\Desktop\50个用户健康档案（脱敏）.json` 已复制到项目 `data/` 目录，两个文件 SHA-256 一致。`app.models.UserProfile` 现在保留身高、体重、BMI 和体检指标，后续健康规则、营养规划和测试都必须以这份详细档案为准。

数据属于比赛材料，不能把真实或脱敏档案直接推送到公开 GitHub。新线程操作时要检查 `.gitignore`，不要为了方便执行 `git add -f data`。

## 五、接口和网页

### 健康检查

```http
GET /api/health
```

预期返回包含：

```json
{
  "status": "ok",
  "recipe_count": 2000,
  "user_count": 50
}
```

### 推荐接口

```http
POST /api/recommend
Content-Type: application/json
```

请求示例：

```json
{
  "user_id": 3,
  "messages": [
    "4个人吃午餐，先推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}
```

响应重点字段：

- `menu`：官方菜谱结果；
- `constraints`：抽取出的结构化约束；
- `nutrition`：整桌、人均、营养置信度和均衡等级；
- `nutrition_review`：本地营养数值基础上的结构化营养评审，可选外部模型辅助润色，但不能覆盖营养数值；
- `changes`：本轮保留和替换情况；
- `session_id`、`menu_version`、`history`：会话上下文；
- `score_card`：基础、健康、口味、场景、最小修改和营养状态；
- `warnings`：健康风险提示；
- `answer`：给用户看的自然语言结果。

### 继续多轮

```json
{
  "user_id": 3,
  "session_id": "上一轮响应中的session_id",
  "messages": [
    "我不吃虾，其他菜尽量别动。"
  ]
}
```

### 完整历史重放

```json
{
  "messages": [
    "4个人吃午餐，推荐4道菜",
    "我不吃鸡蛋，其他菜尽量别动"
  ]
}
```

### 回溯菜单版本

```json
{
  "session_id": "上一轮响应中的session_id",
  "messages": [],
  "rollback_to": 1
}
```

历史查询：

```http
GET /api/sessions/{session_id}/history
```

网页首页为 `/`，可用于手工输入对话并查看推荐、约束、营养和审计结果。网页不是最终评分接口，最终接口以 `/api/recommend` 为准。

可选外部模型环境变量：

```bash
FANGTAI_LLM_BASE_URL=...
FANGTAI_LLM_API_KEY（仅在服务器环境中配置，不写入仓库）
FANGTAI_LLM_MODEL=gpt-5.4-mini
FANGTAI_LLM_TIMEOUT=2.5
FANGTAI_LLM_NUTRITION_REVIEW=1
FANGTAI_LLM_NUTRITION_TIMEOUT=3
```

不要把真实 API Key、服务器密码或中转站凭据写入仓库、README、交接文档或提交历史。

## 六、公网部署的最后已知状态

当前已在阿里云 ECS 上以 `systemd` 常驻服务运行：

```text
公网地址：http://47.116.110.131:8000/
健康检查：http://47.116.110.131:8000/api/health
服务名：fangtai-robot.service
服务器目录：/opt/fangtaiRobot
当前服务器提交：4c90370
```

2026-08-21 已验证返回 `status=ok`、2000道菜谱和50份档案。新线程开始后仍必须重新检查：

```powershell
curl.exe http://47.116.110.131:8000/api/health
```

服务器当前由 systemd 管理，不再依赖 SSH 前台进程：

```bash
systemctl status fangtai-robot --no-pager
systemctl restart fangtai-robot
```

服务器环境文件为 `/etc/fangtai-robot.env`，权限应保持 `600`。不要在终端、日志、文档或最终回复中打印真实密钥内容。服务器无法访问 GitHub 时，可以等待网络恢复后重试；当前推荐方式仍是直接 `git pull --ff-only origin codex/audit-docker-readiness`，不要随意覆盖服务器工作区。

## 七、已经验证的测试结果

2026-08-21 在本机执行完整验证：

```powershell
python tests/test_llm_assist.py
python tests/test_nutrition_review.py
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

结果均通过。`git diff --check` 在 Windows 上可能提示 LF/CRLF 替换 warning；只要没有 trailing whitespace 或 patch error，就不是失败。

当前验证覆盖：

- 2000道官方菜谱、50份用户、20组对话用例；
- 约束抽取 LLM 辅助、跳过门控和失败回退；
- 营养评审、LLM 辅助、独立超时和失败回退；
- 官方菜谱真实性、过敏/忌口零违反、菜品数量；
- 多人健康约束、营养估算和营养评审；
- 多轮菜单保留、完整上下文回放、版本回滚；
- 审计任务 API、网页演示和 Docker 文件契约。

重点验收输入公网复测：

```json
{
  "messages": [
    "4个人吃午餐，先推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}
```

公网结果：

- `menu_count=4`；
- `people_count=4`；
- `requested_dish_count=4`；
- `avoid_ingredients` 包含 `蛋`、`鸡蛋`；
- `allergens` 为空，没有把“不吃鸡蛋”误判为过敏；
- `changes.mode=minimal_revision`；
- `change_count=1`；
- `nutrition.table.people_count=4`；
- `nutrition_review.source=local`，低风险样例跳过外部营养评审，`nutrition_review.llm_assist.skipped=low_risk`。

2026-08-21 公网速度批测，每类3次，结果均低于官方单轮优秀线8秒：

| 场景 | 最大耗时 | 说明 |
| --- | ---: | --- |
| 重点验收对话 | 0.56s | 本地低不确定性，跳过中转站 |
| 复杂人数表达 | 3.26s | 调用外部模型补充人数约束 |
| 高血压/减脂营养评审 | 4.28s | 本地链路保底，营养评审可选增强 |
| 四大一小离线解析 | 0.41s | 本地解析 |
| 普通清淡晚餐 | 3.69s | 跳过约束中转 |

注意：网络环境会抖动，不能承诺外部中转站永久稳定。因此当前代码通过门控和超时保证外部模型不是硬依赖；官方评测应优先依赖本地确定性链路。

## 八、下一步开发顺序

新线程应按下面顺序执行。当前 P0 和 P1 已基本完成，后续重点转向 P2 准确率提升和 P3 Docker 实测打包；不要重写推荐器，不要把外部模型变成最终必需依赖。

### P0：当前版本验收（已完成，后续每次修改仍要重复）

1. 执行 `git status --short --branch`，确认没有用户未提交改动；确认本地详细档案仍与桌面来源文件一致。
2. 运行完整测试集：

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

3. 复测复杂输入：人数、菜数、健康目标、多人冲突、过敏/忌口、多轮保留、回滚，以及50份详细健康档案。
4. 生成一份当前正式批测报告，记录通过率、失败样例、P95、平均响应时间和营养告警。

### P1：公网 API 交付（已完成，后续每次部署仍要复测）

1. 最新代码已通过 GitHub 同步到 ECS；
2. 服务已改为 `systemd` 后台常驻；
3. 已验证 `/api/health`、`/api/recommend`、`/api/sessions/{id}/history`；
4. 已验证公网端口和服务重启；
5. 后续仍需持续补齐 API 接口文档、技术方案文档和答辩材料；
6. 对外提供公网 API 前必须再次跑重点验收输入和速度批测。

### P2：做大范围准确率提升

重点不是堆简单测试，而是扩充有结构化真值的困难场景：

- 50种健康档案逐一覆盖；
- 高血压、糖尿病/控糖、高尿酸、减脂、增肌、孕期、哺乳期、儿童、老人、养胃等组合；
- 过敏和忌口同义词、否定表达、并列表达；
- 荤素比例、烹饪方式数量、冷热搭配、主食/汤/蔬菜类别；
- 多人互相冲突的健康和口味条件；
- “其他菜尽量别动”“只替换这一道”“全部重做”等最小修改和重置意图；
- 模糊需求主动澄清，而不是无条件猜测；
- 输入数量与实际返回数量不一致、无可行解、营养数据缺失等异常路径。

每修复一个问题，都要先补一个回归样例，再修改逻辑，最后重新跑全量批测。失败记录至少保存：原始消息、解析约束、返回菜名、命中禁忌、营养结果、修改数量和响应时间。

### P3：Docker 交付准备

虽然当前官方阶段只要求公网 API，但必须提前保证 Docker 可用：

```powershell
docker build -t fangtai-robot:latest .
docker run --rm -p 8000:8000 fangtai-robot:latest
curl.exe http://127.0.0.1:8000/api/health
docker save fangtai-robot:latest -o fangtai-robot.tar
```

构建前必须确认 `data/` 在本机存在。不要把外部 ChatGPT、阿里百炼或中转站设为最终运行的必要依赖，否则复赛离线评测和 Docker 交付会有风险。当前可选外部模型辅助在未配置环境变量时会自动关闭，Docker 离线 API 仍应能运行。

## 九、当前明确的风险和不要做的事

- 不要提交 `data/*.json`、`data/*.csv` 到公开 GitHub；
- 不要把密码、API Key、中转站地址和服务器凭据写进代码；
- 不要为了提升自然语言效果让大模型直接生成菜名；
- 不要让外部模型覆盖本地营养数值，`nutrition` 始终以本地计算为准；
- 不要取消 LLM 调用速度门控，否则普通请求可能被中转站延迟拖慢；
- 不要把营养估算称为医学诊断；
- 不要为了通过数量测试而放宽过敏和忌口过滤；
- 不要在用户提出局部修改时无理由更换整桌菜单；
- 不要把开发期 Customer Agent / Review Agent 误当成最终推荐服务依赖；
- 不要在没有验证 ECS 最新版本的情况下声称公网已经是最新代码；
- 不要直接执行 `git reset --hard` 或覆盖用户未提交的工作。

## 十、新线程可直接粘贴的启动指令

把下面内容作为新线程第一条消息，可以让新线程快速进入工作状态：

```text
请先阅读 D:\Codex\Lesson\fangtaiRobot\docs\PROJECT_HANDOFF.md，并检查仓库当前 git status、最近提交和数据文件是否存在。

项目是方太“个性化膳食规划 Agent”，目标是提高官方评分体系下的硬约束满足率、多人整桌营养均衡、多轮上下文一致性、最小化修改和响应速度。不要重复实现文档中已经完成的功能。当前允许外部模型作为可选增强，但不得引入最终运行必需的外部模型或外网 API。

请按 P0 -> P1 -> P2 -> P3 顺序推进。先运行完整测试集和复杂场景批测，输出失败样例，再针对失败样例补回归测试并修改代码。每次修改后都要验证：官方菜谱真实性、过敏/忌口零违反、菜品数量、多人健康约束、营养结果、多轮菜单保留、上下文回放、版本回滚和响应时间。

当前最重要的验收输入是：
{
  "messages": [
    "4个人吃午餐，先推荐4道菜。",
    "我不吃鸡蛋，其他菜尽量别动。"
  ]
}

要求最终仍能离线运行，并保留 Dockerfile、API 文档和公网部署能力。完成前不要只报告计划，必须实际测试、修改和验证。
```
