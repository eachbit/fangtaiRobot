# Evaluation Foundation Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可复现、可判分、可报告的高级膳食评测基础设施，覆盖不同健康客户、整桌结构、多轮操作和语言变化，并准确暴露当前系统的能力缺口。

**Architecture:** 测试系统先生成带隐藏标准答案的结构化健康客户和场景，再调用现有本地推荐入口，最后由确定性判分器检查硬约束、菜谱真实性、健康字段、整桌结构、营养一致性和多轮关系。第一阶段允许把尚未实现的高级能力记录为版本化 `known_gap`，但现有硬约束和历史回归仍然是阻断项；外部 Agent 只通过经过 schema 校验的 JSON 候选入口参与开发期探索。

**Tech Stack:** Python 3.11 标准库、`dataclasses`、`unittest`、CSV/JSON 离线数据、现有本地推荐函数、Codex 子 Agent。

---

## Program Roadmap

- Phase 1：确定性评测基础设施、健康客户、覆盖率、失败归档和 Agent 候选契约。
- Phase 2：根据 Phase 1 报告实现荤素、冷热、烹饪方式、健康冲突和高级多轮规划。
- Phase 3：每日多 Agent 探索、自动最小化、回归入库和草稿 PR 修复闭环。
- Phase 4：Docker 禁网、隐藏盲测、性能优化和竞赛提交材料。

Phase 1 不通过修改预期结果来隐藏当前高级能力缺口。它必须把“荤素比例无法调整”等问题稳定识别并输出统一违反代码，为 Phase 2 提供真实基线。

## File Map

- Create `tests/evaluation/__init__.py`: 评测包入口。
- Create `tests/evaluation/schemas.py`: 健康客户、场景、标准答案、违反项和报告结构。
- Create `tests/evaluation/persona_factory.py`: 官方档案适配和合成健康客户生成。
- Modify `app/recipe_features.py`: 提供统一的整桌可判分菜谱特征。
- Create `tests/evaluation/deterministic_oracle.py`: 本地确定性判分器。
- Create `tests/evaluation/scenario_generator.py`: 健康分层和高级场景组合生成。
- Create `tests/evaluation/language_mutator.py`: 版本化离线语言变形。
- Create `tests/evaluation/dialogue_state_machine.py`: 多轮操作生成。
- Create `tests/evaluation/failure_minimizer.py`: 失败对话最小化。
- Create `tests/evaluation/runner.py`: quick/daily/deep 运行入口。
- Create `tests/evaluation/report.py`: JSON 和 Markdown 报告。
- Create `tests/evaluation/agent_candidates.py`: 开发期多 Agent 候选校验。
- Create `scripts/run_evaluation.py`: 命令行入口。
- Create `tests/corpus/seeds/advanced_scenarios.json`: 人工确认的高级种子。
- Create `tests/corpus/known_gaps/phase1.json`: 第一阶段已知能力缺口。
- Create `tests/corpus/regressions/.gitkeep`: 永久回归目录。
- Create `tests/corpus/holdout/sample_health_structure.json`: 公开的盲测加载样例，不作为真实隐藏集。
- Modify `.gitignore`: 忽略运行报告和 Agent 候选暂存区。
- Modify `README.md`: 增加评测命令和阶段说明。

---

### Task 1: Define Typed Evaluation Contracts

**Files:**
- Create: `tests/evaluation/__init__.py`
- Create: `tests/evaluation/schemas.py`
- Create: `tests/test_evaluation_schemas.py`

- [ ] **Step 1: Write the failing round-trip test**

```python
import unittest

from tests.evaluation.schemas import HealthPersona, MenuExpectation, Scenario


class EvaluationSchemaTests(unittest.TestCase):
    def test_round_trip_preserves_health_and_menu_ground_truth(self) -> None:
        scenario = Scenario(
            scenario_id="ratio-001",
            persona=HealthPersona(
                persona_id="multi-001",
                primary_bucket="multi_condition",
                gender="男",
                age=62,
                labor_intensity="低",
                pregnancy_week=None,
                taste_preference="清淡",
                special_groups=("高血压", "高血糖"),
                allergens=("花生",),
                health_goals=("降压", "控糖"),
            ),
            messages=("六个人晚餐，六道菜，两荤四素",),
            expectation=MenuExpectation(
                dish_count=6,
                meat_count=2,
                vegetable_count=4,
                minimum_cooking_methods=2,
            ),
            seed=20260713,
            intent="structure_ratio",
            dialogue_mode="single_turn",
        )

        self.assertEqual(Scenario.from_dict(scenario.to_dict()), scenario)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_evaluation_schemas -v`

