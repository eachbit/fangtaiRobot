from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.models import Recipe
from tests.evaluation.deterministic_oracle import evaluate_result
from tests.evaluation.schemas import HealthPersona, MenuExpectation, Scenario


def recipe(
    recipe_id: int,
    name: str,
    ingredients: str,
    steps: str,
    labels: list[str] | None = None,
) -> Recipe:
    return Recipe(recipe_id, name, ingredients, steps, labels or [])


def balanced_recipes() -> dict[int, Recipe]:
    items = (
        recipe(1, "清蒸鲈鱼", "鲈鱼500克；生姜10克", "上锅蒸熟"),
        recipe(2, "萝卜炖牛肉", "牛肉300克；白萝卜200克", "小火炖熟"),
        recipe(3, "清炒菠菜", "菠菜300克；蒜10克", "下锅炒熟"),
        recipe(4, "凉拌黄瓜", "黄瓜300克；醋10克", "焯水后凉拌"),
        recipe(5, "清蒸南瓜", "南瓜300克", "上锅蒸熟"),
        recipe(6, "番茄煮豆腐", "番茄200克；豆腐200克", "加水煮熟"),
    )
    return {item.id: item for item in items}


def scenario(
    scenario_id: str = "ratio-gold",
    *,
    persona: HealthPersona | None = None,
    expectation: MenuExpectation | None = None,
) -> Scenario:
    return Scenario(
        scenario_id,
        persona or HealthPersona("healthy-1", "healthy"),
        ("请推荐菜单",),
        expectation or MenuExpectation(),
        1,
    )


def response_for(recipes: list[Recipe]) -> dict[str, object]:
    return {
        "menu": [
            {"id": item.id, "name": item.name, "ingredients": item.ingredients}
            for item in recipes
        ],
        "constraints": {
            "inferred_profile": {"special_groups": [], "allergens": []},
            "health_goals": [],
        },
    }


def codes(result: object) -> list[str]:
    return [violation.code for violation in result.violations]  # type: ignore[attr-defined]


