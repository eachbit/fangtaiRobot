from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from app.models import Recipe
from app.recipe_features import RecipeFeatures, analyze_recipe, classify_recipe, is_breakfast_friendly


def recipe(
    name: str,
    ingredients: str,
    steps: str = "",
    labels: list[str] | None = None,
) -> Recipe:
    return Recipe(1, name, ingredients, steps, labels or [])


class RecipeStructureTests(unittest.TestCase):
    def test_recipe_features_is_frozen(self) -> None:
        features = RecipeFeatures("meat", "meat", "hot", "蒸")

        with self.assertRaises(FrozenInstanceError):
            features.temperature = "cold"  # type: ignore[misc]

    def test_analyzes_fish_leafy_vegetable_and_egg_protein_styles(self) -> None:
        cases = [
            (recipe("清蒸鲈鱼", "鲈鱼500g", "蒸熟"), "meat"),
            (recipe("清炒菠菜", "菠菜300g", "炒熟"), "vegetable"),
            (recipe("蒸鸡蛋羹", "鸡蛋2个", "蒸熟"), "other"),
        ]

        for item, expected in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, expected)

    def test_animal_ingredient_wins_over_tofu_or_mushroom(self) -> None:
        cases = [
            recipe("肉末豆腐", "豆腐300g；猪肉末80g", "炖煮入味"),
            recipe("菌菇乳鸽", "香菇100g；乳鸽1只", "炖熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "meat")

    def test_tofu_and_mushroom_without_animal_ingredients_are_vegetable(self) -> None:
        cases = [
            recipe("家常豆腐", "豆腐300g；青椒50g", "炖煮入味"),
            recipe("清炒菌菇", "香菇100g；口蘑100g", "炒熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "vegetable")

    def test_common_soy_products_are_vegetable(self) -> None:
        cases = [
            recipe("凉拌腐竹", "腐竹300g", "焯水后拌匀"),
            recipe("清炒千张", "千张300g", "炒熟"),
            recipe("红烧豆泡", "豆泡300g", "炖煮入味"),
            recipe("豆制品拼盘", "豆制品300g", "切片装盘"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "vegetable")

    def test_animal_named_plants_and_mushrooms_are_vegetable(self) -> None:
        cases = [
            recipe("清炒鸡毛菜", "鸡毛菜300g", "炒熟"),
            recipe("凉拌牛蒡", "牛蒡300g", "焯水后拌匀"),
            recipe("鱼腥草沙拉", "鱼腥草300g", "拌匀"),
            recipe("清炒鸡腿菇", "鸡腿菇300g", "炒熟"),
            recipe("清炒猪肚菇", "猪肚菇300g", "炒熟"),
            recipe("香煎素鸡", "素鸡300g", "煎熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "vegetable")

    def test_real_animal_ingredients_override_disambiguated_plants(self) -> None:
        cases = [
            recipe("鸡毛菜炒鸡胸肉", "鸡毛菜200g；鸡胸肉200g", "炒熟"),
            recipe("腐竹牛肉", "腐竹200g；牛肉200g", "炖熟"),
            recipe("鱼腥草炖鱼肉", "鱼腥草100g；鱼肉300g", "炖熟"),
            recipe("猪肚菇炒猪肚", "猪肚菇200g；猪肚200g", "炒熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "meat")

    def test_common_leaf_root_gourd_and_legume_ingredients_are_vegetable(self) -> None:
        cases = [
            recipe("拍黄瓜", "黄瓜300g；蒜", "拍碎后拌匀"),
            recipe("清炒莴笋", "莴笋300g", "炒熟"),
            recipe("萝卜炖汤", "白萝卜300g；水", "炖熟"),
            recipe("清蒸南瓜", "南瓜300g", "蒸熟"),
            recipe("蒸贝贝南瓜", "贝贝南瓜300g", "蒸熟"),
            recipe("毛豆炒豆角", "毛豆100g；豆角200g", "炒熟"),
            recipe("清炒牛肝菌", "牛肝菌300g", "炒熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "vegetable")

    def test_common_shellfish_ingredients_are_meat(self) -> None:
        cases = [
            recipe("白灼蛤蜊", "蛤蜊500g", "白灼至熟"),
            recipe("爆炒花蛤", "花蛤500g", "炒熟"),
            recipe("辣炒花甲", "花甲500g", "炒熟"),
            recipe("蒜蓉扇贝", "扇贝6只", "蒸熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "meat")

    def test_animal_named_condiments_do_not_make_vegetables_meat(self) -> None:
        cases = [
            recipe("鱼露拍黄瓜", "黄瓜300g；鱼露5g", "拍碎后拌匀"),
            recipe("蚝油生菜", "生菜300g；蚝油10g", "炒熟"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, "vegetable")

    def test_ingredients_prevent_fish_flavored_eggplant_false_positive(self) -> None:
        item = recipe("鱼香茄子", "茄子400g；蒜；醋；豆瓣酱", "炒熟")

        features = analyze_recipe(item)

        self.assertEqual(features.protein_style, "vegetable")
        self.assertEqual(features.category, "meat")

    def test_name_has_priority_over_conflicting_steps_for_cooking_method(self) -> None:
        item = recipe("清蒸鲈鱼", "鲈鱼500g", "先炒香配料，再炖至入味")

        features = analyze_recipe(item)

        self.assertEqual(features.cooking_method, "蒸")
        self.assertEqual(features.temperature, "hot")

    def test_steps_are_used_when_name_has_no_cooking_method(self) -> None:
        item = recipe("蒜蓉生菜", "生菜300g；蒜", "下锅炒熟")

        features = analyze_recipe(item)

        self.assertEqual(features.cooking_method, "炒")
        self.assertEqual(features.temperature, "hot")

    def test_cold_mix_precedes_generic_mix_and_sets_cold_temperature(self) -> None:
        item = recipe("凉拌菠菜", "菠菜300g", "焯水后拌匀")

        features = analyze_recipe(item)

        self.assertEqual(features.cooking_method, "凉拌")
        self.assertEqual(features.temperature, "cold")

    def test_generic_stirring_does_not_override_explicit_hot_method_in_steps(self) -> None:
        for method in ["蒸", "炒", "煮"]:
            with self.subTest(method=method):
                features = analyze_recipe(
                    recipe("家常豆腐", "豆腐300g", f"搅拌均匀后{method}熟")
                )
                self.assertEqual(features.cooking_method, method)
                self.assertEqual(features.temperature, "hot")

    def test_baked_aliases_map_to_baked_method(self) -> None:
        for name in ["芝士焗豆腐", "烧烤菌菇"]:
            with self.subTest(name=name):
                features = analyze_recipe(recipe(name, "豆腐；口蘑", "加热至熟"))
                self.assertEqual(features.cooking_method, "烤")
                self.assertEqual(features.temperature, "hot")

    def test_cold_plate_can_have_unknown_method(self) -> None:
        features = analyze_recipe(recipe("时蔬冷盘", "黄瓜；番茄", "切片摆盘"))

        self.assertEqual(features.temperature, "cold")
        self.assertEqual(features.cooking_method, "unknown")

    def test_unknown_structure_is_not_invented(self) -> None:
        features = analyze_recipe(recipe("神秘拼盘", "特制原料", "装盘即可"))

        self.assertEqual(features.protein_style, "other")
        self.assertEqual(features.temperature, "unknown")
        self.assertEqual(features.cooking_method, "unknown")

    def test_name_is_used_for_protein_style_only_when_ingredients_are_empty(self) -> None:
        features = analyze_recipe(recipe("清蒸鲈鱼", "", "蒸熟"))

        self.assertEqual(features.protein_style, "meat")

    def test_classify_recipe_delegates_to_analyze_recipe(self) -> None:
        item = recipe("任意菜", "任意食材")
        expected = RecipeFeatures("staple", "other", "unknown", "unknown")

        with patch("app.recipe_features.analyze_recipe", return_value=expected) as analyze:
            self.assertEqual(classify_recipe(item), "staple")

        analyze.assert_called_once_with(item)

    def test_legacy_category_and_breakfast_behavior_remain_compatible(self) -> None:
        cases = [
            (recipe("红糖点心", "红糖；面粉"), "dessert"),
            (recipe("番茄汤", "番茄；水"), "soup"),
            (recipe("米饭", "大米"), "staple"),
            (recipe("鱼香茄子", "茄子"), "meat"),
            (recipe("清炒菠菜", "菠菜"), "vegetable"),
            (recipe("调味汁", "盐；生抽"), "other"),
        ]

        for item, expected in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).category, expected)
                self.assertEqual(classify_recipe(item), expected)

        self.assertTrue(is_breakfast_friendly(recipe("水煮蛋", "鸡蛋", labels=["早餐"])))
        self.assertTrue(is_breakfast_friendly(recipe("燕麦粥", "燕麦；水")))


if __name__ == "__main__":
    unittest.main()
