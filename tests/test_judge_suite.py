from __future__ import annotations

import json
import random
import time
import unittest
from pathlib import Path

from app.agent import get_recipes, recommend, recommend_with_session
from app.food_terms import contains_food_term


CASES = Path(__file__).resolve().parent / "judge_cases" / "scenarios.json"


class JudgeInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.official = {recipe.id: recipe for recipe in get_recipes()}
        cls.cases = json.loads(CASES.read_text(encoding="utf-8"))

    def test_fixed_judge_scenarios(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["name"]):
                start = time.perf_counter()
                result = recommend(None, case["messages"])
                elapsed = time.perf_counter() - start
                self._assert_result_invariants(result)
                self.assertLess(elapsed, 2.0)
                self.assertEqual(len(result["menu"]), case["count"])
                text = " ".join(item["name"] + item["ingredients"] + " ".join(item["labels"]) for item in result["menu"])
                for forbidden in case.get("forbid", []):
                    self.assertFalse(contains_food_term(text, forbidden), f"命中禁忌 {forbidden}: {text}")

    def test_seeded_constraint_combinations(self) -> None:
        random.seed(20260713)
        allergens = ["花生", "虾", "牛奶"]
        meals = ["早餐", "午餐", "晚餐"]
        goals = ["减脂", "增肌", "控糖", "降压"]
        for index in range(24):
            allergen = random.choice(allergens)
            meal = random.choice(meals)
            goal = random.choice(goals)
            count = random.randint(2, 5)
            result = recommend(None, [f"我对{allergen}过敏，{meal}想要{goal}，推荐{count}道菜"])
            with self.subTest(index=index, allergen=allergen, meal=meal, goal=goal):
                self._assert_result_invariants(result)
                self.assertEqual(len(result["menu"]), count)
                menu_text = " ".join(item["name"] + item["ingredients"] for item in result["menu"])
                self.assertFalse(contains_food_term(menu_text, allergen))

    def test_session_minimal_change_and_full_history_are_equivalent(self) -> None:
        initial = "晚餐推荐4道菜，想吃高蛋白"
        follow_up = "不要虾，其他菜保留"
        first = recommend_with_session(None, [initial])
        revised = recommend_with_session(
            None,
            [follow_up],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )
        replayed = recommend_with_session(None, [initial, follow_up])

        self._assert_result_invariants(revised)
        self.assertEqual([x["id"] for x in revised["menu"]], [x["id"] for x in replayed["menu"]])
        self.assertEqual(revised["changes"], replayed["changes"])
        self.assertEqual(
            revised["changes"]["change_count"],
            sum(a["id"] != b["id"] for a, b in zip(first["menu"], revised["menu"])),
        )

    def _assert_result_invariants(self, result: dict) -> None:
        ids = [item["id"] for item in result["menu"]]
        self.assertEqual(len(ids), len(set(ids)), f"重复菜谱: {ids}")
        for item in result["menu"]:
            official = self.official[item["id"]]
            self.assertEqual(item["name"], official.name)
            self.assertEqual(item["ingredients"], official.ingredients)
            self.assertIn("nutrition", item)
        summed = {
            key: round(sum(item["nutrition"]["nutrients"][key] for item in result["menu"]), 2)
            for key in result["nutrition"]["table_total"]
        }
        self.assertEqual(result["nutrition"]["table_total"], summed)
        if result["confidence"]["level"] == "low":
            self.assertNotEqual(result["nutrition_score"]["assessment"], "balanced")


if __name__ == "__main__":
    unittest.main()
