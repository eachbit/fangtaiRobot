from __future__ import annotations

import json
import re
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.session_store import SessionStore
from app.constraints import extract_constraints
from app.food_terms import expand_terms
from tests.evaluation.runner import EvaluationRunner
from tests.evaluation.schemas import PRIMARY_BUCKETS, Scenario


CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"
SEEDS_ROOT = CORPUS_ROOT / "seeds"
HOLDOUT_ROOT = CORPUS_ROOT / "holdout"
REGRESSIONS_ROOT = CORPUS_ROOT / "regressions"
KNOWN_GAPS_PATH = CORPUS_ROOT / "known_gaps" / "phase1.json"
ADVANCED_SEEDS_PATH = SEEDS_ROOT / "advanced_scenarios.json"
PUBLIC_HOLDOUT_PATH = HOLDOUT_ROOT / "sample_health_structure.json"
KNOWN_GAP_FIELDS = (
    "scenario_id",
    "violation_code",
    "owner_phase",
    "expires_after_phase",
)
STABLE_VIOLATION_CODE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)
ALLOWED_PHASE1_KNOWN_GAP_CODES = {
    "dialogue.clarification",
    "structure.cooking_diversity",
    "structure.meat_count",
    "structure.vegetable_count",
}
PHASE1_KNOWN_GAP_BASELINE = frozenset(
    {
        ("adv-healthy-ratio-003", "structure.meat_count"),
        ("adv-healthy-ratio-003", "structure.vegetable_count"),
        ("adv-healthy-add-vegetables-preserve-005", "structure.meat_count"),
        ("adv-healthy-add-vegetables-preserve-005", "structure.vegetable_count"),
        ("adv-healthy-multi-person-tradeoff-006", "dialogue.clarification"),
        ("adv-single-glucose-ratio-008", "structure.meat_count"),
        ("adv-single-glucose-ratio-008", "structure.vegetable_count"),
        ("adv-single-uric-allergy-cooking-009", "structure.cooking_diversity"),
        ("adv-single-ambiguous-vegetable-ratio-012", "dialogue.clarification"),
        ("adv-multi-hypertension-uric-ratio-014", "structure.meat_count"),
        ("adv-multi-hypertension-uric-ratio-014", "structure.vegetable_count"),
        ("adv-multi-person-allergy-conflict-018", "dialogue.clarification"),
        ("adv-special-pregnancy-allergy-ratio-020", "structure.meat_count"),
        ("adv-special-pregnancy-allergy-ratio-020", "structure.vegetable_count"),
        ("adv-special-pregnancy-add-vegetables-023", "structure.meat_count"),
        ("adv-special-pregnancy-add-vegetables-023", "structure.vegetable_count"),
        ("adv-special-pregnancy-family-conflict-024", "dialogue.clarification"),
        ("adv-high-risk-metrics-clarification-025", "dialogue.clarification"),
        ("adv-high-risk-explicit-ratio-026", "structure.meat_count"),
        ("adv-high-risk-explicit-ratio-026", "structure.vegetable_count"),
        ("adv-high-risk-explicit-ratio-026", "dialogue.clarification"),
        ("adv-high-risk-allergy-cooking-027", "dialogue.clarification"),
        ("adv-high-risk-allergy-cooking-027", "structure.cooking_diversity"),
        ("adv-high-risk-minimal-revision-028", "dialogue.clarification"),
        ("adv-high-risk-negative-hypertension-029", "dialogue.clarification"),
        ("adv-high-risk-multi-person-nutrition-conflict-030", "dialogue.clarification"),
    }
)
RETIRED_PHASE1_KNOWN_GAPS = frozenset(
    {
        ("adv-healthy-cooking-diversity-004", "structure.cooking_diversity"),
        (
            "adv-single-hypertension-metrics-tradeoff-010",
            "structure.cooking_diversity",
        ),
        ("adv-multi-glucose-uric-cooking-015", "structure.cooking_diversity"),
    }
)
REQUIRED_DISCLOSED_GOALS = {
    "adv-multi-metrics-nutrition-tradeoff-016": {"足量蛋白质"},
    "adv-special-prepregnancy-calcium-tradeoff-022": {"补铁", "补钙"},
}
REQUIRED_VISIBLE_FACTS = {
    "adv-high-risk-allergy-cooking-027": {
        "高血压",
        "高血糖",
        "高尿酸",
        "降压",
        "控糖",
        "护肾",
    },
}
PUBLIC_SCENARIO_ROOTS = (SEEDS_ROOT, REGRESSIONS_ROOT, HOLDOUT_ROOT)
CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
NUMBER_TOKEN = r"[一二两三四五六七八九十]|\d+"
DISH_COUNT_PATTERN = re.compile(rf"({NUMBER_TOKEN})道")
ADDED_DISH_PATTERN = re.compile(
    rf"(?:再|只)?多?加({NUMBER_TOKEN})道(素菜|荤菜|菜)"
)
COMPOSITION_PATTERN = re.compile(
    rf"({NUMBER_TOKEN})荤({NUMBER_TOKEN})素"
)
COOKING_MINIMUM_PATTERN = re.compile(
    rf"至少[^。；\n]{{0,30}}?({NUMBER_TOKEN})种(?:不同)?烹饪(?:方式|方法)?"
)
CLARIFICATION_MARKERS = ("澄清", "确认", "先问", "问清", "询问")
PRESERVATION_MARKERS = ("保留", "不变", "不要改", "只换", "只改", "只多加", "未受影响")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(value: str) -> object:
    return json.loads(value, object_pairs_hook=_strict_json_object)


