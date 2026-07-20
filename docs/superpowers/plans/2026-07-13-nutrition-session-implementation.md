# Nutrition Scoring And Session Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为官方菜谱推荐加入可追溯营养估算、整桌与人均均衡评分、带最小修改原则的双模式会话，以及可重复的自动压力测试。

**Architecture:** 营养能力拆分为食材解析、离线数据仓库、计算与评分四层；推荐器只消费标准营养摘要。会话能力通过线程安全的内存存储保存菜单快照，并由独立修订器保留未冲突菜品；无会话 ID 时通过完整消息历史确定性重放。所有新增字段保持现有 API 向后兼容。

**Tech Stack:** Python 3 标准库、`unittest`、CSV/JSON 离线数据、现有 `ThreadingHTTPServer`、原生 HTML/CSS/JavaScript。

---

### Task 1: 食材文本解析

**Files:**
- Create: `app/ingredient_parser.py`
- Create: `tests/test_ingredient_parser.py`

- [ ] **Step 1: Write failing parser tests**

测试明确克数、千克、毫升、鸡蛋个数、半勺、少许/适量以及无法解析项；断言输出包含标准名称、克数、用量来源与可信度。

- [ ] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_ingredient_parser -v`
Expected: FAIL because `app.ingredient_parser` does not exist.

- [ ] **Step 3: Implement minimal parser**

使用正则拆分“主料/辅料/调料”和中文分隔符，完成 `g/kg/ml/个/勺/碗` 换算；自然单位只从版本化常量表读取，模糊量记录 `default`，未知量记录 `unknown`。

- [ ] **Step 4: Run test and verify GREEN**

Run: `python -m unittest tests.test_ingredient_parser -v`
Expected: all parser tests pass.

- [ ] **Step 5: Commit**

Run: `git add app/ingredient_parser.py tests/test_ingredient_parser.py && git commit -m "feat: parse recipe ingredient quantities"`

### Task 2: 离线营养数据仓库与单菜计算

**Files:**
- Create: `data/nutrition/foods.json`
- Create: `data/nutrition/aliases.json`
- Create: `data/nutrition/README.md`
- Create: `app/nutrition_repository.py`
- Create: `app/nutrition_calculator.py`
- Create: `tests/test_nutrition_calculator.py`

- [ ] **Step 1: Write failing repository and formula tests**

覆盖番茄/西红柿别名、鸡胸肉 200g 的逐项公式、盐和酱油钠贡献、菜品合计、推定份数、缺失食材及覆盖率。测试使用固定基准条目，不依赖网络。

- [ ] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_nutrition_calculator -v`
Expected: FAIL because nutrition modules do not exist.

- [ ] **Step 3: Add traceable offline dataset and repository**

数据条目包含标准名称、来源、来源 ID、每 100g 七项营养值；别名表只做确定映射。README 记录 USDA FoodData Central 来源、字段含义和估算边界。

- [ ] **Step 4: Implement recipe calculation**

实现 `calculate_recipe_nutrition(recipe)`，返回营养总量、推定份数、逐食材明细、缺失项、重量覆盖率和可信等级；未知项不编造营养值。

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_nutrition_calculator -v`
Expected: all nutrition calculation tests pass.

- [ ] **Step 6: Commit**

Run: `git add app/nutrition_repository.py app/nutrition_calculator.py data/nutrition tests/test_nutrition_calculator.py && git commit -m "feat: calculate traceable recipe nutrition"`

### Task 3: 整桌、人均与个体化营养评分

**Files:**
- Create: `app/nutrition_targets.py`
- Create: `app/nutrition_scoring.py`
- Create: `tests/test_nutrition_scoring.py`
- Modify: `app/planner.py`

- [ ] **Step 1: Write failing table scoring tests**

构造两道固定菜，断言整桌总量等于单菜之和、人均等于总量除人数、高血压钠超限、增肌蛋白质不足、宏量供能比、低覆盖率不能显示精确达标。

- [ ] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_nutrition_scoring -v`
Expected: FAIL because scoring modules do not exist.

