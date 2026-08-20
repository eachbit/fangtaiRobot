# 方太个性化膳食规划 Agent 项目交接文档

更新时间：2026-08-20（已切换详细健康档案版本）

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
- 相对远程状态：曾显示 `ahead 2`，继续开发前必须执行 `git status` 和 `git log` 确认；不要覆盖用户已有改动。
- 最近关键提交：
  - `4934a30 feat: add context replay and menu rollback`
  - `5997b7b docs: define context rollback design`
  - `f8db1bf feat: simulate audit sessions for minimal revisions`
  - `84ae9bc feat: tighten nutrition-aware menu reranking`
  - `32507f4 feat: add official audit scoring report`

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
- 菜品数量：例如“推荐4道菜”“安排6道”；
- 口味：清淡、辣、甜、咸、油等正向和否定表达；
- 健康目标：减脂、增肌、控糖、降压、补钙、补铁、养胃等；
- 过敏与忌口区分：例如“对鸡蛋过敏”和“我不吃鸡蛋”不是同一种约束；
- 常见食材别名扩展，例如虾、海蛎子、花甲等；
- 年龄、性别、孕期、老人、儿童、哺乳期和运动量等对话画像特征；
- 时间、难度、聚餐、便当和夏季清爽等场景要求；
- 多人同时提出的冲突约束，以及需要主动澄清的模糊场景。

### 3. 整桌营养计算和营养重排

`app/nutrition.py` 已提供离线营养估算：

- 整桌总热量、蛋白质、脂肪、碳水、膳食纤维、糖和钠；
- 按人数计算人均营养；
- 根据食材克数、毫升、个、只、勺等单位估算；
- 输出营养覆盖率、缺失食材、估算假设和置信度；
- 输出 `balance_level`，并在规划阶段用于换菜排序。

注意：这是竞赛解释和排序用的离线估算，不是医学诊断。当前数据存在食材营养字段不完整的情况，因此必须同时保留 `confidence` 和 `missing_ingredients`，不能把估算结果包装成临床精确值。

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
- 当前运行时只使用 Python 标准库，不依赖外部大模型或网络 API。

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

## 六、公网部署的最后已知状态

此前已在阿里云 ECS 上成功启动过服务：

```text
公网地址：http://47.116.110.131:8000/
健康检查：http://47.116.110.131:8000/api/health
```

此前已验证返回 `status=ok`、2000道菜谱和50份档案。这个状态是“最后已知状态”，不是当前线程自动验证的实时状态。新线程开始后必须重新检查：

```powershell
curl.exe http://47.116.110.131:8000/api/health
```

服务器之前使用的启动方式是：

```bash
HOST=0.0.0.0 PORT=8000 python3.11 server.py
```

已知风险：此前服务是前台运行，关闭 SSH 窗口会停止。公网交付前必须改为 `systemd`、`nohup` 或其他可靠后台服务，并在重启后验证。不要把 API 密钥、服务器密码或中转站密钥写入仓库和文档。

## 七、已经验证的测试结果

2026-08-20 在本机执行（包含详细档案字段回归校验）：

```powershell
python tests/test_agent.py
python tests/test_server_api.py
```

结果均通过：

- `test_agent.py`：2000道菜谱、50份用户、20组对话用例通过；
- `test_server_api.py`：审计任务 API、推荐 API、历史查询、回滚错误处理通过；
- `git diff --check`：通过。

详细档案回归校验已确认：50条用户记录均包含 `身高_cm`、`体重_kg`、`BMI`、`体检指标`；用户3的结构化对象可读取身高180.1cm、体重78.2kg、BMI 24.1及空腹血糖7.3 mmol/L。

针对历史典型问题“4个人吃午餐，推荐4道菜，但只返回2道”，当前本地复测结果为：

- `menu_count=4`；
- `people_count=4`；
- `requested_dish_count=4`；
- 只替换了必要菜品，`change_count=1`。

注意：这只是本机代码验证，还需要把同一版本同步到 ECS 后再测公网接口。此前旧样例中的 `nutrition.assessment=insufficient_data` 不应直接当成当前结构；当前实现主要返回 `table`、`per_person`、`balance_level` 和 `confidence`。

## 八、下一步开发顺序

新线程应按下面顺序执行，不要一开始就引入外部大模型或重写推荐器。

### P0：先完成当前版本验收

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

### P1：完成公网 API 交付

1. 将最新代码和 `data/` 数据同步到 ECS；
2. 改成后台常驻服务；
3. 验证 `/api/health`、`/api/recommend`、`/api/sessions/{id}/history`；
4. 确认安全组放行端口、服务重启后仍可用；
5. 补齐 API 接口文档和技术方案文档；
6. 提供给组委会稳定的公网 API 地址、请求示例、响应字段说明和测试账号/用户 ID 说明。

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

构建前必须确认 `data/` 在本机存在。不要把外部 ChatGPT、阿里百炼或中转站设为最终运行的必要依赖，否则复赛离线评测和 Docker 交付会有风险。

## 九、当前明确的风险和不要做的事

- 不要提交 `data/*.json`、`data/*.csv` 到公开 GitHub；
- 不要把密码、API Key、中转站地址和服务器凭据写进代码；
- 不要为了提升自然语言效果让大模型直接生成菜名；
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

项目是方太“个性化膳食规划 Agent”，目标是提高官方评分体系下的硬约束满足率、多人整桌营养均衡、多轮上下文一致性、最小化修改和响应速度。不要重复实现文档中已经完成的功能，也不要引入最终运行必需的外部模型或外网 API。

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
