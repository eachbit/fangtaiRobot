from __future__ import annotations

import unittest

from app.agent import recommend, recommend_with_session
from app.constraints import extract_constraints
from app.models import Recipe
from app.recipe_features import analyze_recipe


def menu_features(result: dict) -> list:
    return [
        analyze_recipe(
            Recipe(
                item["id"],
                item["name"],
                item["ingredients"],
                item["steps"],
                item["labels"],
            )
        )
        for item in result["menu"]
    ]


class AdvancedPlanningTests(unittest.TestCase):
    def test_exact_ratio_is_parsed_and_planned_as_two_meat_four_vegetable(self) -> None:
        messages = ["晚餐推荐六道菜，荤素一比二，也就是两荤四素。"]

        constraints = extract_constraints(messages)
        result = recommend(None, messages)
        features = menu_features(result)

        self.assertEqual(constraints.requested_meat_count, 2)
        self.assertEqual(constraints.requested_vegetable_count, 4)
        self.assertEqual(len(features), 6)
        self.assertEqual(sum(item.protein_style == "meat" for item in features), 2)
        self.assertEqual(
            sum(item.protein_style == "vegetable" for item in features),
            4,
        )

    def test_minimum_cooking_methods_uses_only_provable_methods(self) -> None:
        messages = ["晚餐推荐六道菜，至少用三种不同烹饪方式。"]

        constraints = extract_constraints(messages)
        result = recommend(None, messages)
        features = menu_features(result)

        self.assertEqual(constraints.minimum_cooking_methods, 3)
        self.assertEqual(len(features), 6)
        self.assertNotIn("unknown", {item.cooking_method for item in features})
        self.assertGreaterEqual(len({item.cooking_method for item in features}), 3)

    def test_ambiguous_and_high_risk_requests_explicitly_require_clarification(self) -> None:
        messages = (
            ["推荐六道菜，不过肉菜太多了。"],
            ["这份菜单调整一下。"],
            ["我有些健康信息还需确认，请安排六道晚餐。"],
            ["两人的约束不同，请先澄清这份菜单服务谁。"],
        )

        for value in messages:
            with self.subTest(message=value[0]):
                constraints = extract_constraints(value)
                result = recommend(None, value)
                self.assertTrue(constraints.clarification_required)
                self.assertIs(result["clarification_required"], True)
                self.assertIn("确认", result["answer"])

    def test_clear_exact_request_does_not_require_clarification(self) -> None:
        result = recommend(None, ["晚餐推荐六道菜，明确两荤四素。"])

        self.assertIs(result["clarification_required"], False)

    def test_exact_composition_without_total_infers_complete_table_size(self) -> None:
        result = recommend(None, ["晚餐明确两荤两素。"])
        features = menu_features(result)

        self.assertEqual(result["constraints"]["requested_dish_count"], 4)
        self.assertEqual(len(features), 4)
        self.assertEqual(sum(item.protein_style == "meat" for item in features), 2)
        self.assertEqual(sum(item.protein_style == "vegetable" for item in features), 2)

    def test_real_ambiguous_and_high_risk_phrases_require_clarification(self) -> None:
        messages = (
            "血压165/101、血糖9.8，推荐四道晚餐。",
            "六道菜里素菜多一点、荤菜少一点。",
            "两个人晚饭，一个人想吃辣，一个人一点辣都不想碰，推荐四道菜。",
            "今晚吃啥比较好？",
        )

        for message in messages:
            with self.subTest(message=message):
                result = recommend(None, [message])
                self.assertIs(result["clarification_required"], True)
                self.assertIn("确认", result["answer"])

    def test_conflicting_total_and_composition_keeps_requested_table_size(self) -> None:
        result = recommend(None, ["推荐六道菜，两荤两素。"])

        self.assertIs(result["clarification_required"], True)
        self.assertEqual(len(result["menu"]), 6)
        self.assertTrue(result["answer"].startswith("为确保方案准确"))

    def test_high_constraint_ratio_still_returns_complete_table(self) -> None:
        result = recommend(
            None,
            [
                "饮食目标是增肌、提高爆发力；安排六道菜，"
                "明确按荤素一比二，也就是两荤四素，训练日要有足量蛋白质。"
            ],
        )
        features = menu_features(result)

        self.assertEqual(len(features), 6)
        self.assertEqual(sum(item.protein_style == "meat" for item in features), 2)
        self.assertEqual(sum(item.protein_style == "vegetable" for item in features), 4)

    def test_multi_turn_add_vegetables_preserves_maximum_old_dishes(self) -> None:
        first = recommend_with_session(
            None,
            ["先推荐四道菜，两荤两素。"],
        )
        second = recommend_with_session(
            None,
            ["再多加两道素菜，原来的四道菜都保留。"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )
        features = menu_features(second)

        self.assertEqual(len(features), 6)
        self.assertEqual(sum(item.protein_style == "meat" for item in features), 2)
        self.assertEqual(sum(item.protein_style == "vegetable" for item in features), 4)
        self.assertGreaterEqual(len(second["changes"]["kept_dishes"]), 2)

    def test_reducing_structured_table_keeps_every_compatible_old_dish(self) -> None:
        first = recommend_with_session(None, ["先推荐六道菜，四荤两素。"])
        old_ids = {item["id"] for item in first["menu"]}
        second = recommend_with_session(
            None,
            ["改成四道菜，两荤两素，尽量保留原菜单。"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )
        new_ids = {item["id"] for item in second["menu"]}
        features = menu_features(second)

        self.assertEqual(len(old_ids & new_ids), 4)
        self.assertEqual(sum(item.protein_style == "meat" for item in features), 2)
        self.assertEqual(sum(item.protein_style == "vegetable" for item in features), 2)
        self.assertEqual(second["changes"]["change_count"], 2)


if __name__ == "__main__":
    unittest.main()