Expected: FAIL with `ModuleNotFoundError` for `tests.evaluation`.

- [ ] **Step 3: Implement immutable schemas**

Implement these frozen dataclasses in `schemas.py`:

```python
@dataclass(frozen=True)
class HealthPersona:
    persona_id: str
    primary_bucket: str
    source_user_id: int | None = None
    gender: str | None = None
    age: int | None = None
    labor_intensity: str | None = None
    pregnancy_week: str | None = None
    taste_preference: str | None = None
    special_groups: tuple[str, ...] = ()
    allergens: tuple[str, ...] = ()
    health_goals: tuple[str, ...] = ()


@dataclass(frozen=True)
class MenuExpectation:
    dish_count: int | None = None
    meat_count: int | None = None
    vegetable_count: int | None = None
    minimum_cooking_methods: int | None = None
    forbidden_terms: tuple[str, ...] = ()
    clarification_required: bool = False
    preserve_unaffected: bool = False


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    persona: HealthPersona
    messages: tuple[str, ...]
    expectation: MenuExpectation
    seed: int
    intent: str = "general_recommendation"
    dialogue_mode: str = "single_turn"
```

Add explicit `to_dict()` and `from_dict()` methods that convert JSON lists back to tuples. Reject unknown `primary_bucket`, unknown `dialogue_mode`, empty `scenario_id`, and empty `intent` values with `ValueError`.

- [ ] **Step 4: Add violation and report contracts**

Add frozen `Violation(code, severity, message, evidence)`, `ScenarioResult(scenario_id, passed, violations, elapsed_ms)`, `FailureRecord(scenario_id, seed, commit_sha, original_messages, minimized_messages, violations, elapsed_ms)`, and `EvaluationReport(total, passed, failures, coverage, metrics, timings)` dataclasses. Restrict severity to `blocking`, `known_gap`, and `soft_review`.

- [ ] **Step 5: Run schema tests**

Run: `python -m unittest tests.test_evaluation_schemas -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add tests/evaluation/__init__.py tests/evaluation/schemas.py tests/test_evaluation_schemas.py
git commit -m "test: define evaluation contracts"
```

### Task 2: Generate Persistent Health Personas

**Files:**
- Create: `tests/evaluation/persona_factory.py`
- Create: `tests/test_persona_factory.py`

- [ ] **Step 1: Write failing quota and reproducibility tests**

```python
import collections
import unittest

from tests.evaluation.persona_factory import build_personas


class PersonaFactoryTests(unittest.TestCase):
    def test_one_hundred_personas_match_exclusive_health_quotas(self) -> None:
        personas = build_personas(seed=20260713, count=100)
        counts = collections.Counter(item.primary_bucket for item in personas)
        self.assertEqual(counts, {
            "healthy": 20,
            "single_condition": 25,
            "multi_condition": 30,
            "special_group": 15,
            "high_risk": 10,
        })

    def test_same_seed_produces_identical_personas(self) -> None:
        self.assertEqual(build_personas(7, 50), build_personas(7, 50))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_persona_factory -v`

Expected: FAIL because `persona_factory` is missing.

- [ ] **Step 3: Implement exact quota allocation**

Use these exclusive primary weights:

```python
BUCKET_WEIGHTS = {
    "healthy": 0.20,
    "single_condition": 0.25,
    "multi_condition": 0.30,
    "special_group": 0.15,
    "high_risk": 0.10,
}
```

Use largest-remainder allocation so arbitrary counts still sum exactly. Reject counts below `10`. Use `random.Random(seed)` and stable persona IDs containing bucket, seed, and index.

- [ ] **Step 4: Cover official and extended health axes**

Load the official 50 profiles through `app.data_loader.load_users()`, preserve all official fields, and set `source_user_id` so the runner can call the real profile path. Synthesize additional combinations from high blood pressure, high blood glucose, high uric acid, pregnancy, lactation, children, older adults, activity level, and official allergens. Synthetic personas use `source_user_id=None` and disclose their health ground truth through generated dialogue. Extended high-risk conditions must set `primary_bucket="high_risk"` and expect clarification rather than a medical treatment rule.

- [ ] **Step 5: Add negative-control tests**

Assert healthy personas have no disease groups and at least 20% of generated messages later use negative health expressions such as “我没有高血压”, preventing keyword-only recognition.

- [ ] **Step 6: Run persona and existing profile tests**

