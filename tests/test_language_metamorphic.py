from __future__ import annotations

import inspect
import random
import unittest
from dataclasses import FrozenInstanceError

import tests.evaluation.language_mutator as language_mutator
from tests.evaluation.language_mutator import (
    LANGUAGE_VARIANT_VERSION,
    LANGUAGE_VARIANTS,
    LanguageVariant,
    select_variant,
    variants_for_intent,
)


class LanguageMetamorphicTests(unittest.TestCase):
    def test_required_phrases_have_static_expected_intents(self) -> None:
        expected = {
            "多来几个素菜": "structure_ratio",
            "少整点荤的": "structure_ratio",
            "肉菜太多了": "structure_ratio",
            "荤素一比二": "structure_ratio",
            "别全是蒸的": "cooking_diversity",
            "我没有高血压": "negative_expression",
            "不要把素菜换掉": "relative_revision",
        }
        actual = {item.text: item.expected_intent for item in LANGUAGE_VARIANTS}

        self.assertEqual(LANGUAGE_VARIANT_VERSION, "1.0")
        for text, intent in expected.items():
            self.assertEqual(actual[text], intent)

    def test_variants_are_frozen_and_returned_in_stable_immutable_order(self) -> None:
        variants = variants_for_intent("structure_ratio")

        self.assertIsInstance(LANGUAGE_VARIANTS, tuple)
        self.assertIsInstance(variants, tuple)
        self.assertEqual(variants, variants_for_intent("structure_ratio"))
        self.assertEqual(
            [item.variant_id for item in variants],
            sorted(item.variant_id for item in variants),
        )
        with self.assertRaises(FrozenInstanceError):
            variants[0].text = "changed"  # type: ignore[misc]

    def test_unknown_intent_has_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown intent 'not-an-intent'"):
            variants_for_intent("not-an-intent")

    def test_seeded_selection_is_repeatable_and_does_not_touch_global_random(self) -> None:
        random.seed(2026)
        before = random.getstate()

        first = select_variant("structure_ratio", seed=7, slot=3)
        second = select_variant("structure_ratio", seed=7, slot=3)

        self.assertEqual(first, second)
        self.assertEqual(before, random.getstate())

    def test_synonymous_family_preserves_structure_ground_truth(self) -> None:
        family = tuple(
            item
            for item in LANGUAGE_VARIANTS
            if item.family_id == "more-vegetables"
        )

        self.assertGreaterEqual(len(family), 3)
        self.assertEqual({item.expected_intent for item in family}, {"structure_ratio"})
        self.assertEqual(len({item.ground_truth for item in family}), 1)
        self.assertEqual(
            dict(family[0].ground_truth),
            {"direction": "more_vegetable", "preserve_unaffected": True},
        )

    def test_exact_ratio_does_not_infer_dish_counts_without_context(self) -> None:
        variant = next(item for item in LANGUAGE_VARIANTS if item.text == "荤素一比二")

        self.assertEqual(
            dict(variant.ground_truth),
            {"ratio_meat": 1, "ratio_vegetable": 2},
        )

    def test_non_steamed_request_does_not_infer_three_methods(self) -> None:
        variant = next(item for item in LANGUAGE_VARIANTS if item.text == "别全是蒸的")

        self.assertEqual(
            dict(variant.ground_truth),
            {"requires_non_steamed": True},
        )

    def test_expected_labels_are_not_generated_by_production_parser(self) -> None:
        source = inspect.getsource(language_mutator)

        self.assertNotIn("extract_constraints", source)
        self.assertTrue(
            all(isinstance(item, LanguageVariant) for item in LANGUAGE_VARIANTS)
        )


if __name__ == "__main__":
    unittest.main()