- [ ] **Step 3: Implement targets and scoring**

按早餐/午餐/晚餐建立一般成人餐次目标；对高血压、控糖、减脂和增肌应用透明修正。返回分项状态、0-100 总分、整桌总量、人均营养与可信度。

- [ ] **Step 4: Attach nutrition to planner output**

每个 `menu` 项新增 `nutrition`，响应新增 `nutrition`、`nutrition_score` 和 `confidence`；原字段保持不变。候选组合在硬约束满足后使用营养偏差作为选择依据。

- [ ] **Step 5: Run tests and full regression**

Run: `python -m unittest tests.test_nutrition_scoring -v && python tests/test_agent.py && python tests/audit_recommendations.py`
Expected: all tests pass and old response contract remains usable.

- [ ] **Step 6: Commit**

Run: `git add app/nutrition_targets.py app/nutrition_scoring.py app/planner.py tests/test_nutrition_scoring.py && git commit -m "feat: score whole-table nutrition balance"`

### Task 4: 会话存储与请求兼容

**Files:**
- Create: `app/session_store.py`
- Create: `tests/test_session_store.py`
- Modify: `app/agent.py`
- Modify: `server.py`

- [ ] **Step 1: Write failing session tests**

验证创建随机会话、版本递增、过期、最大容量淘汰、版本冲突和线程安全更新；验证旧 `recommend(user_id, messages)` 调用仍然工作。

- [ ] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_session_store -v`
Expected: FAIL because session store does not exist.

- [ ] **Step 3: Implement bounded in-memory session store**

使用 `RLock`、单调时钟、TTL 和 LRU 顺序保存消息、约束、菜单与版本。会话数据不写磁盘。

- [ ] **Step 4: Extend agent and HTTP request normalization**

支持原有 `messages`、单条 `message`、可选 `session_id` 和 `menu_version`；返回 `session_id` 与 `menu_version`。无效会话可在完整历史存在时回退，不使旧客户端报错。

- [ ] **Step 5: Run tests and regression**

Run: `python -m unittest tests.test_session_store -v && python tests/test_agent.py`
Expected: session tests and legacy tests pass.

- [ ] **Step 6: Commit**

Run: `git add app/session_store.py app/agent.py server.py tests/test_session_store.py && git commit -m "feat: add compatible recommendation sessions"`

### Task 5: 最小修改菜单修订与历史重放

**Files:**
- Create: `app/menu_revision.py`
- Create: `app/history_replay.py`
- Create: `tests/test_menu_revision.py`
- Modify: `app/agent.py`
- Modify: `app/planner.py`
- Modify: `app/retriever.py`

- [ ] **Step 1: Write failing minimal-change tests**

覆盖首轮四道菜后新增虾过敏只替换含虾菜、无冲突追加保持 ID 和顺序、明确全部换掉、局部指定第二道、完整历史与会话模式一致，以及 `minimal_change` 不再固定为真。

- [ ] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_menu_revision -v`
Expected: assertions fail because existing planner regenerates the whole table.

- [ ] **Step 3: Implement deterministic revision**

对旧菜逐项执行现有硬约束检查并锁定可保留项；替换项先匹配原类别，再按营养和推荐分排序。相同分数按菜谱 ID，保证历史重放稳定。

- [ ] **Step 4: Implement full-history replay**

没有有效会话且 `messages` 至少两轮时，用前缀消息生成上一轮菜单，再应用最后一轮；显式“全部换掉”绕过锁定。

- [ ] **Step 5: Return auditable changes**

响应包含 `mode`、`kept_dishes`、`replaced_dishes`、`change_count`；评分卡根据实际修改结果计算 `minimal_change`。

- [ ] **Step 6: Run tests and full regression**