Run: `python -m unittest tests.test_persona_factory tests.test_agent -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/evaluation/persona_factory.py tests/test_persona_factory.py
git commit -m "test: generate diverse health personas"
```

### Task 3: Provide Deterministic Recipe Structure Features

**Files:**
- Modify: `app/recipe_features.py`
- Create: `tests/test_recipe_structure.py`

- [ ] **Step 1: Write failing structure tests**

```python
import unittest

from app.models import Recipe
from app.recipe_features import analyze_recipe


class RecipeStructureTests(unittest.TestCase):
    def test_fish_and_leafy_vegetable_have_distinct_styles(self) -> None:
        fish = Recipe(1, "清蒸鲈鱼", "鲈鱼、生姜", "上锅蒸熟", [])
        vegetable = Recipe(2, "蒜蓉生菜", "生菜、大蒜", "大火炒熟", [])
        self.assertEqual(analyze_recipe(fish).protein_style, "meat")
        self.assertEqual(analyze_recipe(fish).cooking_method, "蒸")
        self.assertEqual(analyze_recipe(vegetable).protein_style, "vegetable")
        self.assertEqual(analyze_recipe(vegetable).cooking_method, "炒")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_recipe_structure -v`

Expected: FAIL because `analyze_recipe` is missing.

- [ ] **Step 3: Implement the feature contract**

Add frozen `RecipeFeatures(category, protein_style, temperature, cooking_method)`. Classify any recipe containing a recognized animal ingredient as `meat`; classify a recipe without animal ingredients but with vegetables, fungi, tofu, or beans as `vegetable`; otherwise use `other`. Detect `凉拌` before generic `拌`, then detect 蒸、炒、炖、煮、炸、烤 and unknown.

- [ ] **Step 4: Preserve compatibility**

Make existing `classify_recipe()` call `analyze_recipe(recipe).category`. Keep all current category names and breakfast helpers unchanged.

- [ ] **Step 5: Run recipe, planner, and revision tests**

Run: `python -m unittest tests.test_recipe_structure tests.test_agent tests.test_menu_revision -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/recipe_features.py tests/test_recipe_structure.py
git commit -m "feat: expose deterministic recipe structure"
```

### Task 4: Build the Deterministic Oracle

**Files:**
- Create: `tests/evaluation/deterministic_oracle.py`
- Create: `tests/test_deterministic_oracle.py`

- [ ] **Step 1: Write failing gold-standard tests**

```python
import unittest

from app.models import Recipe
from tests.evaluation.deterministic_oracle import evaluate_result
from tests.evaluation.schemas import HealthPersona, MenuExpectation, Scenario


class DeterministicOracleTests(unittest.TestCase):
    def test_wrong_vegetable_count_has_stable_violation_code(self) -> None:
        scenario = Scenario(
            "ratio-gold",
            HealthPersona("healthy-1", "healthy"),
            ("六道菜，两荤四素",),
            MenuExpectation(dish_count=6, meat_count=2, vegetable_count=4),
            1,
        )
        official = {
            index: Recipe(index, f"肉菜{index}", "猪肉、生姜", "炒熟", [])
            for index in range(6)
        }
        menu = [
            {"id": item.id, "name": item.name, "ingredients": item.ingredients}
            for item in official.values()
        ]
        result = evaluate_result(scenario, {"menu": menu}, official)
        self.assertIn("structure.vegetable_count", [item.code for item in result.violations])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_deterministic_oracle -v`

Expected: FAIL because the oracle is missing.

- [ ] **Step 3: Implement ordered hard checks**

Check response schema, official recipe ID/name consistency, duplicate IDs, forbidden terms through `contains_food_term`, dish count, health field extraction, table structure, nutrition sums, clarification, minimal change, and elapsed time. Derive menu structure from official `Recipe` objects through `analyze_recipe`; do not trust structure labels supplied by the response being evaluated. Unknown feature coverage must emit `coverage.recipe_structure` and cannot count as a successful exact-ratio claim.

- [ ] **Step 4: Separate regression and capability-gap severity**

Load known gaps from `tests/corpus/known_gaps/phase1.json` as exact `(scenario_id, violation_code)` pairs with `owner_phase` and `expires_after_phase`. Historical hard constraints, authenticity, response schema, and existing regression failures are always `blocking`. A violation code match without the same scenario ID remains blocking, preventing broad code-level downgrades.

- [ ] **Step 5: Test incorrect and correct menus**

