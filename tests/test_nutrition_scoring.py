from __future__ import annotations

import unittest

from app.models import Constraints, Recipe
from app.nutrition_scoring import score_table_nutrition
from app.nutrition_targets import build_nutrition_targets
from app.planner import plan_meal


def nutrition(
    *,
    kcal: float,
    protein: float,
    fat: float,
    carbs: float,
    fiber: float,
    sugar: float,
    sodium: float,
    confidence: str = "high",
) -> dict:
    return {
        "nutrients": {
            "kcal": kcal,
            "protein_g": protein,
            "fat_g": fat,
            "carbohydrate_g": carbs,
            "fiber_g": fiber,
            "sugar_g": sugar,
            "sodium_mg": sodium,
        },
        "confidence": {"level": confidence, "score": 0.95 if confidence == "high" else 0.5, "reasons": []},
        "estimated_servings": 2.0,
        "missing_ingredients": [],
    }


class NutritionTargetTests(unittest.TestCase):
    def test_hypertension_uses_stricter_sodium_limit(self) -> None:
        normal = build_nutrition_targets(Constraints(meal="晚餐"), None)
        hypertension = build_nutrition_targets(
            Constraints(meal="晚餐", inferred_profile={"special_groups": ["高血压"]}),
            None,
        )

        self.assertLess(hypertension["sodium_mg"]["max"], normal["sodium_mg"]["max"])

    def test_muscle_gain_raises_protein_minimum(self) -> None:
        normal = build_nutrition_targets(Constraints(meal="晚餐"), None)
        muscle = build_nutrition_targets(Constraints(meal="晚餐", health_goals=["增肌"]), None)

        self.assertGreater(muscle["protein_g"]["min"], normal["protein_g"]["min"])


class TableNutritionScoringTests(unittest.TestCase):
    def test_sums_table_and_divides_per_person(self) -> None:
        dishes = [
            nutrition(kcal=400, protein=30, fat=10, carbs=50, fiber=6, sugar=5, sodium=500),
            nutrition(kcal=600, protein=40, fat=20, carbs=70, fiber=10, sugar=8, sodium=700),
        ]

        result = score_table_nutrition(dishes, Constraints(meal="晚餐", people_count=2), None)

        self.assertEqual(result["table_total"]["kcal"], 1000)
        self.assertEqual(result["per_person"]["kcal"], 500)
        self.assertEqual(result["per_person"]["protein_g"], 35)
        self.assertEqual(result["people_count"], 2)

    def test_hypertension_flags_high_sodium(self) -> None:
        dishes = [nutrition(kcal=600, protein=35, fat=20, carbs=70, fiber=10, sugar=8, sodium=900)]
        constraints = Constraints(
            meal="晚餐",
            people_count=1,
            inferred_profile={"special_groups": ["高血压"]},
        )

        result = score_table_nutrition(dishes, constraints, None)

        self.assertEqual(result["components"]["sodium_mg"]["status"], "high")
        self.assertLess(result["score"], 80)

    def test_reports_macro_energy_ratios(self) -> None:
        dishes = [nutrition(kcal=600, protein=30, fat=20, carbs=75, fiber=10, sugar=8, sodium=500)]

        result = score_table_nutrition(dishes, Constraints(meal="晚餐", people_count=1), None)

        ratios = result["macro_energy_ratios"]
        self.assertAlmostEqual(ratios["protein"], 0.2, places=2)
        self.assertAlmostEqual(ratios["fat"], 0.3, places=2)
        self.assertAlmostEqual(ratios["carbohydrate"], 0.5, places=2)

    def test_low_confidence_cannot_claim_balanced(self) -> None:
        dishes = [
            nutrition(
                kcal=600,
                protein=30,
                fat=20,
                carbs=75,
                fiber=10,
                sugar=8,
                sodium=500,
                confidence="low",
            )
        ]

        result = score_table_nutrition(dishes, Constraints(meal="晚餐", people_count=1), None)

        self.assertEqual(result["confidence"]["level"], "low")
        self.assertNotEqual(result["assessment"], "balanced")


class PlannerNutritionIntegrationTests(unittest.TestCase):
    def test_returns_nutrition_fields_and_prefers_lower_sodium_for_hypertension(self) -> None:
        recipes = [
            Recipe(1, "咸味鸡胸", "鸡胸肉200g；盐3g", "煎熟", ["高蛋白"]),
            Recipe(2, "原味鸡胸", "鸡胸肉200g", "蒸熟", ["高蛋白"]),
        ]
        constraints = Constraints(
            requested_dish_count=1,
            health_goals=["增肌", "降压"],
            inferred_profile={"special_groups": ["高血压"]},
        )

        result = plan_meal(recipes, constraints, None)

        self.assertEqual(result["menu"][0]["id"], 2)
        self.assertIn("nutrition", result["menu"][0])
        self.assertIn("table_total", result["nutrition"])
        self.assertIn("score", result["nutrition_score"])
        self.assertIn("level", result["confidence"])

    def test_unknown_nutrition_is_not_treated_as_zero_sodium_advantage(self) -> None:
        recipes = [
            Recipe(1, "未知酱鸡", "神秘鸡肉200g", "蒸熟", ["高蛋白"]),
            Recipe(2, "原味鸡胸", "鸡胸肉200g", "蒸熟", ["高蛋白"]),
        ]
        constraints = Constraints(
            requested_dish_count=1,
            health_goals=["降压"],
            inferred_profile={"special_groups": ["高血压"]},
        )

        result = plan_meal(recipes, constraints, None)

        self.assertEqual(result["menu"][0]["id"], 2)


if __name__ == "__main__":
    unittest.main()