class DeterministicOracleTests(unittest.TestCase):
    def test_correct_two_meat_four_vegetable_menu_has_no_structure_violation(self) -> None:
        official = balanced_recipes()
        case = scenario(
            expectation=MenuExpectation(
                dish_count=6,
                meat_count=2,
                vegetable_count=4,
                minimum_cooking_methods=4,
            )
        )

        result = evaluate_result(case, response_for(list(official.values())), official)

        self.assertTrue(result.passed)
        self.assertFalse(any(code.startswith("structure.") for code in codes(result)))

    def test_wrong_vegetable_count_has_stable_violation_code(self) -> None:
        official = balanced_recipes()
        egg = recipe(7, "蒸鸡蛋羹", "鸡蛋2个；水100克", "上锅蒸熟")
        official[egg.id] = egg
        selected = [official[index] for index in (1, 2, 3, 4, 5, 7)]
        case = scenario(
            expectation=MenuExpectation(dish_count=6, meat_count=2, vegetable_count=4)
        )

        result = evaluate_result(case, response_for(selected), official)

        self.assertIn("structure.vegetable_count", codes(result))

    def test_response_schema_errors_return_violation_instead_of_raising(self) -> None:
        official = balanced_recipes()
        valid = response_for([official[1]])
        malformed = (
            None,
            {"menu": "not-a-list"},
            {**valid, "menu": ["not-a-mapping"]},
            {**valid, "menu": [{"id": True, "name": "x", "ingredients": "y"}]},
            {**valid, "menu": [{"id": 1, "name": 7, "ingredients": "y"}]},
            {**valid, "menu": [{"id": 1, "name": "x", "ingredients": None}]},
        )

        for response in malformed:
            with self.subTest(response=response):
                result = evaluate_result(scenario(), response, official)
                self.assertEqual(codes(result), ["response.schema"])
                self.assertFalse(result.passed)

    def test_fake_recipe_id_is_rejected(self) -> None:
        response = response_for([recipe(999, "伪造菜", "伪造食材", "伪造步骤")])

        result = evaluate_result(scenario(), response, balanced_recipes())

        self.assertIn("recipe.unknown_id", codes(result))

    def test_official_name_and_ingredients_mismatches_have_distinct_codes(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1]])
        response["menu"][0]["name"] = "冒充名称"  # type: ignore[index]
        response["menu"][0]["ingredients"] = "冒充食材"  # type: ignore[index]

        result = evaluate_result(scenario(), response, official)

        self.assertEqual(
            [
                code
                for code in codes(result)
                if code.startswith("recipe.")
            ],
            ["recipe.name_mismatch", "recipe.ingredients_mismatch"],
        )

    def test_duplicate_recipe_id_is_rejected(self) -> None:
        official = balanced_recipes()

        result = evaluate_result(
            scenario(),
            response_for([official[1], official[1]]),
            official,
        )

        self.assertIn("recipe.duplicate_id", codes(result))

    def test_forbidden_term_uses_official_recipe_fields(self) -> None:
        item = recipe(
            11,
            "菠菜拌花生",
            "菠菜300克；花生30克",
            "拌匀",
            ["含坚果"],
        )
        response = response_for([item])
        response["menu"][0]["labels"] = []  # type: ignore[index]
        case = scenario(expectation=MenuExpectation(forbidden_terms=("花生",)))

        result = evaluate_result(case, response, {item.id: item})

        violation = next(
            violation
            for violation in result.violations
            if violation.code == "constraint.forbidden_term"
        )
        self.assertEqual(violation.to_dict()["evidence"], {"term": "花生", "recipe_id": 11})

    def test_dish_count_is_checked(self) -> None:
        official = balanced_recipes()
        case = scenario(expectation=MenuExpectation(dish_count=6))

        result = evaluate_result(case, response_for([official[1]]), official)

        self.assertIn("structure.dish_count", codes(result))

    def test_unknown_recipe_structure_emits_coverage_and_unproven_checks(self) -> None:
        item = recipe(20, "神秘拼盘", "特制原料", "装盘即可")
        case = scenario(
            expectation=MenuExpectation(
                dish_count=1,
                meat_count=0,
                vegetable_count=0,
                minimum_cooking_methods=1,
            )
        )

        result = evaluate_result(case, response_for([item]), {item.id: item})

        self.assertEqual(
            codes(result),
            [
                "coverage.recipe_structure",
                "structure.meat_count",
                "structure.vegetable_count",
                "structure.cooking_diversity",
            ],
        )
        self.assertEqual(result.violations[0].severity, "soft_review")
        self.assertFalse(result.passed)

    def test_negative_health_statement_false_positive_is_rejected(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1]])
        response["constraints"]["inferred_profile"]["special_groups"] = [  # type: ignore[index]
            "高血压"
        ]
        case = Scenario(
            "negative-health",
            HealthPersona("healthy-negative", "healthy"),
            ("我没有高血压",),
            MenuExpectation(),
            1,
        )

        result = evaluate_result(case, response, official)

        self.assertIn("health.special_groups_false_positive", codes(result))
        self.assertNotIn("health.special_groups_false_negative", codes(result))

    def test_all_health_false_positive_and_false_negative_codes_are_stable(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1]])
        response["constraints"] = {
            "inferred_profile": {
                "special_groups": ["孕妇"],
                "allergens": ["牛奶"],
            },
            "health_goals": ["减脂"],
        }
        persona = HealthPersona(
            "health-ground-truth",
            "multi_condition",
            special_groups=("高血压",),
            allergens=("花生",),
            health_goals=("控糖",),
        )

        result = evaluate_result(
            scenario("health-diff", persona=persona),
            response,
            official,
        )

        self.assertEqual(
            [code for code in codes(result) if code.startswith("health.")],
            [
                "health.special_groups_false_positive",
                "health.special_groups_false_negative",
                "health.allergens_false_positive",
                "health.allergens_false_negative",
                "health.goals_false_positive",
                "health.goals_false_negative",
            ],
        )

    def test_health_fields_must_be_present_string_arrays(self) -> None:
        official = balanced_recipes()
        valid = response_for([official[1]])
        malformed = (
            {"menu": valid["menu"]},
            {**valid, "constraints": "not-a-mapping"},
            {**valid, "constraints": {"health_goals": []}},
            {
                **valid,
                "constraints": {"inferred_profile": "bad", "health_goals": []},
            },
            {
                **valid,
                "constraints": {
                    "inferred_profile": {"special_groups": "高血压", "allergens": []},
                    "health_goals": [],
                },
            },
            {
                **valid,
                "constraints": {
                    "inferred_profile": {"special_groups": [], "allergens": []},
                    "health_goals": "控糖",
                },
            },
            {
                **valid,
                "constraints": {
                    "inferred_profile": {"special_groups": [], "allergens": [7]},
                    "health_goals": [],
                },
            },
        )

        for response in malformed:
            with self.subTest(response=response):
                result = evaluate_result(scenario(), response, official)
                self.assertEqual(codes(result), ["response.schema"])
                self.assertFalse(result.passed)

    def test_nutrition_table_total_matches_rounded_menu_sum(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1], official[3]])
        nutrients = (
            {"energy_kcal": 100.105, "protein_g": 10.1},
            {"energy_kcal": 200.105, "protein_g": 20.2},
        )
        for item, values in zip(response["menu"], nutrients, strict=True):  # type: ignore[arg-type]
            item["nutrition"] = {"nutrients": values}
        response["nutrition"] = {
            "table_total": {"energy_kcal": 300.21, "protein_g": 30.3}
        }

        result = evaluate_result(scenario(), response, official)

        self.assertNotIn("nutrition.table_total_mismatch", codes(result))
        self.assertNotIn("response.schema", codes(result))

    def test_nutrition_sum_mismatch_has_stable_violation_code(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1], official[3]])
        for item, energy in zip(response["menu"], (100.0, 200.0), strict=True):  # type: ignore[arg-type]
            item["nutrition"] = {
                "nutrients": {"energy_kcal": energy, "protein_g": 10.0}
            }
        response["nutrition"] = {
            "table_total": {"energy_kcal": 999.0, "protein_g": 20.0}
        }

        result = evaluate_result(scenario(), response, official)

        self.assertIn("nutrition.table_total_mismatch", codes(result))
        self.assertFalse(result.passed)

    def test_nutrition_shape_requires_uniform_string_keys_and_finite_numbers(self) -> None:
        official = balanced_recipes()
        valid = response_for([official[1], official[3]])
        for item in valid["menu"]:  # type: ignore[union-attr]
            item["nutrition"] = {
                "nutrients": {"energy_kcal": 100.0, "protein_g": 10.0}
            }
        valid["nutrition"] = {
            "table_total": {"energy_kcal": 200.0, "protein_g": 20.0}
        }

        missing_item_nutrition = deepcopy(valid)
        del missing_item_nutrition["menu"][0]["nutrition"]
        missing_table_total = deepcopy(valid)
        del missing_table_total["nutrition"]["table_total"]
        inconsistent_keys = deepcopy(valid)
        del inconsistent_keys["menu"][1]["nutrition"]["nutrients"]["protein_g"]
        non_string_key = deepcopy(valid)
        non_string_key["menu"][0]["nutrition"]["nutrients"][7] = 1.0
        malformed = (
            {**deepcopy(valid), "nutrition": "bad"},
            missing_item_nutrition,
            missing_table_total,
            inconsistent_keys,
            non_string_key,
        )
        for invalid_number in (True, float("nan"), float("inf"), float("-inf")):
            bad_number = deepcopy(valid)
            bad_number["menu"][0]["nutrition"]["nutrients"]["energy_kcal"] = invalid_number
            malformed += (bad_number,)

        for response in malformed:
            with self.subTest(response=response):
                result = evaluate_result(scenario(), response, official)
                self.assertEqual(codes(result), ["response.schema"])
                self.assertFalse(result.passed)

    def test_clarification_requirement_must_be_explicitly_satisfied(self) -> None:
        official = balanced_recipes()
        case = scenario(
            "clarification",
            expectation=MenuExpectation(clarification_required=True),
        )
        missing = response_for([official[1]])
        satisfied = response_for([official[1]])
        satisfied["clarification_required"] = True

        failed_result = evaluate_result(case, missing, official)
        passed_result = evaluate_result(case, satisfied, official)

        self.assertIn("dialogue.clarification", codes(failed_result))
        self.assertNotIn("dialogue.clarification", codes(passed_result))

    def test_minimal_change_requires_mode_and_score_card_confirmation(self) -> None:
        official = balanced_recipes()
        case = scenario(
            "minimal-change",
            expectation=MenuExpectation(preserve_unaffected=True),
        )
        valid = response_for([official[1]])
        valid.update(
            {
                "changes": {"mode": "minimal_revision"},
                "score_card": {"minimal_change": True},
            }
        )
        wrong_mode = deepcopy(valid)
        wrong_mode["changes"]["mode"] = "full_revision"
        wrong_score = deepcopy(valid)
        wrong_score["score_card"]["minimal_change"] = False

        for response in (response_for([official[1]]), wrong_mode, wrong_score):
            with self.subTest(response=response):
                result = evaluate_result(case, response, official)
                self.assertIn("dialogue.minimal_change", codes(result))
        self.assertNotIn(
            "dialogue.minimal_change",
            codes(evaluate_result(case, valid, official)),
        )

    def test_elapsed_timeout_threshold_is_strictly_greater_than_15000_ms(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1]])

        at_limit = evaluate_result(scenario(), response, official, elapsed_ms=15000.0)
        over_limit = evaluate_result(scenario(), response, official, elapsed_ms=15000.01)

        self.assertNotIn("performance.response_timeout", codes(at_limit))
        self.assertIn("performance.response_timeout", codes(over_limit))
        self.assertFalse(over_limit.passed)

    def test_invalid_elapsed_values_are_schema_violations_without_exceptions(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1]])

        for elapsed in (-0.1, True, "slow", float("nan"), float("inf")):
            with self.subTest(elapsed=elapsed):
                result = evaluate_result(  # type: ignore[arg-type]
                    scenario(), response, official, elapsed_ms=elapsed
                )
                self.assertIn("response.schema", codes(result))
                self.assertEqual(result.elapsed_ms, 0.0)

        malformed_result = evaluate_result(
            scenario(), None, official, elapsed_ms=float("nan")
        )
        self.assertEqual(codes(malformed_result), ["response.schema"])
        self.assertEqual(malformed_result.elapsed_ms, 0.0)

    def test_exact_known_gap_pair_is_downgraded_but_other_scenario_is_not(self) -> None:
        official = balanced_recipes()
        response = response_for([official[1]])
        exact_case = scenario(
            "known-gap-exact",
            expectation=MenuExpectation(dish_count=2),
        )
        other_case = scenario(
            "known-gap-other",
            expectation=MenuExpectation(dish_count=2),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known-gaps.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "scenario_id": "known-gap-exact",
                            "violation_code": "structure.dish_count",
                            "owner_phase": "phase2",
                            "expires_after_phase": "phase2",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            exact_result = evaluate_result(
                exact_case, response, official, known_gaps_path=path
            )
            other_result = evaluate_result(
                other_case, response, official, known_gaps_path=path
            )

        exact_violation = next(
            item for item in exact_result.violations if item.code == "structure.dish_count"
        )
        other_violation = next(
            item for item in other_result.violations if item.code == "structure.dish_count"
        )
        self.assertEqual(exact_violation.severity, "known_gap")
        self.assertTrue(exact_result.passed)
        self.assertEqual(other_violation.severity, "blocking")
        self.assertFalse(other_result.passed)

    def test_all_hard_error_codes_cannot_be_downgraded(self) -> None:
        official = balanced_recipes()
        base_item = official[1]
        name_mismatch = response_for([base_item])
        name_mismatch["menu"][0]["name"] = "伪造名称"  # type: ignore[index]
        ingredients_mismatch = response_for([base_item])
        ingredients_mismatch["menu"][0]["ingredients"] = "伪造食材"  # type: ignore[index]
        forbidden_item = recipe(30, "花生菠菜", "花生30克；菠菜300克", "下锅炒熟")
        nutrition_mismatch = response_for([base_item])
        nutrition_mismatch["menu"][0]["nutrition"] = {  # type: ignore[index]
            "nutrients": {"energy_kcal": 100.0}
        }
        nutrition_mismatch["nutrition"] = {"table_total": {"energy_kcal": 99.0}}
        cases = (
            ("response.schema", scenario("hard-schema"), None, official, 0.0),
            (
                "recipe.unknown_id",
                scenario("hard-unknown"),
                response_for([recipe(999, "伪造菜", "伪造食材", "伪造步骤")]),
                official,
                0.0,
            ),
            ("recipe.name_mismatch", scenario("hard-name"), name_mismatch, official, 0.0),
            (
                "recipe.ingredients_mismatch",
                scenario("hard-ingredients"),
                ingredients_mismatch,
                official,
                0.0,
            ),
            (
                "recipe.duplicate_id",
                scenario("hard-duplicate"),
                response_for([base_item, base_item]),
                official,
                0.0,
            ),
            (
                "constraint.forbidden_term",
                scenario(
                    "hard-forbidden",
                    expectation=MenuExpectation(forbidden_terms=("花生",)),
                ),
                response_for([forbidden_item]),
                {forbidden_item.id: forbidden_item},
                0.0,
            ),
            (
                "nutrition.table_total_mismatch",
                scenario("hard-nutrition"),
                nutrition_mismatch,
                official,
                0.0,
            ),
            (
                "performance.response_timeout",
                scenario("hard-timeout"),
                response_for([base_item]),
                official,
                15000.01,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known-gaps.json"
            for code, case, response, recipes, elapsed in cases:
                with self.subTest(code=code):
                    path.write_text(
                        json.dumps(
                            [
                                {
                                    "scenario_id": case.scenario_id,
                                    "violation_code": code,
                                    "owner_phase": "phase2",
                                    "expires_after_phase": "phase2",
                                }
                            ]
                        ),
                        encoding="utf-8",
                    )
                    result = evaluate_result(
                        case,
                        response,
                        recipes,
                        elapsed_ms=elapsed,
                        known_gaps_path=path,
                    )
                    target = next(item for item in result.violations if item.code == code)
                    self.assertEqual(target.severity, "blocking")
                    self.assertFalse(result.passed)

    def test_missing_known_gap_file_is_treated_as_empty(self) -> None:
        official = balanced_recipes()
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_result(
                scenario(),
                response_for([official[1]]),
                official,
                known_gaps_path=Path(directory) / "missing.json",
            )

        self.assertTrue(result.passed)
        self.assertEqual(result.violations, ())

    def test_malformed_known_gap_file_fails_closed_with_stable_code(self) -> None:
        official = balanced_recipes()
        malformed_payloads = (
            "not-json",
            json.dumps({}),
            json.dumps([{"scenario_id": "missing-fields"}]),
            json.dumps(
                [
                    {
                        "scenario_id": "extra-field",
                        "violation_code": "structure.dish_count",
                        "owner_phase": "phase2",
                        "expires_after_phase": "phase2",
                        "unexpected": True,
                    }
                ]
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known-gaps.json"
            for payload in malformed_payloads:
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    result = evaluate_result(
                        scenario(),
                        response_for([official[1]]),
                        official,
                        known_gaps_path=path,
                    )
                    self.assertEqual(codes(result), ["evaluation.known_gaps_invalid"])
                    self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
