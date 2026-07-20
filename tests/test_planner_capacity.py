from __future__ import annotations

import unittest

from app.agent import recommend


class PlannerCapacityTests(unittest.TestCase):
    def test_explicit_do_not_add_ingredient_is_absent_from_menu(self) -> None:
        result = recommend(
            None,
            ["我有高血压，推荐六道晚餐，另外本次新增忌口：不要放香菜。"],
        )

        menu_text = " ".join(
            f"{item['name']} {item['ingredients']}" for item in result["menu"]
        )
        self.assertEqual(len(result["menu"]), 6)
        self.assertNotIn("香菜", menu_text)

    def test_extra_sugar_avoidance_is_absent_from_menu(self) -> None:
        result = recommend(
            None,
            ["健康情况是高血糖，推荐六道菜，避免额外糖。"],
        )

        menu_text = " ".join(
            f"{item['name']} {item['ingredients']}" for item in result["menu"]
        )
        self.assertEqual(len(result["menu"]), 6)
        self.assertNotIn("糖", menu_text)

    def test_large_menu_survives_multi_person_allergy_filtering(self) -> None:
        result = recommend(
            None,
            [
                "健康情况是高血压、高血糖；我对虾过敏；"
                "饮食目标是降压、控糖；三个人吃六道菜，父亲想吃海鲜，"
                "孩子只吃甜口。安全约束冲突时先澄清，不得拿虾折中。"
            ],
        )

        self.assertEqual(result["constraints"]["requested_dish_count"], 6)
        self.assertEqual(len(result["menu"]), 6)

    def test_four_dishes_remain_available_after_milk_allergy_filtering(self) -> None:
        result = recommend(
            None,
            [
                "健康情况是备孕；我对牛奶过敏；"
                "饮食目标是补气血、调理月经、养脾胃、补铁、补钙；"
                "四道菜要兼顾铁和钙来源，但不能用牛奶或把甜味当成补营养。"
            ],
        )

        self.assertEqual(result["constraints"]["requested_dish_count"], 4)
        self.assertEqual(len(result["menu"]), 4)

    def test_four_dishes_remain_available_for_pregnancy_conflict(self) -> None:
        result = recommend(
            None,
            [
                "健康情况是孕妇；饮食目标是控制体重、补铁；"
                "一家三口吃四道菜：孕28周的我需要清淡全熟，"
                "配偶坚持重辣半熟牛肉。先澄清是否分餐，不能直接混成一道菜。"
            ],
        )

        self.assertEqual(result["constraints"]["requested_dish_count"], 4)
        self.assertEqual(len(result["menu"]), 4)


if __name__ == "__main__":
    unittest.main()
