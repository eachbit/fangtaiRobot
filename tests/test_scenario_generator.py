from __future__ import annotations

import random
import unittest
from collections import Counter

from tests.evaluation.dialogue_state_machine import (
    DIALOGUE_OPERATIONS,
    build_dialogue_plan,
)
from tests.evaluation.persona_factory import build_personas
from tests.evaluation.scenario_generator import (
    MANDATORY_INTENTS,
    generate_scenarios,
    scenario_json_bytes,
    scenarios_to_json,
    summarize_coverage,
)
from tests.evaluation.schemas import Scenario


class ScenarioGeneratorTests(unittest.TestCase):
    def test_two_hundred_cases_keep_exact_health_and_advanced_coverage(self) -> None:
        scenarios = generate_scenarios(seed=20260713, count=200)
        coverage = summarize_coverage(scenarios)

        self.assertEqual(
            coverage["primary_bucket"],
            {
                "healthy": 40,
                "high_risk": 20,
                "multi_condition": 60,
                "single_condition": 50,
                "special_group": 30,
            },
        )
        self.assertEqual(set(coverage["intent"]), set(MANDATORY_INTENTS))
        self.assertGreater(coverage["dialogue"]["multi_turn"], 0)
        self.assertEqual(set(coverage["operation"]), set(DIALOGUE_OPERATIONS))
        self.assertTrue(all(count > 0 for count in coverage["operation"].values()))

    def test_ten_cases_include_every_bucket_and_mandatory_intent(self) -> None:
        coverage = summarize_coverage(generate_scenarios(seed=11, count=10))

        self.assertEqual(len(coverage["primary_bucket"]), 5)
        self.assertEqual(set(coverage["intent"]), set(MANDATORY_INTENTS))

    def test_personas_are_preserved_as_complete_factory_objects(self) -> None:
        seed = 97
        scenarios = generate_scenarios(seed=seed, count=50)
        expected = build_personas(seed, 50)

        self.assertEqual(tuple(item.persona for item in scenarios), expected)
        for scenario, persona in zip(scenarios, expected, strict=True):
            self.assertEqual(scenario.persona.source_user_id, persona.source_user_id)
            self.assertEqual(scenario.persona.height_cm, persona.height_cm)
            self.assertEqual(scenario.persona.weight_kg, persona.weight_kg)
            self.assertEqual(scenario.persona.bmi, persona.bmi)
            self.assertEqual(scenario.persona.checkup_metrics, persona.checkup_metrics)
            self.assertEqual(scenario.persona.special_groups, persona.special_groups)
            self.assertEqual(scenario.persona.allergens, persona.allergens)
            self.assertEqual(scenario.persona.health_goals, persona.health_goals)

    def test_health_intents_disclose_real_complete_context_without_diagnosis(self) -> None:
        scenarios = generate_scenarios(seed=20260713, count=200)
        health_cases = [
            item
            for item in scenarios
            if item.intent in {"health_profile", "nutrition_tradeoff"}
        ]

        self.assertTrue(health_cases)
        for scenario in health_cases:
            message = "".join(scenario.messages)
            for value in (
                *scenario.persona.special_groups,
                *scenario.persona.allergens,
                *scenario.persona.health_goals,
            ):
                self.assertIn(value, message)
            if scenario.persona.checkup_metrics is not None:
                values = scenario.persona.checkup_metrics.to_dict().values()
                self.assertTrue(
                    any(value is not None and str(value) in message for value in values)
                )
            elif scenario.persona.bmi is not None:
                self.assertIn(str(scenario.persona.bmi), message)
            for prohibited in ("确诊", "严重", "治疗"):
                self.assertNotIn(prohibited, message)

    def test_each_intent_has_the_required_expectation(self) -> None:
        scenarios = generate_scenarios(seed=31, count=90)
        by_intent = {item.intent: item for item in scenarios}

        hard = by_intent["hard_constraint"]
        self.assertTrue(hard.expectation.forbidden_terms)
        self.assertEqual(
            hard.expectation.forbidden_terms[0],
            hard.persona.allergens[0] if hard.persona.allergens else "花生",
        )

        ratio = by_intent["structure_ratio"].expectation
        self.assertEqual((ratio.dish_count, ratio.meat_count, ratio.vegetable_count), (6, 2, 4))

        diversity = by_intent["cooking_diversity"].expectation
        self.assertEqual(diversity.dish_count, 6)
        self.assertGreaterEqual(diversity.minimum_cooking_methods or 0, 3)

        revision = by_intent["relative_revision"]
        self.assertEqual(revision.dialogue_mode, "multi_turn")
        self.assertTrue(revision.expectation.preserve_unaffected)
        self.assertTrue(by_intent["ambiguous_request"].expectation.clarification_required)
        self.assertTrue(by_intent["multi_person_conflict"].expectation.clarification_required)

    def test_hard_constraint_messages_do_not_invent_allergies(self) -> None:
        cases = [
            item
            for item in generate_scenarios(seed=20260713, count=200)
            if item.intent == "hard_constraint"
        ]

        for scenario in cases:
            expected = scenario.persona.allergens[0] if scenario.persona.allergens else "花生"
            message = "".join(scenario.messages)
            self.assertIn(expected, message)
            if "我对" in message and "过敏" in message:
                self.assertIn(f"我对{expected}过敏", message)

    def test_negative_expression_never_denies_persona_ground_truth(self) -> None:
        scenarios = generate_scenarios(seed=42, count=200)
        negatives = [item for item in scenarios if item.intent == "negative_expression"]

        self.assertTrue(negatives)
        for scenario in negatives:
            message = "".join(scenario.messages)
            for fact in (*scenario.persona.special_groups, *scenario.persona.allergens):
                self.assertNotIn(f"没有{fact}", message)
                self.assertNotIn(f"不过敏{fact}", message)
            self.assertEqual(
                Scenario.from_dict(scenario.to_dict()).persona,
                scenario.persona,
            )

    def test_multi_person_conflict_names_two_people_and_requests_clarification(self) -> None:
        cases = [
            item
            for item in generate_scenarios(seed=61, count=90)
            if item.intent == "multi_person_conflict"
        ]

        for scenario in cases:
            message = "".join(scenario.messages)
            self.assertIn("我", message)
            self.assertIn("另一位", message)
            self.assertTrue(scenario.expectation.clarification_required)

    def test_dialogue_operations_have_distinct_semantics_and_preserve_persona(self) -> None:
        persona = build_personas(seed=8, count=10)[2]
        expected_terms = {
            "append_constraint": "新增忌口",
            "retract_preference": "撤回偏好",
            "request_position_change": "第二道",
            "request_structure_change": "荤素",
            "ambiguous_change": "调整一下",
            "confirm_clarification": "一比二",
        }

        for operation in DIALOGUE_OPERATIONS:
            with self.subTest(operation=operation):
                plan = build_dialogue_plan(persona, operation, seed=13)
                self.assertIs(plan.persona, persona)
                self.assertEqual(plan.operation.name, operation)
                self.assertIn(len(plan.messages), (2, 3))
                self.assertIn(expected_terms[operation], "".join(plan.messages))
                disclosure = plan.messages[0]
                for fact in (*persona.special_groups, *persona.allergens):
                    self.assertIn(fact, disclosure)
                for fact in (*persona.special_groups, *persona.allergens):
                    self.assertNotIn(f"撤回{fact}", "".join(plan.messages[1:]))
                if plan.expectation.preserve_unaffected:
                    self.assertIn("菜", "".join(plan.messages[1:]))
                    self.assertIn("保留", "".join(plan.messages[1:]))

        self.assertTrue(
            build_dialogue_plan(persona, "append_constraint").operation.added_constraints
        )
        retract = build_dialogue_plan(persona, "retract_preference")
        self.assertTrue(retract.operation.retracted_preferences)
        self.assertFalse(retract.operation.retracted_health_facts)
        position = build_dialogue_plan(persona, "request_position_change")
        self.assertTrue(position.expectation.preserve_unaffected)
        structure = build_dialogue_plan(persona, "request_structure_change")
        self.assertEqual(
            (structure.expectation.meat_count, structure.expectation.vegetable_count),
            (2, 4),
        )
        ambiguous = build_dialogue_plan(persona, "ambiguous_change")
        self.assertTrue(ambiguous.expectation.clarification_required)
        confirm = build_dialogue_plan(persona, "confirm_clarification")
        self.assertTrue(confirm.first_stage_expectation.clarification_required)
        self.assertFalse(confirm.expectation.clarification_required)

    def test_ids_round_trip_and_serialization_are_stable(self) -> None:
        first = generate_scenarios(seed=73, count=50)
        second = generate_scenarios(seed=73, count=50)
        other = generate_scenarios(seed=74, count=50)

        self.assertEqual(first, second)
        self.assertEqual(scenario_json_bytes(first), scenario_json_bytes(second))
        self.assertEqual(scenarios_to_json(first).encode("utf-8"), scenario_json_bytes(first))
        self.assertNotEqual(first, other)
        self.assertNotEqual(scenario_json_bytes(first), scenario_json_bytes(other))
        ids = [item.scenario_id for item in first]
        self.assertEqual(len(ids), len(set(ids)))
        for index, scenario in enumerate(first):
            self.assertIn(f"seed-73-index-{index:04d}-{scenario.intent}", scenario.scenario_id)
            self.assertEqual(scenario.seed, 73)
            self.assertEqual(Scenario.from_dict(scenario.to_dict()), scenario)

    def test_coverage_pairs_are_sorted_and_each_totals_the_scenario_count(self) -> None:
        scenarios = generate_scenarios(seed=17, count=200)
        coverage = summarize_coverage(scenarios)

        self.assertEqual(
            list(coverage["pairs"]),
            ["intent,dialogue", "primary_bucket,dialogue", "primary_bucket,intent"],
        )
        for pair_counts in coverage["pairs"].values():
            self.assertEqual(list(pair_counts), sorted(pair_counts))
            self.assertEqual(sum(pair_counts.values()), len(scenarios))

    def test_coverage_validation_lists_missing_dimensions(self) -> None:
        scenarios = generate_scenarios(seed=19, count=10)

        with self.assertRaisesRegex(ValueError, "missing primary_bucket.*missing intent"):
            summarize_coverage(scenarios[:1])

    def test_generator_does_not_read_or_mutate_global_random_state(self) -> None:
        random.seed(123456)
        before = random.getstate()
        first = generate_scenarios(seed=5, count=50)
        after = random.getstate()

        generate_scenarios(seed=999, count=200)
        again = generate_scenarios(seed=5, count=50)

        self.assertEqual(before, after)
        self.assertEqual(first, again)
        self.assertEqual(
            Counter(item.intent for item in first),
            Counter(item.intent for item in again),
        )

    def test_count_below_ten_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 10"):
            generate_scenarios(seed=1, count=9)


if __name__ == "__main__":
    unittest.main()
