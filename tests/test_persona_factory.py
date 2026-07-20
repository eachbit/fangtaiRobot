from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import FrozenInstanceError, replace
import math
import unittest

from app.data_loader import (
    CHECKUP_FLOAT_SANITY_MAX,
    CHECKUP_FLOAT_SANITY_MIN,
    DIASTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG,
    DIASTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG,
    SYSTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG,
    SYSTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG,
    URIC_ACID_SANITY_MAX_UMOL_L,
    URIC_ACID_SANITY_MIN_UMOL_L,
    load_users,
)
from app.models import UserProfile
from tests.evaluation.persona_factory import (
    ALLERGEN_OPTIONS,
    CONDITION_GROUPS,
    SPECIAL_GROUPS,
    PersonaDisclosure,
    build_disclosures,
    build_personas,
    classify_primary_bucket,
    persona_from_user,
)
from tests.evaluation.schemas import HealthPersona


BUCKETS = (
    "healthy",
    "single_condition",
    "multi_condition",
    "special_group",
    "high_risk",
)
RATIOS = (20, 25, 30, 15, 10)


def expected_quotas(count: int) -> dict[str, int]:
    raw = [count * ratio / 100 for ratio in RATIOS]
    quotas = [math.floor(value) for value in raw]
    for index in sorted(
        range(len(BUCKETS)), key=lambda item: (raw[item] - quotas[item], -item), reverse=True
    )[: count - sum(quotas)]:
        quotas[index] += 1
    return dict(zip(BUCKETS, quotas, strict=True))