def load_json_file(path: Path) -> object:
    return strict_json_loads(path.read_text(encoding="utf-8"))


def _number(value: str) -> int:
    return int(value) if value.isascii() and value.isdigit() else CHINESE_NUMBERS[value]


def load_scenario_directory(
    directory: Path,
    *,
    require_files: bool = True,
) -> tuple[Scenario, ...]:
    paths = sorted(directory.rglob("*.json")) if directory.is_dir() else []
    if require_files and not paths:
        raise AssertionError(f"scenario directory has no JSON files: {directory}")

    scenarios: list[Scenario] = []
    for path in paths:
        payload = load_json_file(path)
        values = payload if type(payload) is list else [payload]
        for index, value in enumerate(values):
            try:
                scenarios.append(Scenario.from_dict(value))
            except (TypeError, ValueError) as error:
                raise AssertionError(f"invalid scenario at {path}[{index}]: {error}") from error
    return tuple(scenarios)


def _explicit_final_dish_count(messages: str) -> int | None:
    base_counts = [
        _number(match.group(1))
        for match in DISH_COUNT_PATTERN.finditer(messages)
        if _number(match.group(1)) >= 4
    ]
    if not base_counts:
        return None
    additions = sum(
        _number(match.group(1))
        for match in ADDED_DISH_PATTERN.finditer(messages)
    )
    return base_counts[0] + additions


def _explicit_meat_vegetable_counts(
    messages: str,
    dish_count: int | None,
) -> tuple[int, int] | None:
    compositions = list(COMPOSITION_PATTERN.finditer(messages))
    if compositions:
        selected = compositions[-1]
        meat_count = _number(selected.group(1))
        vegetable_count = _number(selected.group(2))
        trailing_messages = messages[selected.end():]
        for addition in ADDED_DISH_PATTERN.finditer(trailing_messages):
            amount = _number(addition.group(1))
            if addition.group(2) == "素菜":
                vegetable_count += amount
            elif addition.group(2) == "荤菜":
                meat_count += amount
        if (
            dish_count is not None
            and meat_count + vegetable_count != dish_count
            and dish_count % (meat_count + vegetable_count) == 0
            and ("比例" in messages or "一荤两素" in messages)
        ):
            multiplier = dish_count // (meat_count + vegetable_count)
            meat_count *= multiplier
            vegetable_count *= multiplier
        return meat_count, vegetable_count

    if re.search(r"(?:1\s*[:：]\s*2|一比二)", messages) and dish_count:
        if dish_count % 3 == 0:
            return dish_count // 3, dish_count * 2 // 3
    return None


def _explicit_cooking_minimum(messages: str) -> int | None:
    if "四道不同做法" in messages:
        return 4
    match = COOKING_MINIMUM_PATTERN.search(messages)
    if match:
        return _number(match.group(1))
    if "不要全是" in messages or re.search(r"别[^。；\n]{0,20}只", messages):
        return 2
    return None