Run: `python -m unittest tests.test_menu_revision -v && python tests/test_agent.py && python tests/audit_recommendations.py`
Expected: all minimal-change and legacy tests pass.

- [ ] **Step 7: Commit**

Run: `git add app/menu_revision.py app/history_replay.py app/agent.py app/planner.py app/retriever.py tests/test_menu_revision.py && git commit -m "feat: preserve menus across dialogue turns"`

### Task 6: 自动评委压力测试与覆盖率报告

**Files:**
- Create: `tests/judge_cases/scenarios.json`
- Create: `tests/test_judge_suite.py`
- Create: `tests/nutrition_coverage.py`
- Modify: `tests/audit_recommendations.py`

- [ ] **Step 1: Write failing judge invariants**

固定场景覆盖过敏、忌口、多人冲突、疾病组合、数量、餐次、营养阈值、会话修改和全部重做。断言菜谱真实性、硬约束零违反、无重复、营养加总一致、修改最少和耗时。

- [ ] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_judge_suite -v`
Expected: at least one current logic issue is detected or required audit field is missing.

- [ ] **Step 3: Add deterministic generated cases**

使用固定随机种子组合约束，失败信息包含输入、约束、菜单 ID、违反规则、修改记录和耗时。不得通过放宽硬约束使测试通过。

- [ ] **Step 4: Add nutrition coverage report**

扫描 2000 道菜谱，报告食材匹配率、明确重量覆盖率、高频缺失食材和可信等级分布；报告不修改生产数据。

- [ ] **Step 5: Run judge suite and coverage report**

Run: `python -m unittest tests.test_judge_suite -v && python tests/nutrition_coverage.py`
Expected: judge invariants pass; report prints measured coverage and top missing terms.

- [ ] **Step 6: Commit**

Run: `git add tests/judge_cases tests/test_judge_suite.py tests/nutrition_coverage.py tests/audit_recommendations.py && git commit -m "test: add automated judge stress suite"`

### Task 7: 网页展示与文档

**Files:**
- Modify: `public/index.html`
- Modify: `public/app.js`
- Modify: `public/styles.css`
- Modify: `README.md`
- Create: `docs/api.md`

- [ ] **Step 1: Add browser acceptance assertions**

使用浏览器验证首轮会话 ID 保存、追加对话发送、每道菜营养、整桌/人均评分、可信度和修改记录可见，移动端无溢出。

- [ ] **Step 2: Implement session-aware demonstration UI**

保留当前输入方式，增加继续对话和新会话控制；显示营养单位、目标状态、可信度和被替换菜品，不用页面文字夸大医学精度。

- [ ] **Step 3: Document API and limitations**

README 和 API 文档给出兼容请求、会话请求、响应字段、Docker 离线数据说明与营养估算边界。

- [ ] **Step 4: Verify UI and API manually**

Run server, exercise both request forms, capture desktop/mobile screenshots, inspect console and network errors.

- [ ] **Step 5: Commit**

Run: `git add public README.md docs/api.md && git commit -m "feat: present nutrition and menu revisions"`

### Task 8: 最终验证

**Files:**
- Modify only files required by verified defects.

- [ ] **Step 1: Run all automated tests**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: zero failures and zero errors.

- [ ] **Step 2: Run legacy audit and coverage**

Run: `python tests/audit_recommendations.py && python tests/nutrition_coverage.py`
Expected: audit exits 0 and coverage is reported honestly.

- [ ] **Step 3: Verify API performance**

Measure cold and warm recommendations plus five-turn sessions; report P50/P95 and verify core warm recommendation stays below two seconds locally.

- [ ] **Step 4: Review diff and data licensing**

Run: `git diff main...HEAD --check && git status --short`
Expected: no whitespace errors, no official private datasets tracked, and only intentional files changed.

- [ ] **Step 5: Final review and integration decision**

Use independent spec and quality review, fix all material findings, then follow the branch finishing workflow.
