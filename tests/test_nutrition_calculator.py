from __future__ import annotations

import unittest

from app.models import Recipe
from app.nutrition_calculator import calculate_recipe_nutrition
from app.nutrition_repository import NUTRIENT_KEYS, NutritionRepository


class NutritionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = NutritionRepository()

    def test_resolves_canonical_name_and_exact_alias(self) -> None:
        tomato = self.repository.resolve("西红柿")

        self.assertIsNotNone(tomato)
        self.assertEqual(tomato.canonical_name, "番茄")
        self.assertTrue(tomato.source)
        self.assertTrue(tomato.source_id)
        self.assertEqual(set(tomato.nutrients_per_100g), set(NUTRIENT_KEYS))

    def test_does_not_use_fuzzy_substring_matching(self) -> None:
        self.assertIsNone(self.repository.resolve("西红柿味薯片"))


class RecipeNutritionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = NutritionRepository()

    def test_calculates_explicit_weight_from_per_100g_values(self) -> None:
        recipe = Recipe(1, "烤鸡胸", "鸡胸肉200g", "烤熟", ["高蛋白"])

        result = calculate_recipe_nutrition(recipe, self.repository)
        food = self.repository.resolve("鸡胸肉")

        for key in NUTRIENT_KEYS:
            expected = round(food.nutrients_per_100g[key] * 2, 2)
            self.assertEqual(result["nutrients"][key], expected)
        self.assertEqual(result["ingredients"][0]["source_id"], food.source_id)
        self.assertEqual(result["ingredients"][0]["amount_source"], "explicit")

    def test_includes_salt_and_soy_sauce_sodium(self) -> None:
        recipe = Recipe(2, "调味汁", "盐少许；生抽少许", "混合", [])

        result = calculate_recipe_nutrition(recipe, self.repository)
        salt = self.repository.resolve("盐")
        soy = self.repository.resolve("生抽")
        expected = round(salt.nutrients_per_100g["sodium_mg"] * 0.03 + soy.nutrients_per_100g["sodium_mg"] * 0.1, 2)

        self.assertEqual(result["nutrients"]["sodium_mg"], expected)
        self.assertGreater(result["nutrients"]["sodium_mg"], 1000)

    def test_aggregates_known_ingredients_and_reports_missing(self) -> None:
        recipe = Recipe(
            3,
            "鸡胸番茄",
            "鸡胸肉200g；西红柿100g；盐2g；神秘香草10g",
            "炒熟",
            ["晚餐"],
        )

        result = calculate_recipe_nutrition(recipe, self.repository)

        expected_kcal = sum(
            self.repository.resolve(name).nutrients_per_100g["kcal"] * grams / 100
            for name, grams in [("鸡胸肉", 200), ("番茄", 100), ("盐", 2)]
        )
        self.assertEqual(result["nutrients"]["kcal"], round(expected_kcal, 2))
        self.assertIn("神秘香草", result["missing_ingredients"])
        self.assertEqual(result["ingredient_match_coverage"], 0.75)
        self.assertEqual(result["weight_coverage"], 1.0)
        self.assertEqual(result["confidence"]["level"], "medium")

    def test_estimates_servings_from_recipe_category_and_weight(self) -> None:
        recipe = Recipe(4, "清炒番茄", "番茄400g", "炒熟", ["蔬菜"])

        result = calculate_recipe_nutrition(recipe, self.repository)

        self.assertEqual(result["estimated_servings"], 2.0)
        self.assertEqual(result["serving_range"], [1, 3])

    def test_empty_ingredients_are_safe_and_low_confidence(self) -> None:
        recipe = Recipe(5, "空菜谱", "", "", [])

        result = calculate_recipe_nutrition(recipe, self.repository)

        self.assertEqual(result["nutrients"], {key: 0.0 for key in NUTRIENT_KEYS})
        self.assertEqual(result["estimated_servings"], 1.0)
        self.assertEqual(result["ingredient_match_coverage"], 0.0)
        self.assertEqual(result["weight_coverage"], 0.0)
        self.assertEqual(result["confidence"]["level"], "low")


if __name__ == "__main__":
    unittest.main()
