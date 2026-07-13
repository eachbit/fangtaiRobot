from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from tests.evaluation.schemas import (
    CheckupMetrics,
    EvaluationReport,
    FailureRecord,
    HealthPersona,
    MenuExpectation,
    Scenario,
    ScenarioResult,
    Violation,
)


class EvaluationSchemaTests(unittest.TestCase):
    def build_scenario(self) -> Scenario:
        return Scenario(
            scenario_id="multi-condition-001",
            persona=HealthPersona(
                persona_id="persona-001",
                primary_bucket="multi_condition",
                source_user_id=42,
                gender="female",
                age=36,
                labor_intensity="medium",
                pregnancy_week=None,
                taste_preference="light",
                special_groups=("hypertension", "diabetes"),
                allergens=("peanut",),
                health_goals=("lower_sodium", "control_sugar"),
            ),
            messages=("Plan a six-dish dinner.", "Keep a 2:4 meat-to-vegetable ratio."),
            expectation=MenuExpectation(
                dish_count=6,
                meat_count=2,
                vegetable_count=4,
                minimum_cooking_methods=2,
                forbidden_terms=("peanut", "peanut oil"),
                clarification_required=False,
                preserve_unaffected=True,
            ),
            seed=20260713,
            intent="structure_ratio",
            dialogue_mode="multi_turn",
        )

    def test_scenario_round_trip_is_json_safe_and_restores_tuples(self) -> None:
        scenario = self.build_scenario()

        payload = json.loads(json.dumps(scenario.to_dict()))
        restored = Scenario.from_dict(payload)

        self.assertEqual(restored, scenario)
        self.assertIsInstance(restored.messages, tuple)
        self.assertIsInstance(restored.persona.special_groups, tuple)
        self.assertIsInstance(restored.persona.allergens, tuple)
        self.assertIsInstance(restored.persona.health_goals, tuple)
        self.assertIsInstance(restored.expectation.forbidden_terms, tuple)

    def test_detailed_health_persona_round_trip_keeps_typed_checkup(self) -> None:
        persona = HealthPersona(
            persona_id="official-002",
            primary_bucket="special_group",
            source_user_id=2,
            gender="female",
            age=32,
            labor_intensity="low",
            pregnancy_week="22 weeks",
            taste_preference="sweet_and_sour",
            height_cm=160.4,
            weight_kg=64.1,
            bmi=24.9,
            checkup_metrics=CheckupMetrics(
                fasting_glucose_mmol_l=5.4,
                systolic_blood_pressure_mm_hg=116,
                diastolic_blood_pressure_mm_hg=81,
                total_cholesterol_mmol_l=4.56,
                triglycerides_mmol_l=1.02,
                ldl_mmol_l=2.62,
                hdl_mmol_l=1.67,
                uric_acid_umol_l=261,
            ),
            special_groups=("pregnant",),
            allergens=(),
            health_goals=("calcium", "iron", "balanced_nutrition"),
        )

        payload = json.loads(json.dumps(persona.to_dict()))
        restored = HealthPersona.from_dict(payload)

        self.assertEqual(restored, persona)
        self.assertIsInstance(restored.checkup_metrics, CheckupMetrics)

    def test_empty_checkup_is_valid_frozen_and_typed_after_round_trip(self) -> None:
        persona = HealthPersona(
            "official-001",
            "healthy",
            checkup_metrics=CheckupMetrics(),
        )
        payload = persona.to_dict()
        payload["checkup_metrics"] = {}

        restored = HealthPersona.from_dict(
            json.loads(json.dumps(payload))
        )

        self.assertEqual(restored, persona)
        self.assertIsInstance(restored.checkup_metrics, CheckupMetrics)
        with self.assertRaises(FrozenInstanceError):
            restored.checkup_metrics.fasting_glucose_mmol_l = 5.0  # type: ignore[misc]

    def test_checkup_rejects_partial_blood_pressure_with_nested_path(self) -> None:
        payload = self.build_scenario().persona.to_dict()
        payload["checkup_metrics"] = {"systolic_blood_pressure_mm_hg": 120}

        with self.assertRaisesRegex(
            ValueError,
            r"\$\.checkup_metrics\.diastolic_blood_pressure_mm_hg",
        ):
            HealthPersona.from_dict(payload)

    def test_checkup_rejects_reversed_blood_pressure_with_nested_path(self) -> None:
        payload = self.build_scenario().persona.to_dict()
        payload["checkup_metrics"] = {
            "systolic_blood_pressure_mm_hg": 80,
            "diastolic_blood_pressure_mm_hg": 120,
        }

        with self.assertRaisesRegex(
            ValueError,
            r"\$\.checkup_metrics\.systolic_blood_pressure_mm_hg",
        ):
            HealthPersona.from_dict(payload)

    def test_body_measurements_reject_non_positive_or_non_finite_values(self) -> None:
        invalid_values = (0, -1, float("nan"), float("inf"), float("-inf"))
        for field in ("height_cm", "weight_kg", "bmi"):
            for invalid in invalid_values:
                with self.subTest(field=field, value=invalid):
                    with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                        HealthPersona(
                            "invalid-body-metric",
                            "healthy",
                            **{field: invalid},
                        )

    def test_checkup_rejects_non_positive_or_non_finite_values(self) -> None:
        fields = (
            "fasting_glucose_mmol_l",
            "systolic_blood_pressure_mm_hg",
            "diastolic_blood_pressure_mm_hg",
            "total_cholesterol_mmol_l",
            "triglycerides_mmol_l",
            "ldl_mmol_l",
            "hdl_mmol_l",
            "uric_acid_umol_l",
        )
        invalid_values = (0, -1, float("nan"), float("inf"), float("-inf"))
        for field in fields:
            for invalid in invalid_values:
                with self.subTest(field=field, value=invalid):
                    values = {field: invalid}
                    if field == "systolic_blood_pressure_mm_hg":
                        values["diastolic_blood_pressure_mm_hg"] = 80
                    elif field == "diastolic_blood_pressure_mm_hg":
                        values["systolic_blood_pressure_mm_hg"] = 120
                    with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                        CheckupMetrics(**values)

    def test_new_numeric_fields_reject_bool(self) -> None:
        for field in ("height_cm", "weight_kg", "bmi"):
            with self.subTest(owner="persona", field=field):
                with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                    HealthPersona("bool-metric", "healthy", **{field: True})

        checkup_fields = (
            "fasting_glucose_mmol_l",
            "systolic_blood_pressure_mm_hg",
            "diastolic_blood_pressure_mm_hg",
            "total_cholesterol_mmol_l",
            "triglycerides_mmol_l",
            "ldl_mmol_l",
            "hdl_mmol_l",
            "uric_acid_umol_l",
        )
        for field in checkup_fields:
            with self.subTest(owner="checkup", field=field):
                values = {field: True}
                if field == "systolic_blood_pressure_mm_hg":
                    values["diastolic_blood_pressure_mm_hg"] = 80
                elif field == "diastolic_blood_pressure_mm_hg":
                    values["systolic_blood_pressure_mm_hg"] = 120
                with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                    CheckupMetrics(**values)

    def test_health_persona_from_dict_rejects_unknown_checkup_field(self) -> None:
        payload = self.build_scenario().persona.to_dict()
        payload["checkup_metrics"] = {"blood_glucose": 5.4}

        with self.assertRaisesRegex(
            ValueError, r"\$\.checkup_metrics\.blood_glucose"
        ):
            HealthPersona.from_dict(payload)

    def test_health_persona_from_dict_rejects_wrong_checkup_shape(self) -> None:
        payload = self.build_scenario().persona.to_dict()
        payload["checkup_metrics"] = []

        with self.assertRaisesRegex(ValueError, r"\$\.checkup_metrics"):
            HealthPersona.from_dict(payload)

    def test_json_integer_inputs_for_float_fields_are_normalized(self) -> None:
        payload = self.build_scenario().persona.to_dict()
        payload.update({"height_cm": 160, "weight_kg": 64, "bmi": 25})
        payload["checkup_metrics"] = {
            "fasting_glucose_mmol_l": 5,
            "systolic_blood_pressure_mm_hg": 120,
            "diastolic_blood_pressure_mm_hg": 80,
            "total_cholesterol_mmol_l": 4,
            "triglycerides_mmol_l": 1,
            "ldl_mmol_l": 3,
            "hdl_mmol_l": 1,
            "uric_acid_umol_l": 300,
        }

        restored = HealthPersona.from_dict(payload)

        self.assertIs(type(restored.height_cm), float)
        self.assertIs(type(restored.weight_kg), float)
        self.assertIs(type(restored.bmi), float)
        self.assertIs(type(restored.checkup_metrics.fasting_glucose_mmol_l), float)
        self.assertIs(type(restored.checkup_metrics.total_cholesterol_mmol_l), float)
        self.assertIs(type(restored.checkup_metrics.triglycerides_mmol_l), float)
        self.assertIs(type(restored.checkup_metrics.ldl_mmol_l), float)
        self.assertIs(type(restored.checkup_metrics.hdl_mmol_l), float)

    def test_body_float_fields_reject_imprecise_integer_conversion(self) -> None:
        imprecise = 2**53 + 1
        for field in ("height_cm", "weight_kg", "bmi"):
            with self.subTest(field=field, source="constructor"):
                with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                    HealthPersona(
                        "imprecise-body-metric",
                        "healthy",
                        **{field: imprecise},
                    )

            with self.subTest(field=field, source="from_dict"):
                payload = self.build_scenario().persona.to_dict()
                payload[field] = imprecise
                with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                    HealthPersona.from_dict(payload)

    def test_checkup_float_fields_reject_imprecise_integer_conversion(self) -> None:
        imprecise = 2**53 + 1
        fields = (
            "fasting_glucose_mmol_l",
            "total_cholesterol_mmol_l",
            "triglycerides_mmol_l",
            "ldl_mmol_l",
            "hdl_mmol_l",
        )
        for field in fields:
            with self.subTest(field=field, source="constructor"):
                with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                    CheckupMetrics(**{field: imprecise})

            with self.subTest(field=field, source="from_dict"):
                with self.assertRaisesRegex(ValueError, rf"\$\.{field}"):
                    CheckupMetrics.from_dict({field: imprecise})

    def test_exact_float_integer_boundary_round_trips(self) -> None:
        exact = 2**53
        persona = HealthPersona(
            "exact-float-boundary",
            "healthy",
            height_cm=exact,
            weight_kg=exact,
            bmi=exact,
            checkup_metrics=CheckupMetrics(
                fasting_glucose_mmol_l=exact,
                total_cholesterol_mmol_l=exact,
                triglycerides_mmol_l=exact,
                ldl_mmol_l=exact,
                hdl_mmol_l=exact,
            ),
        )

        restored = HealthPersona.from_dict(
            json.loads(json.dumps(persona.to_dict()))
        )

        self.assertEqual(restored, persona)
        self.assertEqual(restored.height_cm, float(exact))
        self.assertEqual(
            restored.checkup_metrics.fasting_glucose_mmol_l,
            float(exact),
        )

    def test_schema_does_not_enforce_bmi_formula_consistency(self) -> None:
        persona = HealthPersona(
            "external-bmi",
            "healthy",
            height_cm=200,
            weight_kg=40,
            bmi=99,
        )

        self.assertEqual(persona.bmi, 99.0)

    def test_report_round_trip_restores_nested_tuples(self) -> None:
        violation = Violation(
            code="allergen.peanut",
            severity="blocking",
            message="The menu contains a forbidden allergen.",
            evidence={"dish_ids": [7, 9], "term": "peanut"},
        )
        failure = FailureRecord(
            scenario_id="multi-condition-001",
            seed=20260713,
            commit_sha="d9e32ea",
            original_messages=("Plan dinner.", "Do not use peanuts."),
            minimized_messages=("Do not use peanuts.",),
            violations=(violation,),
            elapsed_ms=12.5,
        )
        report = EvaluationReport(
            total=2,
            passed=1,
            failures=(failure,),
            coverage={"primary_bucket": {"healthy": 1, "multi_condition": 1}},
            metrics={"allergens": {"precision": 1.0, "recall": 0.5}},
            timings={"p50_ms": 8.0, "p95_ms": 12.5},
        )

        payload = json.loads(json.dumps(report.to_dict()))
        restored = EvaluationReport.from_dict(payload)

        self.assertEqual(restored, report)
        self.assertIsInstance(restored.failures, tuple)
        self.assertIsInstance(restored.failures[0].original_messages, tuple)
        self.assertIsInstance(restored.failures[0].minimized_messages, tuple)
        self.assertIsInstance(restored.failures[0].violations, tuple)

    def test_scenario_result_round_trip_restores_violations_tuple(self) -> None:
        result = ScenarioResult(
            scenario_id="healthy-001",
            passed=False,
            violations=(Violation("schema.invalid", "known_gap", "Invalid output", None),),
            elapsed_ms=3.25,
        )

        payload = json.loads(json.dumps(result.to_dict()))

        self.assertEqual(ScenarioResult.from_dict(payload), result)
        self.assertIsInstance(ScenarioResult.from_dict(payload).violations, tuple)

    def test_defaults_are_stable(self) -> None:
        scenario = Scenario(
            scenario_id="healthy-001",
            persona=HealthPersona("persona-healthy", "healthy"),
            messages=("Plan dinner.",),
            expectation=MenuExpectation(),
            seed=7,
        )

        self.assertEqual(scenario.intent, "general_recommendation")
        self.assertEqual(scenario.dialogue_mode, "single_turn")

    def test_contracts_are_frozen(self) -> None:
        scenario = self.build_scenario()

        with self.assertRaises(FrozenInstanceError):
            scenario.seed = 8  # type: ignore[misc]

    def test_rejects_invalid_primary_bucket(self) -> None:
        with self.assertRaises(ValueError):
            HealthPersona("persona-001", "unsupported")

    def test_rejects_invalid_dialogue_mode(self) -> None:
        with self.assertRaises(ValueError):
            Scenario(
                "scenario-001",
                HealthPersona("persona-001", "healthy"),
                ("Plan dinner.",),
                MenuExpectation(),
                7,
                dialogue_mode="unsupported",
            )

    def test_rejects_empty_scenario_id(self) -> None:
        with self.assertRaises(ValueError):
            Scenario(
                "",
                HealthPersona("persona-001", "healthy"),
                ("Plan dinner.",),
                MenuExpectation(),
                7,
            )

    def test_rejects_empty_intent(self) -> None:
        with self.assertRaises(ValueError):
            Scenario(
                "scenario-001",
                HealthPersona("persona-001", "healthy"),
                ("Plan dinner.",),
                MenuExpectation(),
                7,
                intent="",
            )

    def test_rejects_invalid_violation_severity(self) -> None:
        with self.assertRaises(ValueError):
            Violation("schema.invalid", "fatal", "Invalid output", None)

    def test_rejects_set_and_unknown_object_with_evidence_path(self) -> None:
        for invalid in ({"not", "json"}, object()):
            with self.subTest(value=type(invalid).__name__):
                with self.assertRaisesRegex(ValueError, r"\$\.evidence"):
                    Violation("schema.invalid", "blocking", "Invalid output", invalid)

    def test_rejects_non_finite_numbers_with_nested_path(self) -> None:
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(
                    ValueError, r"\$\.evidence\.scores\[1\]"
                ):
                    Violation(
                        "score.invalid",
                        "blocking",
                        "Invalid score",
                        {"scores": [1.0, invalid]},
                    )

    def test_rejects_non_string_json_keys_without_coercion_or_collision(self) -> None:
        evidence = {1: "integer key", "1": "string key"}

        with self.assertRaisesRegex(ValueError, r"\$\.evidence"):
            Violation("schema.invalid", "blocking", "Invalid output", evidence)

    def test_from_dict_rejects_string_instead_of_messages_array(self) -> None:
        payload = self.build_scenario().to_dict()
        payload["messages"] = "Plan dinner."

        with self.assertRaisesRegex(ValueError, r"\$\.messages"):
            Scenario.from_dict(payload)

    def test_from_dict_rejects_wrong_nested_value_with_exact_path(self) -> None:
        payload = self.build_scenario().to_dict()
        payload["persona"]["special_groups"] = ["hypertension", 7]

        with self.assertRaisesRegex(
            ValueError, r"\$\.persona\.special_groups\[1\]"
        ):
            Scenario.from_dict(payload)

    def test_from_dict_rejects_unknown_root_and_nested_fields(self) -> None:
        for nested, path in ((False, r"\$\.intnet"), (True, r"\$\.persona\.agge")):
            with self.subTest(nested=nested):
                payload = self.build_scenario().to_dict()
                if nested:
                    payload["persona"]["agge"] = 36
                else:
                    payload["intnet"] = "structure_ratio"

                with self.assertRaisesRegex(ValueError, path):
                    Scenario.from_dict(payload)

    def test_from_dict_reports_missing_required_nested_field(self) -> None:
        payload = self.build_scenario().to_dict()
        del payload["persona"]["primary_bucket"]

        with self.assertRaisesRegex(ValueError, r"\$\.persona\.primary_bucket"):
            Scenario.from_dict(payload)

    def test_from_dict_normalizes_nested_shape_errors_to_value_error(self) -> None:
        payload = self.build_scenario().to_dict()
        payload["persona"] = "not an object"

        with self.assertRaisesRegex(ValueError, r"\$\.persona"):
            Scenario.from_dict(payload)

    def test_from_dict_does_not_accept_bool_as_integer_or_number(self) -> None:
        scenario_payload = self.build_scenario().to_dict()
        scenario_payload["seed"] = True
        result_payload = {
            "scenario_id": "healthy-001",
            "passed": False,
            "violations": [],
            "elapsed_ms": True,
        }

        with self.assertRaisesRegex(ValueError, r"\$\.seed"):
            Scenario.from_dict(scenario_payload)
        with self.assertRaisesRegex(ValueError, r"\$\.elapsed_ms"):
            ScenarioResult.from_dict(result_payload)

    def test_from_dict_normalizes_out_of_range_number_to_value_error(self) -> None:
        payload = {
            "scenario_id": "healthy-001",
            "passed": True,
            "violations": [],
            "elapsed_ms": 10**10000,
        }

        with self.assertRaisesRegex(ValueError, r"\$\.elapsed_ms"):
            ScenarioResult.from_dict(payload)

    def test_rejects_nested_ints_outside_signed_64_bit_range(self) -> None:
        for invalid in (-(2**63) - 1, 2**63):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(
                    ValueError, r"\$\.evidence\.details\.count"
                ):
                    Violation(
                        "count.invalid",
                        "blocking",
                        "Invalid count",
                        {"details": {"count": invalid}},
                    )
                with self.assertRaisesRegex(
                    ValueError, r"\$\.evidence\.details\.count"
                ):
                    Violation.from_dict(
                        {
                            "code": "count.invalid",
                            "severity": "blocking",
                            "message": "Invalid count",
                            "evidence": {"details": {"count": invalid}},
                        }
                    )

    def test_report_rejects_nested_out_of_range_ints_in_all_json_maps(self) -> None:
        for field in ("coverage", "metrics", "timings"):
            with self.subTest(field=field):
                values = {
                    "coverage": {"nested": {"count": 1}},
                    "metrics": {"nested": {"count": 1}},
                    "timings": {"nested": {"count": 1}},
                }
                values[field]["nested"]["count"] = 2**63

                with self.assertRaisesRegex(
                    ValueError, rf"\$\.{field}\.nested\.count"
                ):
                    EvaluationReport(
                        1,
                        1,
                        (),
                        values["coverage"],
                        values["metrics"],
                        values["timings"],
                    )
                with self.assertRaisesRegex(
                    ValueError, rf"\$\.{field}\.nested\.count"
                ):
                    EvaluationReport.from_dict(
                        {
                            "total": 1,
                            "passed": 1,
                            "failures": [],
                            "coverage": values["coverage"],
                            "metrics": values["metrics"],
                            "timings": values["timings"],
                        }
                    )

    def test_from_dict_rejects_tuple_in_generic_json_arrays(self) -> None:
        violation_payload = {
            "code": "evidence.invalid",
            "severity": "blocking",
            "message": "Invalid evidence",
            "evidence": {"dish_ids": (7, 9)},
        }
        with self.assertRaisesRegex(ValueError, r"\$\.evidence\.dish_ids"):
            Violation.from_dict(violation_payload)
        for field in ("coverage", "metrics", "timings"):
            with self.subTest(field=field):
                report_payload = {
                    "total": 1,
                    "passed": 1,
                    "failures": [],
                    "coverage": {},
                    "metrics": {},
                    "timings": {},
                }
                report_payload[field] = {"values": (1, 2)}

                with self.assertRaisesRegex(ValueError, rf"\$\.{field}\.values"):
                    EvaluationReport.from_dict(report_payload)

    def test_direct_construction_accepts_tuple_and_exports_json_lists(self) -> None:
        violation = Violation(
            "evidence.valid",
            "soft_review",
            "Valid evidence",
            {"dish_ids": (7, 9)},
        )
        report = EvaluationReport(
            1,
            1,
            (),
            {"buckets": ("healthy",)},
            {"scores": (1.0,)},
            {"samples_ms": (8.0,)},
        )

        self.assertEqual(violation.to_dict()["evidence"], {"dish_ids": [7, 9]})
        self.assertEqual(report.to_dict()["coverage"], {"buckets": ["healthy"]})
        self.assertEqual(report.to_dict()["metrics"], {"scores": [1.0]})
        self.assertEqual(report.to_dict()["timings"], {"samples_ms": [8.0]})

    def test_signed_64_bit_boundaries_round_trip_through_json(self) -> None:
        violation = Violation(
            "integer.boundary",
            "soft_review",
            "Boundary values",
            {"minimum": -(2**63), "maximum": 2**63 - 1},
        )

        payload = json.loads(json.dumps(violation.to_dict()))

        self.assertEqual(Violation.from_dict(payload), violation)

    def test_violation_defensively_freezes_nested_evidence(self) -> None:
        source = {"details": {"dish_ids": [7, 9]}}
        violation = Violation("allergen.peanut", "blocking", "Found peanut", source)

        source["details"]["dish_ids"].append(11)
        source["details"]["term"] = "peanut"

        self.assertEqual(
            violation.to_dict()["evidence"], {"details": {"dish_ids": [7, 9]}}
        )
        with self.assertRaises(TypeError):
            violation.evidence["new"] = "value"  # type: ignore[index]
        with self.assertRaises(TypeError):
            violation.evidence["details"]["dish_ids"][0] = 99  # type: ignore[index]

    def test_report_defensively_freezes_all_json_mappings(self) -> None:
        coverage = {"bucket": {"healthy": 1}}
        metrics = {"allergens": {"values": [1.0, 0.5]}}
        timings = {"samples_ms": [8.0, 12.5]}
        report = EvaluationReport(1, 1, (), coverage, metrics, timings)

        coverage["bucket"]["healthy"] = 99
        metrics["allergens"]["values"].append(0.0)
        timings["samples_ms"].append(20.0)

        self.assertEqual(report.to_dict()["coverage"], {"bucket": {"healthy": 1}})
        self.assertEqual(
            report.to_dict()["metrics"], {"allergens": {"values": [1.0, 0.5]}}
        )
        self.assertEqual(report.to_dict()["timings"], {"samples_ms": [8.0, 12.5]})
        with self.assertRaises(TypeError):
            report.coverage["bucket"]["healthy"] = 2  # type: ignore[index]
        with self.assertRaises(TypeError):
            report.metrics["allergens"]["values"][0] = 0.0  # type: ignore[index]

    def test_to_dict_returns_mutable_copy_without_exposing_frozen_state(self) -> None:
        violation = Violation(
            "allergen.peanut",
            "blocking",
            "Found peanut",
            {"details": {"dish_ids": [7]}},
        )

        payload = violation.to_dict()
        payload["evidence"]["details"]["dish_ids"].append(9)

        self.assertEqual(
            violation.to_dict()["evidence"], {"details": {"dish_ids": [7]}}
        )


if __name__ == "__main__":
    unittest.main()