Add gold fixtures for correct 2:4 structure, wrong structure, peanut violation, fake recipe ID, negative health false positive, and mismatched nutrition sum. Assert exact violation codes and severity.

- [ ] **Step 6: Run oracle and existing judge tests**

Run: `python -m unittest tests.test_deterministic_oracle tests.test_judge_suite -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/evaluation/deterministic_oracle.py tests/test_deterministic_oracle.py
git commit -m "test: add deterministic evaluation oracle"
```

### Task 5: Generate Measurable Advanced Scenarios

**Files:**
- Create: `tests/evaluation/scenario_generator.py`
- Create: `tests/evaluation/language_mutator.py`
- Create: `tests/evaluation/dialogue_state_machine.py`
- Create: `tests/test_scenario_generator.py`
- Create: `tests/test_language_metamorphic.py`

- [ ] **Step 1: Write failing coverage tests**

```python
import unittest

from tests.evaluation.scenario_generator import generate_scenarios, summarize_coverage


class ScenarioGeneratorTests(unittest.TestCase):
    def test_two_hundred_cases_keep_health_and_advanced_intent_coverage(self) -> None:
        scenarios = generate_scenarios(seed=20260713, count=200)
        coverage = summarize_coverage(scenarios)
        self.assertEqual(coverage["primary_bucket"]["multi_condition"], 60)
        self.assertGreater(coverage["intent"]["structure_ratio"], 0)
        self.assertGreater(coverage["intent"]["relative_revision"], 0)
        self.assertGreater(coverage["dialogue"]["multi_turn"], 0)
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `python -m unittest tests.test_scenario_generator tests.test_language_metamorphic -v`

Expected: FAIL because generators are missing.

- [ ] **Step 3: Implement a mandatory intent cycle**

Cycle through `hard_constraint`, `health_profile`, `structure_ratio`, `relative_revision`, `cooking_diversity`, `nutrition_tradeoff`, `ambiguous_request`, `negative_expression`, and `multi_person_conflict`. Use `random.Random(seed)` only for choices inside each slot.

- [ ] **Step 4: Implement reviewed offline language mutations**

Store each phrase with an explicit expected intent. Include “多来几个素菜”, “少整点荤的”, “肉菜太多了”, “荤素一比二”, “别全是蒸的”, “我没有高血压”, and “不要把素菜换掉”. Never derive the expected intent by parsing the generated text.

- [ ] **Step 5: Implement multi-round operations**

Support `append_constraint`, `retract_preference`, `request_position_change`, `request_structure_change`, `ambiguous_change`, and `confirm_clarification`. Keep the same persona and long-term health profile throughout a dialogue.

- [ ] **Step 6: Measure single and pair coverage**

Return counts for every dimension and sorted dimension pairs. Validation fails if any health bucket or mandatory advanced intent is absent. Identical seeds must produce byte-identical scenario JSON.

- [ ] **Step 7: Run generator and metamorphic tests**

Run: `python -m unittest tests.test_scenario_generator tests.test_language_metamorphic -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add tests/evaluation/scenario_generator.py tests/evaluation/language_mutator.py tests/evaluation/dialogue_state_machine.py tests/test_scenario_generator.py tests/test_language_metamorphic.py
git commit -m "test: generate advanced health scenarios"
```

### Task 6: Minimize Failures and Produce Versioned Reports

**Files:**
- Create: `tests/evaluation/failure_minimizer.py`
- Create: `tests/evaluation/report.py`
- Create: `tests/evaluation/runner.py`
- Create: `scripts/run_evaluation.py`
- Create: `tests/test_evaluation_runner.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing runner tests**

```python
import tempfile
import unittest
from pathlib import Path

from tests.evaluation.runner import EvaluationRunner


class EvaluationRunnerTests(unittest.TestCase):
    def test_runner_writes_summary_and_reproducible_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = EvaluationRunner(Path(directory), seed=7).run_count(20)
            self.assertEqual(report.total, 20)
            self.assertTrue((Path(directory) / "summary.json").exists())
            self.assertTrue((Path(directory) / "summary.md").exists())
            for failure in report.failures:
                self.assertEqual(failure.seed, 7)
                self.assertTrue(failure.minimized_messages)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_evaluation_runner -v`

Expected: FAIL because the runner is missing.

- [ ] **Step 3: Implement deterministic minimization**

Remove dialogue turns first, then punctuation-delimited clauses. Accept a reduction only when the same stable violation code reproduces three consecutive times. Cap attempts at `100` and store whether minimization reached the cap.

- [ ] **Step 4: Implement runner modes**