class PersonaFactoryTests(unittest.TestCase):
    def test_classifies_official_profiles_into_fixed_distribution(self):
        users = load_users()
        distribution = Counter(classify_primary_bucket(user) for user in users)
        self.assertEqual(
            {
                "healthy": 10,
                "single_condition": 14,
                "multi_condition": 8,
                "special_group": 18,
                "high_risk": 0,
            },
            {bucket: distribution[bucket] for bucket in BUCKETS},
        )
        self.assertEqual(frozenset({"高血压", "高血糖", "高尿酸"}), CONDITION_GROUPS)
        self.assertEqual(frozenset({"孕妇", "备孕", "哺乳期"}), SPECIAL_GROUPS)

    def test_special_group_wins_and_metrics_never_add_a_bucket(self):
        user = next(user for user in load_users() if user.id == 2)
        self.assertEqual("special_group", classify_primary_bucket(user))

        no_groups = replace(user, special_groups=[], checkup_metrics=next(
            candidate.checkup_metrics for candidate in load_users() if candidate.id == 3
        ))
        self.assertEqual("healthy", classify_primary_bucket(no_groups))

    def test_maps_user_three_with_all_detailed_fields_and_typed_metrics(self):
        user = next(user for user in load_users() if user.id == 3)
        persona = persona_from_user(user)

        self.assertEqual("official-3", persona.persona_id)
        self.assertEqual(3, persona.source_user_id)
        self.assertEqual("multi_condition", persona.primary_bucket)
        self.assertEqual(user.gender, persona.gender)
        self.assertEqual(user.age, persona.age)
        self.assertEqual(user.labor_intensity, persona.labor_intensity)
        self.assertEqual(user.pregnancy_week, persona.pregnancy_week)
        self.assertEqual(user.taste_preference, persona.taste_preference)
        self.assertEqual(tuple(user.special_groups), persona.special_groups)
        self.assertEqual(tuple(user.allergens), persona.allergens)
        self.assertEqual(tuple(user.health_goals), persona.health_goals)
        self.assertEqual((user.height_cm, user.weight_kg, user.bmi), (persona.height_cm, persona.weight_kg, persona.bmi))
        self.assertIsNotNone(persona.checkup_metrics)
        self.assertEqual(user.checkup_metrics.fasting_glucose_mmol_l, persona.checkup_metrics.fasting_glucose_mmol_l)
        self.assertEqual(user.checkup_metrics.systolic_blood_pressure_mm_hg, persona.checkup_metrics.systolic_blood_pressure_mm_hg)
        self.assertEqual(user.checkup_metrics.diastolic_blood_pressure_mm_hg, persona.checkup_metrics.diastolic_blood_pressure_mm_hg)
        self.assertEqual(user.checkup_metrics.total_cholesterol_mmol_l, persona.checkup_metrics.total_cholesterol_mmol_l)
        self.assertEqual(user.checkup_metrics.triglycerides_mmol_l, persona.checkup_metrics.triglycerides_mmol_l)
        self.assertEqual(user.checkup_metrics.ldl_mmol_l, persona.checkup_metrics.ldl_mmol_l)
        self.assertEqual(user.checkup_metrics.hdl_mmol_l, persona.checkup_metrics.hdl_mmol_l)
        self.assertEqual(user.checkup_metrics.uric_acid_umol_l, persona.checkup_metrics.uric_acid_umol_l)

    def test_official_mapping_preserves_all_empty_checkups(self):
        personas = tuple(persona_from_user(user) for user in load_users())
        self.assertEqual(40, sum(persona.checkup_metrics is not None for persona in personas))
        self.assertEqual(10, sum(persona.checkup_metrics is None for persona in personas))

    def test_rejects_counts_smaller_than_ten(self):
        with self.assertRaises(ValueError):
            build_personas(seed=7, count=9)

    def test_hundred_personas_have_exact_ratio_and_prefer_unique_officials(self):
        personas = build_personas(seed=21, count=100)
        self.assertEqual(100, len(personas))
        self.assertEqual(expected_quotas(100), Counter(persona.primary_bucket for persona in personas))
        self.assertEqual(100, len({persona.persona_id for persona in personas}))

        official = [persona for persona in personas if persona.source_user_id is not None]
        self.assertEqual(47, len(official))
        self.assertEqual(47, len({persona.source_user_id for persona in official}))
        self.assertTrue(all(persona.persona_id == f"official-{persona.source_user_id}" for persona in official))
        self.assertTrue(
            all(
                persona.source_user_id is None
                for persona in personas
                if persona.persona_id.startswith("synthetic-")
            )
        )

    def test_any_valid_count_uses_largest_remainder_quotas(self):
        for count in (10, 11, 17, 39, 40, 41, 73):
            personas = build_personas(seed=31, count=count)
            self.assertEqual(count, len(personas))
            self.assertEqual(expected_quotas(count), Counter(persona.primary_bucket for persona in personas))

    def test_seed_is_reproducible_and_changes_synthetic_personas(self):
        first = build_personas(seed=101, count=100)
        self.assertEqual(first, build_personas(seed=101, count=100))
        first_synthetic = tuple(persona for persona in first if persona.source_user_id is None)
        second_synthetic = tuple(
            persona for persona in build_personas(seed=102, count=100) if persona.source_user_id is None
        )
        self.assertNotEqual(first_synthetic, second_synthetic)

    def test_synthetic_measurements_and_optional_metrics_are_valid(self):
        synthetic = [persona for persona in build_personas(seed=52, count=100) if persona.source_user_id is None]
        self.assertGreater(len({persona.special_groups for persona in synthetic}), 3)
        self.assertGreater(len({persona.allergens for persona in synthetic}), 3)
        self.assertGreater(len({persona.health_goals for persona in synthetic}), 3)
        for persona in synthetic:
            self.assertEqual(1, len(f"{persona.height_cm:.1f}".split(".")[1]))
            self.assertEqual(1, len(f"{persona.weight_kg:.1f}".split(".")[1]))
            calculated = round(persona.weight_kg / (persona.height_cm / 100) ** 2, 1)
            self.assertLessEqual(abs(calculated - persona.bmi), 0.05)
            metrics = persona.checkup_metrics
            if metrics is None:
                continue
            for value in (
                metrics.fasting_glucose_mmol_l,
                metrics.total_cholesterol_mmol_l,
                metrics.triglycerides_mmol_l,
                metrics.ldl_mmol_l,
                metrics.hdl_mmol_l,
            ):
                self.assertGreaterEqual(value, CHECKUP_FLOAT_SANITY_MIN)
                self.assertLessEqual(value, CHECKUP_FLOAT_SANITY_MAX)
            self.assertGreaterEqual(metrics.systolic_blood_pressure_mm_hg, SYSTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG)
            self.assertLessEqual(metrics.systolic_blood_pressure_mm_hg, SYSTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG)
            self.assertGreaterEqual(metrics.diastolic_blood_pressure_mm_hg, DIASTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG)
            self.assertLessEqual(metrics.diastolic_blood_pressure_mm_hg, DIASTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG)
            self.assertGreater(metrics.systolic_blood_pressure_mm_hg, metrics.diastolic_blood_pressure_mm_hg)
            self.assertGreaterEqual(metrics.uric_acid_umol_l, URIC_ACID_SANITY_MIN_UMOL_L)
            self.assertLessEqual(metrics.uric_acid_umol_l, URIC_ACID_SANITY_MAX_UMOL_L)

    def test_disclosures_have_stable_negative_coverage_without_mutating_personas(self):
        personas = build_personas(seed=73, count=100)
        disclosures = build_disclosures(personas)
        self.assertEqual(disclosures, build_disclosures(personas))
        self.assertEqual(len(personas), len(disclosures))
        self.assertTrue(all(isinstance(disclosure, PersonaDisclosure) for disclosure in disclosures))
        with self.assertRaises(FrozenInstanceError):
            disclosures[0].message = "changed"

        before = tuple(persona.to_dict() for persona in personas)
        self.assertEqual(before, tuple(persona.to_dict() for persona in personas))
        by_bucket: dict[str, list[PersonaDisclosure]] = defaultdict(list)
        by_id = {persona.persona_id: persona for persona in personas}
        for disclosure in disclosures:
            persona = by_id[disclosure.persona_id]
            by_bucket[persona.primary_bucket].append(disclosure)
            self.assertTrue(disclosure.message)
            self.assertNotIn("确诊", disclosure.message)
            self.assertNotIn("严重", disclosure.message)
            self.assertNotIn("治疗", disclosure.message)
            if disclosure.negative_expression:
                for group in persona.special_groups:
                    self.assertNotIn(f"没有{group}", disclosure.message)
                for allergen in persona.allergens:
                    self.assertNotIn(f"不对{allergen}过敏", disclosure.message)
        for bucket, bucket_disclosures in by_bucket.items():
            self.assertGreaterEqual(
                sum(item.negative_expression for item in bucket_disclosures), math.ceil(len(bucket_disclosures) * 0.2))
        self.assertTrue(any("澄清" in item.message or "医生" in item.message for item in by_bucket["high_risk"]))

    def test_negative_disclosure_handles_persona_with_all_condition_and_allergen_options(self):
        persona = HealthPersona(
            persona_id="all-options",
            primary_bucket="multi_condition",
            special_groups=tuple(sorted(CONDITION_GROUPS)),
            allergens=ALLERGEN_OPTIONS,
            health_goals=("均衡营养",),
        )

        disclosure = build_disclosures((persona,))[0]

        self.assertTrue(disclosure.negative_expression)
        self.assertIn("没有补充其他健康情况", disclosure.message)
        self.assertIn("没有其他食物过敏", disclosure.message)
        for condition in persona.special_groups:
            self.assertNotIn(f"没有{condition}", disclosure.message)
        for allergen in persona.allergens:
            self.assertNotIn(f"不对{allergen}过敏", disclosure.message)

    def test_synthetic_text_does_not_make_medical_conclusions(self):
        personas = build_personas(seed=88, count=100)
        disclosures = build_disclosures(personas)
        synthetic_text = " ".join(
            " ".join(persona.special_groups + persona.allergens + persona.health_goals)
            for persona in personas
            if persona.source_user_id is None
        ) + " " + " ".join(item.message for item in disclosures)
        for banned in ("确诊", "严重", "治疗"):
            self.assertNotIn(banned, synthetic_text)


if __name__ == "__main__":
    unittest.main()