def semantic_consistency_errors(scenario: Scenario) -> tuple[str, ...]:
    errors: list[str] = []
    messages = "\n".join(scenario.messages)
    missing_goals = REQUIRED_DISCLOSED_GOALS.get(scenario.scenario_id, set()) - set(
        scenario.persona.health_goals
    )
    if missing_goals:
        errors.append(f"persona missing disclosed goals: {sorted(missing_goals)}")

    required_facts = REQUIRED_VISIBLE_FACTS.get(scenario.scenario_id, set())
    missing_facts = {
        fact for fact in required_facts if fact not in messages
    }
    if missing_facts:
        errors.append(f"messages missing visible health facts: {sorted(missing_facts)}")

    expected = scenario.expectation
    explicit_dish_count = _explicit_final_dish_count(messages)
    if explicit_dish_count != expected.dish_count:
        errors.append(
            f"dish_count request is {explicit_dish_count}, expectation is {expected.dish_count}"
        )

    explicit_structure = _explicit_meat_vegetable_counts(
        messages,
        explicit_dish_count,
    )
    expected_structure = (
        (expected.meat_count, expected.vegetable_count)
        if expected.meat_count is not None or expected.vegetable_count is not None
        else None
    )
    if explicit_structure != expected_structure:
        errors.append(
            f"meat/vegetable request is {explicit_structure}, expectation is {expected_structure}"
        )

    explicit_cooking_minimum = _explicit_cooking_minimum(messages)
    if explicit_cooking_minimum != expected.minimum_cooking_methods:
        errors.append(
            "cooking-method request is "
            f"{explicit_cooking_minimum}, expectation is {expected.minimum_cooking_methods}"
        )

    missing_forbidden_terms = [
        term for term in expected.forbidden_terms if term not in messages
    ]
    if missing_forbidden_terms:
        errors.append(
            f"messages missing forbidden terms: {sorted(missing_forbidden_terms)}"
        )

    clarification_requested = any(
        marker in messages for marker in CLARIFICATION_MARKERS
    )
    if clarification_requested != expected.clarification_required:
        errors.append(
            "clarification request is "
            f"{clarification_requested}, expectation is {expected.clarification_required}"
        )

    preservation_requested = any(
        marker in messages for marker in PRESERVATION_MARKERS
    )
    if preservation_requested != expected.preserve_unaffected:
        errors.append(
            "preservation request is "
            f"{preservation_requested}, expectation is {expected.preserve_unaffected}"
        )
    return tuple(errors)


