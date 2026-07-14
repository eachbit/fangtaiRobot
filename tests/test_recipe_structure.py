from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from app.data_loader import load_recipes
from app.models import Recipe
from app.recipe_features import RecipeFeatures, analyze_recipe, classify_recipe, is_breakfast_friendly


LEGACY_MEAT_TERMS = ["肉", "猪", "牛", "羊", "鸡", "鸭", "鱼", "虾", "蟹", "鲍", "排骨", "蹄筋"]
LEGACY_VEG_TERMS = ["菜", "蔬", "生菜", "芥蓝", "西兰花", "菠菜", "茄子", "土豆", "番茄", "豆腐", "菌", "菇"]
LEGACY_STAPLE_TERMS = ["饭", "面", "粥", "粉", "饼", "馒头", "包", "饺", "米"]
LEGACY_SOUP_TERMS = ["汤", "羹", "粥"]
LEGACY_DESSERT_TERMS = ["膏", "奶昔", "甜", "糖", "点心", "下午茶", "饮品"]


def recipe(
    name: str,
    ingredients: str,
    steps: str = "",
    labels: list[str] | None = None,
) -> Recipe:
    return Recipe(1, name, ingredients, steps, labels or [])


def legacy_category(item: Recipe) -> str:
    text = f"{item.name} {item.ingredients} {' '.join(item.labels)}"
    if any(term in text for term in LEGACY_DESSERT_TERMS):
        return "dessert"
    if any(term in text for term in LEGACY_SOUP_TERMS):
        return "soup"
    if any(term in text for term in LEGACY_STAPLE_TERMS):
        return "staple"
    if any(term in text for term in LEGACY_MEAT_TERMS):
        return "meat"
    if any(term in text for term in LEGACY_VEG_TERMS):
        return "vegetable"
    return "other"


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

    def test_milk_and_animal_named_plants_do_not_create_false_meat(self) -> None:
        cases = [
            (recipe("水果奶昔", "纯牛奶250克；火龙果250克"), "other"),
            (recipe("芹菜炒蟹味菇", "蟹味菇200g；芹菜200g", "炒熟"), "vegetable"),
            (recipe("鲍汁海鲜菇", "海鲜菇300g；鲍鱼汁10g", "炒熟"), "vegetable"),
            (recipe("牛油果沙拉", "牛油果300g", "切片装盘"), "vegetable"),
            (recipe("凉拌牛蒡", "牛蒡300g", "焯水后拌匀"), "vegetable"),
            (recipe("清炒牛肝菌", "牛肝菌300g", "炒熟"), "vegetable"),
            (recipe("清炒鸡毛菜", "鸡毛菜300g", "炒熟"), "vegetable"),
            (recipe("清炒鸡腿菇", "鸡腿菇300g", "炒熟"), "vegetable"),
            (recipe("清炒猪肚菇", "猪肚菇300g", "炒熟"), "vegetable"),
        ]

        for item, expected in cases:
            with self.subTest(name=item.name):
                self.assertEqual(analyze_recipe(item).protein_style, expected)

    def test_explicit_animal_ingredients_remain_meat(self) -> None:
        ingredients = [
            "猪肉200g",
            "牛肉200g",
            "鸡肉200g",
            "鸡胸肉200g",
            "鸡腿2只",
            "鸭肉200g",
            "鹅肉200g",
            "羊肉200g",
            "鱼肉200g",
            "虾仁200g",
            "蟹肉200g",
            "扇贝200g",
            "蛤蜊200g",
            "花甲200g",
            "乳鸽1只",
            "排骨200g",
            "火腿100g",
            "培根100g",
            "香肠100g",
            "牛小排900g",
            "猪肘子1100g",
            "母鸡1只",
            "麻鸭500g",
            "武昌鱼1条",
            "黑虎虾300g",
            "青口贝300g",
            "肉松100g",
            "猪油渣100g",
            "肉糜50g",
            "猪手1个",
            "切块猪肋排400克",
            "虾头30g；虾尾120g",
            "腊汁肉夹馍2个",
        ]

        for value in ingredients:
            with self.subTest(ingredients=value):
                self.assertEqual(analyze_recipe(recipe("家常菜", value)).protein_style, "meat")

    def test_real_recipes_with_animal_parts_are_meat(self) -> None:
        recipes = {item.id: item for item in load_recipes()}

        for recipe_id in [27, 75, 488, 748, 902]:
            with self.subTest(recipe_id=recipe_id, name=recipes[recipe_id].name):
                self.assertEqual(analyze_recipe(recipes[recipe_id]).protein_style, "meat")

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

    def test_dried_scallop_and_beibei_squash_are_disambiguated(self) -> None:
        dried_scallop = recipe("干贝香菇蒸豆腐", "干贝；香菇；豆腐", "蒸熟")
        squash = recipe("清蒸贝贝南瓜", "贝贝南瓜300g", "蒸熟")

        self.assertEqual(analyze_recipe(dried_scallop).protein_style, "meat")
        self.assertEqual(analyze_recipe(squash).protein_style, "vegetable")

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

    def test_plain_mixing_does_not_imply_a_cold_dish(self) -> None:
        cases = [
            recipe("周黑鸭卤鸭翅", "鸭翅500g；卤料", "卤制入味，最后用刮刀拌匀"),
            recipe("石锅拌饭", "米饭；蔬菜", "装入石锅即可"),
        ]

        for item in cases:
            with self.subTest(name=item.name):
                features = analyze_recipe(item)
                self.assertEqual(features.cooking_method, "unknown")
                self.assertEqual(features.temperature, "unknown")

    def test_device_words_do_not_override_explicit_step_cooking_method(self) -> None:
        item = recipe(
            "山姆会员版麻薯",
            "麻薯预拌粉；牛奶",
            "放入蒸烤箱，选择环风烤模式烤至金黄",
        )

        features = analyze_recipe(item)

        self.assertEqual(features.cooking_method, "烤")
        self.assertEqual(features.temperature, "hot")

    def test_real_recipes_with_equipment_only_have_unknown_method(self) -> None:
        recipes = {item.id: item for item in load_recipes()}

        for recipe_id in [75, 278, 1220]:
            with self.subTest(recipe_id=recipe_id, name=recipes[recipe_id].name):
                features = analyze_recipe(recipes[recipe_id])
                self.assertEqual(features.cooking_method, "unknown")
                self.assertEqual(features.temperature, "unknown")

    def test_real_recipe_steps_select_explicit_primary_cooking_action(self) -> None:
        recipes = {item.id: item for item in load_recipes()}
        expected = {958: "烤", 1540: "煮", 1226: "蒸"}

        for recipe_id, method in expected.items():
            with self.subTest(recipe_id=recipe_id, name=recipes[recipe_id].name):
                features = analyze_recipe(recipes[recipe_id])
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

    def test_all_real_recipes_keep_legacy_category(self) -> None:
        recipes = load_recipes()
        differences = [
            (item.id, legacy_category(item), classify_recipe(item))
            for item in recipes
            if legacy_category(item) != classify_recipe(item)
        ]

        self.assertEqual(len(recipes), 2000)
        self.assertEqual(differences, [])

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