Use exact defaults:

```python
MODE_COUNTS = {"quick": 120, "daily": 2000, "deep": 10000}
```

`quick` includes regressions and generated scenarios; `daily` additionally reads validated Agent candidates; `deep` additionally reads long-dialogue cases and a private holdout directory supplied through `--holdout-dir` or `EVAL_HOLDOUT_DIR`. Blocking violations produce exit code `1`; known gaps are reported but do not hide blocking failures.

For `single_turn`, call `recommend(source_user_id, messages)`. For `multi_turn`, call `recommend_with_session` with the first turn, then send each later turn as `is_delta=True` using the returned `session_id` and `menu_version`. Record every intermediate result so the oracle can compare preserved constraints and menu IDs.

- [ ] **Step 5: Implement reviewed-label metrics and report output**

Compute per-field true positives, false positives, and false negatives by comparing returned constraints with the scenario persona and expectation. Report precision, recall, and F1 for special groups, allergens, health goals, dish count, people count, and structure intent; do not use Agent opinions as labels. Write `summary.json`, `summary.md`, `coverage.json`, and one JSON file per failure. Include commit SHA, seed, mode, health bucket counts, intent counts, pair coverage, violation counts, P50/P95, minimized messages, and known-gap status.

- [ ] **Step 6: Implement CLI**

Support `--mode`, `--seed`, `--count`, `--output`, and `--include-holdout`. Default output is `artifacts/evaluation/<UTC timestamp>/`. Add `artifacts/evaluation/` to `.gitignore`.

- [ ] **Step 7: Run tests and a 120-case quick evaluation**

Run: `python -m unittest tests.test_evaluation_runner -v`

Run: `python scripts/run_evaluation.py --mode quick --seed 20260713`

Expected: unit tests PASS; the report accurately separates blocking failures from versioned known gaps.

- [ ] **Step 8: Commit**

```powershell
git add tests/evaluation/failure_minimizer.py tests/evaluation/report.py tests/evaluation/runner.py scripts/run_evaluation.py tests/test_evaluation_runner.py .gitignore
git commit -m "test: report reproducible evaluation failures"
```

### Task 7: Add the Development-Only Multi-Agent Candidate Contract

**Files:**
- Create: `tests/evaluation/agent_candidates.py`
- Create: `tests/test_agent_candidates.py`
- Create: `docs/evaluation/agent-prompts.md`

- [ ] **Step 1: Write failing candidate validation tests**

```python
import unittest

from tests.evaluation.agent_candidates import validate_candidate


class AgentCandidateTests(unittest.TestCase):
    def test_agent_review_is_soft_and_cannot_override_ground_truth(self) -> None:
        value = validate_candidate({
            "candidate_id": "negative-health-1",
            "messages": ["我没有高血压，推荐四道菜"],
            "structured_ground_truth": {"special_groups": []},
            "agent_review": {"naturalness": 4, "notes": "口语自然"},
        })
        self.assertEqual(value.structured_ground_truth["special_groups"], [])
        self.assertTrue(value.agent_review_is_soft)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_agent_candidates -v`

Expected: FAIL because candidate validation is missing.

- [ ] **Step 3: Implement strict JSON validation**

Reject unknown health buckets, missing structured ground truth, more than 12 turns, turns longer than 500 characters, and unknown top-level keys. Treat all Agent review fields as `soft_review`. Store unapproved candidates only below ignored `artifacts/evaluation/candidates/`.

- [ ] **Step 4: Document four Agent output contracts**

Define customer, advanced-scenario, judge, and red-team prompts. Each prompt must emit JSON matching the candidate schema. State that Agent output cannot directly change `app/`, expected hard constraints, known gaps, or merge status.

- [ ] **Step 5: Document root-cause Agent boundaries**

The root-cause Agent receives saved evidence and may propose a failing regression test, but it cannot merge, alter holdout expectations, add production network calls, or classify a blocking failure as known gap.

- [ ] **Step 6: Run candidate tests**

Run: `python -m unittest tests.test_agent_candidates -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/evaluation/agent_candidates.py tests/test_agent_candidates.py docs/evaluation/agent-prompts.md
git commit -m "test: define safe multi-agent candidates"
```

### Task 8: Seed Reviewed Scenarios, Known Gaps, and Holdout Data

**Files:**
- Create: `tests/corpus/seeds/advanced_scenarios.json`
- Create: `tests/corpus/known_gaps/phase1.json`
- Create: `tests/corpus/regressions/.gitkeep`
- Create: `tests/corpus/holdout/sample_health_structure.json`
- Create: `tests/test_evaluation_corpus.py`

