from __future__ import annotations

import unittest

from app.ingredient_parser import ParsedIngredient, parse_ingredient_segment, parse_ingredients


class ParseIngredientSegmentTests(unittest.TestCase):
    def test_parses_explicit_grams(self) -> None:
        parsed = parse_ingredient_segment("鸡胸肉200克")

        self.assertEqual(parsed.raw_name, "鸡胸肉")
        self.assertEqual(parsed.canonical_name, "鸡胸肉")
        self.assertEqual(parsed.amount, 200)
        self.assertEqual(parsed.unit, "g")
        self.assertEqual(parsed.grams, 200)
        self.assertEqual(parsed.amount_source, "explicit")
        self.assertGreaterEqual(parsed.confidence, 0.9)

    def test_parses_kilograms_and_ascii_g(self) -> None:
        kilogram = parse_ingredient_segment("面粉1.5kg")
        ascii_grams = parse_ingredient_segment("红枣20g")

        self.assertEqual(kilogram.grams, 1500)
        self.assertEqual(kilogram.unit, "kg")
        self.assertEqual(ascii_grams.grams, 20)
        self.assertEqual(ascii_grams.unit, "g")

    def test_parses_milliliters_with_density_note(self) -> None:
        parsed = parse_ingredient_segment("牛奶250毫升")

        self.assertEqual(parsed.amount, 250)
        self.assertEqual(parsed.unit, "ml")
        self.assertEqual(parsed.grams, 250)
        self.assertEqual(parsed.amount_source, "explicit")
        self.assertLess(parsed.confidence, 0.9)
        self.assertIn("1g/ml", parsed.notes)

    def test_converts_egg_count_and_leaves_general_count_unknown(self) -> None:
        eggs = parse_ingredient_segment("鸡蛋2个")
        tomato = parse_ingredient_segment("西红柿半个")

        self.assertEqual(eggs.amount, 2)
        self.assertEqual(eggs.unit, "个")
        self.assertEqual(eggs.grams, 100)
        self.assertEqual(eggs.amount_source, "explicit")
        self.assertLessEqual(eggs.confidence, 0.75)
        self.assertEqual(tomato.amount, 0.5)
        self.assertEqual(tomato.canonical_name, "番茄")
        self.assertIsNone(tomato.grams)
        self.assertEqual(tomato.amount_source, "unknown")

    def test_converts_generic_egg_count(self) -> None:
        parsed = parse_ingredient_segment("蛋2个")

        self.assertEqual(parsed.canonical_name, "蛋")
        self.assertEqual(parsed.amount, 2)
        self.assertEqual(parsed.unit, "个")
        self.assertEqual(parsed.grams, 100)
        self.assertEqual(parsed.amount_source, "explicit")

    def test_normalizes_garlic_cloves_without_estimating_weight(self) -> None:
        parsed = parse_ingredient_segment("大蒜2瓣")

        self.assertEqual(parsed.canonical_name, "蒜")
        self.assertEqual(parsed.amount, 2)
        self.assertEqual(parsed.unit, "瓣")
        self.assertIsNone(parsed.grams)
        self.assertEqual(parsed.amount_source, "unknown")

    def test_normalizes_garlic_fuzzy_amount(self) -> None:
        parsed = parse_ingredient_segment("大蒜少许")

        self.assertEqual(parsed.canonical_name, "蒜")
        self.assertEqual(parsed.amount_source, "default")
        self.assertIsNone(parsed.grams)

    def test_converts_spoons_bowls_and_fractions(self) -> None:
        cases = {
            "盐半勺": (0.5, "勺", 5),
            "油1/2大勺": (0.5, "大勺", 7.5),
            "糖1/4小勺": (0.25, "小勺", 1.25),
            "水半碗": (0.5, "碗", 100),
        }

        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_ingredient_segment(text)
                self.assertEqual((parsed.amount, parsed.unit, parsed.grams), expected)
                self.assertEqual(parsed.amount_source, "explicit")
                self.assertLessEqual(parsed.confidence, 0.75)

    def test_uses_defaults_for_fuzzy_amounts(self) -> None:
        cases = {
            "盐少许": 3,
            "食用油适量": 10,
            "白砂糖适量": 5,
            "生抽少许": 10,
        }

        for text, grams in cases.items():
            with self.subTest(text=text):
                parsed = parse_ingredient_segment(text)
                self.assertIsNone(parsed.amount)
                self.assertIsNone(parsed.unit)
                self.assertEqual(parsed.grams, grams)
                self.assertEqual(parsed.amount_source, "default")
                self.assertLessEqual(parsed.confidence, 0.45)

    def test_keeps_unmapped_fuzzy_and_missing_amounts_unknown(self) -> None:
        fuzzy = parse_ingredient_segment("葱花适量")
        missing = parse_ingredient_segment("香菜")

        self.assertIsNone(fuzzy.grams)
        self.assertEqual(fuzzy.amount_source, "default")
        self.assertIsNone(missing.amount)
        self.assertIsNone(missing.unit)
        self.assertIsNone(missing.grams)
        self.assertEqual(missing.amount_source, "unknown")

    def test_normalizes_aliases_and_preserves_preparation_notes(self) -> None:
        tomato = parse_ingredient_segment("西红柿100g")
        ginger = parse_ingredient_segment("姜丝25g")
        scallion = parse_ingredient_segment("葱末5克")
        garlic = parse_ingredient_segment("蒜片3克")

        self.assertEqual(tomato.canonical_name, "番茄")
        self.assertEqual(ginger.canonical_name, "姜")
        self.assertIn("姜丝", ginger.notes)
        self.assertEqual(scallion.canonical_name, "葱")
        self.assertIn("葱末", scallion.notes)
        self.assertEqual(garlic.canonical_name, "蒜")
        self.assertIn("蒜片", garlic.notes)

    def test_to_dict_exposes_stable_json_shape(self) -> None:
        parsed = parse_ingredient_segment("鸡蛋1个")

        self.assertIsInstance(parsed, ParsedIngredient)
        self.assertEqual(
            set(parsed.to_dict()),
            {
                "raw_text",
                "raw_name",
                "canonical_name",
                "amount",
                "unit",
                "grams",
                "amount_source",
                "confidence",
                "notes",
            },
        )


class ParseIngredientsTests(unittest.TestCase):
    def test_splits_groups_semicolons_and_newlines(self) -> None:
        text = (
            "主料：鸡胸肉200克；西红柿100克\n"
            "辅料:鸡蛋1个; 调料：盐少许；A料：姜丝5g；B料：生抽10毫升"
        )

        parsed = parse_ingredients(text)

        self.assertEqual(
            [item.canonical_name for item in parsed],
            ["鸡胸肉", "番茄", "鸡蛋", "盐", "姜", "生抽"],
        )
        self.assertEqual([item.grams for item in parsed], [200, 100, 50, 3, 5, 10])

    def test_does_not_split_on_chinese_commas_inside_notes(self) -> None:
        parsed = parse_ingredients("主料：鸡胸肉200克（去皮，切块）；盐2克")

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].raw_name, "鸡胸肉")
        self.assertIn("去皮，切块", parsed[0].notes)


if __name__ == "__main__":
    unittest.main()
