from __future__ import annotations

import unittest

from app.constraints import extract_constraints, infer_profile_from_text
from app.food_terms import expand_terms


class ConstraintExtractionTests(unittest.TestCase):
    def test_explicit_health_and_goal_lists_are_authoritative(self) -> None:
        text = (
            "我目前的健康情况是高血压、高尿酸；"
            "饮食目标是降血压、控糖、护肾；最近空腹血糖5.7。"
        )

        profile = infer_profile_from_text(text)

        self.assertEqual(profile["special_groups"], ["高血压", "高尿酸"])
        self.assertEqual(profile["health_goals"], ["降血压", "控糖", "护肾"])

    def test_explicit_health_list_preserves_supported_extended_conditions(self) -> None:
        profile = infer_profile_from_text(
            "我的健康情况是高血脂、肾功能异常，目标是调节血脂、护肾。"
        )

        self.assertEqual(profile["special_groups"], ["高血脂", "肾功能异常"])
        self.assertEqual(profile["health_goals"], ["调节血脂", "护肾"])

    def test_negated_condition_is_not_inferred_and_goal_keeps_original_wording(self) -> None:
        profile = infer_profile_from_text("我没有高血压，但目标是控制体重。")

        self.assertEqual(profile["special_groups"], [])
        self.assertEqual(profile["health_goals"], ["控制体重"])

    def test_fallback_goal_inference_remains_available_without_explicit_list(self) -> None:
        profile = infer_profile_from_text("最近想减脂，也需要补钙。")

        self.assertEqual(profile["health_goals"], ["减脂", "补钙"])

    def test_allergen_negation_and_conjunction_have_local_scope(self) -> None:
        self.assertEqual(infer_profile_from_text("我不对牛奶过敏")["allergens"], [])
        self.assertEqual(
            infer_profile_from_text("我对花生和虾过敏")["allergens"],
            ["花生", "虾"],
        )
        self.assertEqual(
            infer_profile_from_text("我目前没有已知食物过敏")["allergens"],
            [],
        )

    def test_expanded_egg_aliases_are_idempotent(self) -> None:
        once = expand_terms(["鸡蛋"])

        self.assertEqual(expand_terms(once), once)

    def test_constraints_and_inferred_profile_share_disclosed_health_values(self) -> None:
        messages = [
            "我目前的健康情况是高血压、高尿酸；饮食目标是降血压、护肾；",
            "我对花生和虾过敏，推荐四道菜。",
        ]

        constraints = extract_constraints(messages)

        self.assertEqual(
            constraints.inferred_profile["special_groups"],
            ["高血压", "高尿酸"],
        )
        self.assertEqual(constraints.health_goals, ["降血压", "护肾"])
        self.assertEqual(
            set(expand_terms(constraints.inferred_profile["allergens"])),
            set(expand_terms(["花生", "虾"])),
        )
        self.assertEqual(
            set(expand_terms(constraints.allergens)),
            set(expand_terms(["花生", "虾"])),
        )

    def test_constraints_do_not_add_canonical_aliases_to_explicit_goals(self) -> None:
        constraints = extract_constraints(["饮食目标是减肥、补钙。"])

        self.assertEqual(constraints.inferred_profile["health_goals"], ["减肥", "补钙"])
        self.assertEqual(constraints.health_goals, ["减肥", "补钙"])

    def test_disclosed_lists_stop_before_comma_delimited_explanation(self) -> None:
        profile = infer_profile_from_text(
            "健康情况是高血压、高尿酸，最近空腹血糖5.7。"
            "饮食目标是降压、护肾，晚餐推荐四道菜。"
        )

        self.assertEqual(profile["special_groups"], ["高血压", "高尿酸"])
        self.assertEqual(profile["health_goals"], ["降压", "护肾"])

    def test_fallback_aliases_remain_available_without_explicit_goal_list(self) -> None:
        profile = infer_profile_from_text("最近想控制体重，也想补气血。")

        self.assertEqual(profile["health_goals"], ["减脂", "补铁"])

    def test_latest_explicit_goal_list_replaces_earlier_disclosure(self) -> None:
        constraints = extract_constraints(
            ["饮食目标是减脂。", "调整一下，饮食目标是增肌。"]
        )

        self.assertEqual(constraints.inferred_profile["health_goals"], ["增肌"])
        self.assertEqual(constraints.health_goals, ["增肌"])

    def test_allergen_negation_after_food_keeps_later_positive_allergen(self) -> None:
        self.assertEqual(
            infer_profile_from_text("我对花生不过敏但虾过敏")["allergens"],
            ["虾"],
        )
        self.assertEqual(
            infer_profile_from_text("我对花生过敏但不对虾过敏")["allergens"],
            ["花生"],
        )

    def test_disclosed_lists_reject_narrative_and_numeric_tail_fields(self) -> None:
        examples = (
            (
                "健康情况是高血压，平时喜欢番茄炒蛋，晚餐推荐四道菜。",
                ["高血压"],
                [],
            ),
            (
                "健康情况是高血压，年龄52岁，目标是降压。",
                ["高血压"],
                ["降压"],
            ),
            (
                "饮食目标是控糖，预算50元，推荐四道菜。",
                [],
                ["控糖"],
            ),
            (
                "健康情况是高血压 饮食目标是降压",
                ["高血压"],
                ["降压"],
            ),
        )
        for text, groups, goals in examples:
            with self.subTest(text=text):
                profile = infer_profile_from_text(text)
                self.assertEqual(profile["special_groups"], groups)
                self.assertEqual(profile["health_goals"], goals)

    def test_additive_disclosures_merge_while_replacements_override(self) -> None:
        additive = extract_constraints(
            ["饮食目标是减脂。", "另外饮食目标是补钙。"]
        )
        additive_groups = extract_constraints(
            ["健康情况是高血压。", "另外健康情况是高尿酸。"]
        )

        self.assertEqual(additive.health_goals, ["减脂", "补钙"])
        self.assertEqual(
            additive_groups.inferred_profile["special_groups"],
            ["高血压", "高尿酸"],
        )

    def test_negation_scope_covers_conjoined_conditions_and_goals(self) -> None:
        for text in ("我没有高血压和高血糖。", "我没有高血压、高血糖。"):
            with self.subTest(text=text):
                self.assertEqual(infer_profile_from_text(text)["special_groups"], [])

        self.assertEqual(infer_profile_from_text("我不需要控糖。")["health_goals"], [])
        self.assertEqual(
            infer_profile_from_text("我没有被诊断出高血压。")["special_groups"],
            [],
        )

    def test_natural_allergen_suffix_extracts_only_food_items(self) -> None:
        examples = (
            ("高血糖、高尿酸并且海鲜过敏。", set(expand_terms(["海鲜"]))),
            ("孕16周且芒果过敏。", {"芒果"}),
            ("原来的四道菜和坚果过敏约束都保留。", set()),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                actual = set(
                    expand_terms(infer_profile_from_text(text)["allergens"])
                )
                self.assertEqual(actual, expected)

    def test_secondary_child_and_negated_label_are_not_primary_conditions(self) -> None:
        self.assertNotIn(
            "儿童",
            infer_profile_from_text("三个人吃饭，孩子只吃甜口。")[
                "special_groups"
            ],
        )
        self.assertNotIn(
            "高血压",
            infer_profile_from_text("请不要误加高血压标签。")[
                "special_groups"
            ],
        )

    def test_dish_count_tracks_additions_and_ignores_reference_counts(self) -> None:
        additions = extract_constraints(
            ["先推荐四道菜，两荤两素。", "再多加两道素菜，原来的四道菜都保留。"]
        )
        replacement = extract_constraints(
            ["先给四道菜。", "只换第二道，其他三道菜保留。"]
        )

        self.assertEqual(additions.requested_dish_count, 6)
        self.assertEqual(additions.requested_meat_count, 2)
        self.assertEqual(additions.requested_vegetable_count, 4)
        self.assertEqual(replacement.requested_dish_count, 4)

    def test_later_absolute_count_cancels_earlier_structure_increment(self) -> None:
        constraints = extract_constraints(
            [
                "先推荐四道菜，两荤两素。",
                "再加两道素菜。",
                "还是改成四道菜。",
            ]
        )

        self.assertEqual(constraints.requested_dish_count, 4)
        self.assertEqual(constraints.requested_meat_count, 2)
        self.assertEqual(constraints.requested_vegetable_count, 2)
        self.assertIs(constraints.clarification_required, False)

    def test_clock_time_does_not_override_explicit_structure(self) -> None:
        constraints = extract_constraints(["晚餐六道菜，两荤四素，18:30开饭。"])

        self.assertEqual(constraints.requested_meat_count, 2)
        self.assertEqual(constraints.requested_vegetable_count, 4)
        self.assertIs(constraints.clarification_required, False)

    def test_dish_count_ignores_prohibited_merged_dish_phrase(self) -> None:
        constraints = extract_constraints(
            ["一家三口吃四道菜，约束不能直接混成一道菜。"]
        )

        self.assertEqual(constraints.requested_dish_count, 4)

    def test_dish_count_ignores_pronoun_position_and_process_references(self) -> None:
        examples = (
            ("先给四道菜；再加两道素菜，这两道不要辣。", 6),
            ("先给六道菜；只换后两道，前四道保留。", 6),
            ("先给四道菜；菜品经过两道筛选。", 4),
            ("先给四道菜；把第 2 道菜换掉，其他三道保留。", 4),
            ("高尿酸要兼顾，六道低盐低嘌呤菜。", 6),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(
                    extract_constraints([text]).requested_dish_count,
                    expected,
                )

    def test_explicit_allergens_allow_trailing_explanation_and_multiple_phrases(self) -> None:
        examples = (
            ("我对花生过敏请避开。", ["花生"]),
            ("我对花生过敏很严重。", ["花生"]),
            ("我对花生过敏且对虾过敏。", ["花生", "虾"]),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(
                    infer_profile_from_text(text)["allergens"],
                    expected,
                )

    def test_do_not_add_ingredient_phrase_removes_cooking_verb(self) -> None:
        constraints = extract_constraints(["这顿不要放香菜，也别加花生。"])

        self.assertIn("香菜", constraints.avoid_ingredients)
        self.assertIn("花生", constraints.avoid_ingredients)
        self.assertNotIn("放香菜", constraints.avoid_ingredients)
        self.assertNotIn("加花生", constraints.avoid_ingredients)

    def test_do_not_add_conjoined_ingredients_creates_separate_hard_constraints(self) -> None:
        constraints = extract_constraints(["不要放香菜和葱，也别加花生、芝麻。"])

        for ingredient in ("香菜", "葱", "花生", "芝麻"):
            with self.subTest(ingredient=ingredient):
                self.assertIn(ingredient, constraints.avoid_ingredients)
        self.assertNotIn("香菜和葱", constraints.avoid_ingredients)

    def test_avoid_extra_sugar_becomes_hard_ingredient_constraint(self) -> None:
        constraints = extract_constraints(["避免海鲜和额外糖。"])

        self.assertIn("糖", constraints.avoid_ingredients)


if __name__ == "__main__":
    unittest.main()