- [ ] **Step 1: Write failing corpus integrity tests**

Assert every JSON file parses through `Scenario.from_dict`, scenario IDs are globally unique, all five health buckets exist, every known gap has an owner phase and stable violation code, and holdout cases are excluded from normal scenario loading.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_evaluation_corpus -v`

Expected: FAIL because corpus files are absent.

- [ ] **Step 3: Add reviewed seed scenarios**

Add at least 30 seeds covering official profiles, single and multiple health conditions, allergies, explicit 1:2 ratio, cooking diversity, more vegetables with preservation, negative health expressions, ambiguous ratio, multi-person conflict, and nutrition tradeoffs.

- [ ] **Step 4: Add narrow Phase 1 known gaps**

Only list capabilities demonstrated to fail before Phase 2, such as `structure.ratio`, `structure.cooking_diversity`, and `dialogue.clarification`. Do not include allergy, authenticity, schema, existing session behavior, or nutrition arithmetic.

- [ ] **Step 5: Add a public holdout loader sample and create the private set outside Git**

Add five public sample cases, one per health bucket, solely to test loading and aggregate reporting. Create at least 20 real holdout scenarios under an external directory such as `D:\Codex\Lesson\.evaluation-private\fangtaiRobot\holdout`, add `.evaluation-private/` to the parent workspace ignore policy, and never commit that directory. The runner may emit aggregate holdout scores and violation categories, but normal failure artifacts must not copy holdout messages or expected answers.

- [ ] **Step 6: Run corpus integrity tests**

Run: `python -m unittest tests.test_evaluation_corpus -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/corpus tests/test_evaluation_corpus.py
git commit -m "test: seed advanced evaluation corpus"
```

### Task 9: Document and Verify Phase 1

**Files:**
- Modify: `README.md`
- Modify: `docs/api.md`

- [ ] **Step 1: Document evaluation commands and report interpretation**

Document quick, daily, and deep modes; report paths; health bucket quotas; blocking versus known-gap semantics; and the development-only status of external Agent candidates.

- [ ] **Step 2: Document Phase 1 boundaries**

State that Phase 1 measures advanced structure and dialogue gaps but does not yet promise those capabilities pass. Link failures to the Phase 2 implementation backlog instead of changing expected results.

- [ ] **Step 3: Run the complete Phase 1 gate**

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tests/audit_recommendations.py
python tests/nutrition_coverage.py
python scripts/run_evaluation.py --mode quick --seed 20260713
git diff --check
```

Expected: all unit and existing audit tests exit `0`; quick evaluation contains no blocking violation; current advanced capability failures appear only under reviewed Phase 1 known-gap codes.

- [ ] **Step 4: Verify a known advanced failure is detected**

Run a focused ratio seed and confirm the report contains `structure.ratio` until Phase 2 implements it. This proves the framework detects the user-reported gap rather than silently passing it.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md docs/api.md
git commit -m "docs: explain evaluation foundation workflow"
```

- [ ] **Step 6: Push and verify the draft PR**

```powershell
git -c http.proxy= -c https.proxy= push origin feature/nutrition-session
gh pr view 1 --repo eachbit/fangtaiRobot --json url,isDraft,baseRefName,headRefName
```

Expected: PR `#1` remains draft, base is `main`, and head is `feature/nutrition-session`.

---

## Phase 1 Acceptance Gate

- [ ] Health personas satisfy exclusive `20/25/30/15/10` bucket quotas.
- [ ] Official profile fields remain intact and synthetic profiles are reproducible by seed.
- [ ] Recipe structure classification exposes meat, vegetable, temperature, role, and cooking method with unknown coverage reported honestly.
- [ ] Deterministic oracle has stable violation codes and cannot downgrade existing hard constraints.
- [ ] Generated cases include ratio, relative revision, cooking diversity, nutrition tradeoff, ambiguity, negative expression, and multi-person conflict.
- [ ] Reports show per-health-bucket and per-intent pass rates rather than only total case count.
- [ ] Confirmed failures contain seed, commit SHA, violation code, timing, original messages, and minimized messages.
- [ ] Agent candidates are soft evidence and cannot alter hard expected results.
- [ ] Holdout data is excluded from normal repair artifacts.
- [ ] Current “荤素比例无法调整” problem is detected as a stable Phase 2 capability gap.
- [ ] Existing 60+ tests and recommendation audit remain green.