class EvaluationCorpusIntegrityTests(unittest.TestCase):
    def assert_known_gap_item(
        self,
        item: object,
        seed_ids: set[str],
    ) -> tuple[str, str]:
        self.assertIs(type(item), dict)
        assert type(item) is dict
        self.assertEqual(set(item), set(KNOWN_GAP_FIELDS))
        for field in KNOWN_GAP_FIELDS:
            self.assertIs(type(item[field]), str)
            self.assertTrue(item[field].strip())
        self.assertIn(item["scenario_id"], seed_ids)
        self.assertRegex(item["violation_code"], STABLE_VIOLATION_CODE)
        self.assertIn(
            item["violation_code"],
            ALLOWED_PHASE1_KNOWN_GAP_CODES,
        )
        return item["scenario_id"], item["violation_code"]

    def test_advanced_seed_corpus_parses_and_covers_every_bucket(self) -> None:
        self.assertTrue(ADVANCED_SEEDS_PATH.is_file())
        seeds = load_scenario_directory(SEEDS_ROOT)

        self.assertGreaterEqual(len(seeds), 30)
        self.assertEqual(
            {scenario.persona.primary_bucket for scenario in seeds},
            set(PRIMARY_BUCKETS),
        )

    def test_all_public_scenario_ids_are_globally_unique(self) -> None:
        scenarios: list[Scenario] = []
        for directory in PUBLIC_SCENARIO_ROOTS:
            scenarios.extend(
                load_scenario_directory(
                    directory,
                    require_files=directory != REGRESSIONS_ROOT,
                )
            )
        counts = Counter(scenario.scenario_id for scenario in scenarios)

        self.assertEqual(
            sorted(scenario_id for scenario_id, count in counts.items() if count > 1),
            [],
        )

    def test_phase1_known_gaps_are_narrow_exact_and_reference_seeds(self) -> None:
        seeds = load_scenario_directory(SEEDS_ROOT)
        seed_ids = {scenario.scenario_id for scenario in seeds}
        payload = load_json_file(KNOWN_GAPS_PATH)

        self.assertIs(type(payload), list)
        pairs: list[tuple[str, str]] = []
        for index, item in enumerate(payload):
            with self.subTest(index=index):
                pairs.append(self.assert_known_gap_item(item, seed_ids))

        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(len(PHASE1_KNOWN_GAP_BASELINE), 26)
        self.assertEqual(frozenset(pairs), PHASE1_KNOWN_GAP_BASELINE)

    def test_known_gap_validation_rejects_empty_owner_phase(self) -> None:
        seeds = load_scenario_directory(SEEDS_ROOT)
        seed_ids = {scenario.scenario_id for scenario in seeds}
        payload = load_json_file(KNOWN_GAPS_PATH)
        mutated = {**payload[0], "owner_phase": ""}

        with self.assertRaises(AssertionError):
            self.assert_known_gap_item(mutated, seed_ids)

    def test_each_known_gap_pair_is_a_current_blocking_violation(self) -> None:
        seeds = {
            scenario.scenario_id: scenario
            for scenario in load_scenario_directory(SEEDS_ROOT)
        }
        payload = load_json_file(KNOWN_GAPS_PATH)
        expected_by_scenario: dict[str, set[str]] = {}
        for item in payload:
            expected_by_scenario.setdefault(item["scenario_id"], set()).add(
                item["violation_code"]
            )

        actual_by_scenario: dict[str, set[str]] = {}
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.agent.session_store",
            SessionStore(),
        ):
            runner = EvaluationRunner(
                Path(directory),
                seed=20260715,
                known_gaps_path=None,
                commit_sha="known-gap-evidence-test",
            )
            for scenario_id in sorted(expected_by_scenario):
                result, _, _, _ = runner._execute(
                    seeds[scenario_id],
                    seeds[scenario_id].messages,
                )
                actual_by_scenario[scenario_id] = {
                    violation.code
                    for violation in result.violations
                    if violation.severity == "blocking"
                }

        missing_pairs = sorted(
            (scenario_id, code)
            for scenario_id, expected_codes in expected_by_scenario.items()
            for code in expected_codes - actual_by_scenario[scenario_id]
        )
        self.assertEqual(missing_pairs, [])

    def test_retired_known_gap_pairs_no_longer_block(self) -> None:
        seeds = {
            scenario.scenario_id: scenario
            for scenario in load_scenario_directory(SEEDS_ROOT)
        }
        with tempfile.TemporaryDirectory() as directory:
            runner = EvaluationRunner(
                Path(directory),
                seed=20260715,
                known_gaps_path=None,
                commit_sha="retired-known-gap-evidence-test",
            )
            for scenario_id, retired_code in RETIRED_PHASE1_KNOWN_GAPS:
                result, _, _, _ = runner._execute(
                    seeds[scenario_id],
                    seeds[scenario_id].messages,
                )
                self.assertNotIn(
                    retired_code,
                    {violation.code for violation in result.violations},
                )

    def test_advanced_seed_labels_match_visible_request_semantics(self) -> None:
        for scenario in load_scenario_directory(SEEDS_ROOT):
            with self.subTest(scenario_id=scenario.scenario_id):
                self.assertEqual(semantic_consistency_errors(scenario), ())

    def test_seed_health_ground_truth_is_extractable_from_messages(self) -> None:
        for scenario in load_scenario_directory(SEEDS_ROOT):
            with self.subTest(scenario_id=scenario.scenario_id):
                constraints = extract_constraints(list(scenario.messages))
                inferred = constraints.inferred_profile
                self.assertEqual(
                    set(inferred.get("special_groups", [])),
                    set(scenario.persona.special_groups),
                )
                self.assertEqual(
                    set(expand_terms(inferred.get("allergens", []))),
                    set(expand_terms(list(scenario.persona.allergens))),
                )
                self.assertEqual(
                    set(inferred.get("health_goals", [])),
                    set(scenario.persona.health_goals),
                )

    def test_four_distinct_methods_requires_minimum_four(self) -> None:
        scenario = load_scenario_directory(SEEDS_ROOT)[0]
        mutated = replace(
            scenario,
            scenario_id="semantic-cooking-method-mutation",
            messages=("请给四道不同做法的菜。",),
            expectation=replace(
                scenario.expectation,
                minimum_cooking_methods=3,
            ),
        )

        self.assertIn(
            "cooking-method request is 4, expectation is 3",
            semantic_consistency_errors(mutated),
        )

    def test_explicit_dish_count_mutation_is_rejected(self) -> None:
        scenario = next(
            item
            for item in load_scenario_directory(SEEDS_ROOT)
            if item.scenario_id == "adv-healthy-ratio-003"
        )
        mutated = replace(
            scenario,
            messages=(scenario.messages[0].replace("六道菜", "五道菜"),),
        )

        self.assertIn(
            "dish_count request is 5, expectation is 6",
            semantic_consistency_errors(mutated),
        )

    def test_explicit_ratio_mutation_is_rejected(self) -> None:
        scenario = next(
            item
            for item in load_scenario_directory(SEEDS_ROOT)
            if item.scenario_id == "adv-special-pregnancy-allergy-ratio-020"
        )
        mutated = replace(
            scenario,
            messages=(scenario.messages[0].replace("两荤四素", "五荤一素"),),
        )

        self.assertIn(
            "meat/vegetable request is (5, 1), expectation is (2, 4)",
            semantic_consistency_errors(mutated),
        )

    def test_removing_request_semantics_is_rejected(self) -> None:
        scenario = next(
            item
            for item in load_scenario_directory(SEEDS_ROOT)
            if item.scenario_id == "adv-high-risk-minimal-revision-028"
        )
        mutated = replace(scenario, messages=("请给我一份菜单。",))

        errors = semantic_consistency_errors(mutated)

        self.assertIn(
            f"dish_count request is None, expectation is {scenario.expectation.dish_count}",
            errors,
        )
        self.assertTrue(
            any(error.startswith("messages missing forbidden terms:") for error in errors)
        )
        self.assertIn(
            "clarification request is False, expectation is True",
            errors,
        )
        self.assertIn(
            "preservation request is False, expectation is True",
            errors,
        )

    def test_json_loader_rejects_duplicate_keys(self) -> None:
        duplicate_key_payload = '{"scenario_id": "first", "scenario_id": "second"}'

        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            strict_json_loads(duplicate_key_payload)

    def test_regression_scenarios_participate_in_global_id_check(self) -> None:
        self.assertIn(REGRESSIONS_ROOT, PUBLIC_SCENARIO_ROOTS)
        self.assertNotIn(KNOWN_GAPS_PATH.parent, PUBLIC_SCENARIO_ROOTS)

        scenario = load_scenario_directory(SEEDS_ROOT)[0]
        with tempfile.TemporaryDirectory() as directory:
            regressions = Path(directory) / "regressions"
            regressions.mkdir()
            (regressions / "duplicate.json").write_text(
                json.dumps(scenario.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )

            scenarios = (
                *load_scenario_directory(SEEDS_ROOT),
                *load_scenario_directory(regressions),
            )
            counts = Counter(item.scenario_id for item in scenarios)

        self.assertEqual(counts[scenario.scenario_id], 2)

    def test_public_holdout_has_exactly_one_sample_per_bucket(self) -> None:
        self.assertTrue(PUBLIC_HOLDOUT_PATH.is_file())
        holdout = load_scenario_directory(HOLDOUT_ROOT)

        self.assertEqual(len(holdout), 5)
        self.assertEqual(
            Counter(scenario.persona.primary_bucket for scenario in holdout),
            Counter({bucket: 1 for bucket in PRIMARY_BUCKETS}),
        )

    def test_quick_and_daily_loading_excludes_public_holdout(self) -> None:
        seeds = load_scenario_directory(SEEDS_ROOT)
        holdout_ids = {
            scenario.scenario_id for scenario in load_scenario_directory(HOLDOUT_ROOT)
        }

        with tempfile.TemporaryDirectory() as directory:
            for mode in ("quick", "daily"):
                generated = replace(
                    seeds[0],
                    scenario_id=f"generated-normal-load-{mode}",
                )
                runner = EvaluationRunner(
                    Path(directory) / mode,
                    seed=20260715,
                    mode=mode,
                    official_recipes={},
                    corpus_root=CORPUS_ROOT,
                    generate_fn=lambda _seed, count, item=generated: tuple(
                        replace(item, scenario_id=f"{item.scenario_id}-{index}")
                        for index in range(count)
                    ),
                    commit_sha="corpus-integrity-test",
                )

                selected = runner._load_scenarios(10)

                self.assertTrue(holdout_ids.isdisjoint(item.scenario_id for item in selected))
                self.assertNotIn(
                    HOLDOUT_ROOT.resolve(),
                    {path.resolve() for path, _, _ in runner._corpus_directories()},
                )


if __name__ == "__main__":
    unittest.main()
