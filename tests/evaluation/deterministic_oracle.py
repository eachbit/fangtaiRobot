from __future__ import annotations

from collections.abc import Mapping
import json
import math
from pathlib import Path
from typing import Any

from app.food_terms import contains_food_term
from app.models import Recipe
from app.recipe_features import analyze_recipe
from tests.evaluation.schemas import Scenario, ScenarioResult, Violation


DEFAULT_KNOWN_GAPS_PATH = (
    Path(__file__).resolve().parents[1] / "corpus" / "known_gaps" / "phase1.json"
)
_KNOWN_GAP_FIELDS = frozenset(
    {"scenario_id", "violation_code", "owner_phase", "expires_after_phase"}
)
_NON_DOWNGRADABLE_CODES = frozenset(
    {
        "response.schema",
        "recipe.unknown_id",
        "recipe.name_mismatch",
        "recipe.ingredients_mismatch",
        "recipe.duplicate_id",
        "constraint.forbidden_term",
        "health.special_groups_false_positive",
        "health.special_groups_false_negative",
        "health.allergens_false_positive",
        "health.allergens_false_negative",
        "health.goals_false_positive",
        "health.goals_false_negative",
        "nutrition.table_total_mismatch",
        "performance.response_timeout",
        "evaluation.known_gaps_invalid",
    }
)


def _violation(
    code: str,
    message: str,
    evidence: object,
    *,
    severity: str = "blocking",
) -> Violation:
    return Violation(code, severity, message, evidence)  # type: ignore[arg-type]


def _schema_result(scenario: Scenario, elapsed_ms: float, path: str) -> ScenarioResult:
    normalized_elapsed = _finite_number(elapsed_ms)
    safe_elapsed = (
        normalized_elapsed
        if normalized_elapsed is not None and normalized_elapsed >= 0
        else 0.0
    )
    return ScenarioResult(
        scenario.scenario_id,
        False,
        (
            _violation(
                "response.schema",
                "Response does not match the required schema.",
                {"path": path},
            ),
        ),
        safe_elapsed,
    )


def _validated_menu(response: object) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]] | str:
    if not isinstance(response, Mapping):
        return "$"
    menu = response.get("menu")
    if type(menu) is not list:
        return "$.menu"
    validated: list[Mapping[str, Any]] = []
    for index, item in enumerate(menu):
        path = f"$.menu[{index}]"
        if not isinstance(item, Mapping):
            return path
        if type(item.get("id")) is not int:
            return f"{path}.id"
        if type(item.get("name")) is not str:
            return f"{path}.name"
        if type(item.get("ingredients")) is not str:
            return f"{path}.ingredients"
        validated.append(item)
    return response, validated


def _string_list(value: object, path: str) -> list[str] | str:
    if type(value) is not list:
        return path
    for index, item in enumerate(value):
        if type(item) is not str:
            return f"{path}[{index}]"
    return value


def _validated_health_fields(
    response: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str]] | str:
    constraints = response.get("constraints")
    if not isinstance(constraints, Mapping):
        return "$.constraints"
    inferred_profile = constraints.get("inferred_profile")
    if not isinstance(inferred_profile, Mapping):
        return "$.constraints.inferred_profile"

    special_groups = _string_list(
        inferred_profile.get("special_groups"),
        "$.constraints.inferred_profile.special_groups",
    )
    if type(special_groups) is str:
        return special_groups
    allergens = _string_list(
        inferred_profile.get("allergens"),
        "$.constraints.inferred_profile.allergens",
    )
    if type(allergens) is str:
        return allergens
    health_goals = _string_list(
        constraints.get("health_goals"),
        "$.constraints.health_goals",
    )
    if type(health_goals) is str:
        return health_goals
    return special_groups, allergens, health_goals


def _result(
    scenario: Scenario,
    violations: list[Violation],
    elapsed_ms: float,
) -> ScenarioResult:
    return ScenarioResult(
        scenario.scenario_id,
        not any(item.severity == "blocking" for item in violations),
        tuple(violations),
        elapsed_ms,
    )


def _finite_number(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _nutrition_check(
    response: Mapping[str, Any],
    menu: list[Mapping[str, Any]],
) -> Violation | str | None:
    if "nutrition" not in response and not any("nutrition" in item for item in menu):
        return None

    nutrition = response.get("nutrition")
    if not isinstance(nutrition, Mapping):
        return "$.nutrition"
    table_total = nutrition.get("table_total")
    if not isinstance(table_total, Mapping):
        return "$.nutrition.table_total"

    total_values: dict[str, float] = {}
    for key, value in table_total.items():
        if type(key) is not str:
            return "$.nutrition.table_total"
        number = _finite_number(value)
        if number is None:
            return f"$.nutrition.table_total.{key}"
        total_values[key] = number

    nutrient_rows: list[dict[str, float]] = []
    expected_keys: set[str] | None = None
    for index, item in enumerate(menu):
        item_nutrition = item.get("nutrition")
        if not isinstance(item_nutrition, Mapping):
            return f"$.menu[{index}].nutrition"
        nutrients = item_nutrition.get("nutrients")
        if not isinstance(nutrients, Mapping):
            return f"$.menu[{index}].nutrition.nutrients"

        row: dict[str, float] = {}
        for key, value in nutrients.items():
            if type(key) is not str:
                return f"$.menu[{index}].nutrition.nutrients"
            number = _finite_number(value)
            if number is None:
                return f"$.menu[{index}].nutrition.nutrients.{key}"
            row[key] = number
        row_keys = set(row)
        if expected_keys is None:
            expected_keys = row_keys
        elif row_keys != expected_keys:
            return f"$.menu[{index}].nutrition.nutrients"
        nutrient_rows.append(row)

    if expected_keys is None:
        expected_keys = set(total_values)
    if set(total_values) != expected_keys:
        return "$.nutrition.table_total"

    calculated = {
        key: round(sum(row[key] for row in nutrient_rows), 2)
        for key in sorted(expected_keys)
    }
    actual = {key: total_values[key] for key in sorted(expected_keys)}
    if calculated != actual:
        return _violation(
            "nutrition.table_total_mismatch",
            "Nutrition table total does not equal the rounded menu sum.",
            {"expected": calculated, "actual": actual},
        )
    return None


def _load_known_gap_pairs(path: Path | None) -> set[tuple[str, str]] | None:
    if path is None:
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if type(payload) is not list:
        return None

    pairs: set[tuple[str, str]] = set()
    for item in payload:
        if type(item) is not dict or set(item) != _KNOWN_GAP_FIELDS:
            return None
        if any(type(item[field]) is not str or not item[field].strip() for field in _KNOWN_GAP_FIELDS):
            return None
        pairs.add((item["scenario_id"], item["violation_code"]))
    return pairs


def _apply_known_gaps(
    scenario: Scenario,
    violations: list[Violation],
    known_gaps_path: Path | None,
) -> list[Violation]:
    pairs = _load_known_gap_pairs(known_gaps_path)
    if pairs is None:
        return [
            *violations,
            _violation(
                "evaluation.known_gaps_invalid",
                "Known-gap registry is malformed or unreadable.",
                None,
            ),
        ]

    result: list[Violation] = []
    for violation in violations:
        pair = (scenario.scenario_id, violation.code)
        if (
            violation.severity == "blocking"
            and violation.code not in _NON_DOWNGRADABLE_CODES
            and pair in pairs
        ):
            result.append(
                Violation(
                    violation.code,
                    "known_gap",
                    violation.message,
                    violation.evidence,
                )
            )
        else:
            result.append(violation)
    return result


def evaluate_result(
    scenario: Scenario,
    response: object,
    official_recipes: Mapping[int, Recipe],
    *,
    elapsed_ms: float = 0.0,
    known_gaps_path: Path | None = DEFAULT_KNOWN_GAPS_PATH,
) -> ScenarioResult:
    normalized_elapsed = _finite_number(elapsed_ms)
    if normalized_elapsed is None or normalized_elapsed < 0:
        return _schema_result(scenario, elapsed_ms, "$.elapsed_ms")

    validated = _validated_menu(response)
    if type(validated) is str:
        return _schema_result(scenario, normalized_elapsed, validated)
    response_map, menu = validated

    health_fields = _validated_health_fields(response_map)
    if type(health_fields) is str:
        return _schema_result(scenario, normalized_elapsed, health_fields)

    nutrition_check = _nutrition_check(response_map, menu)
    if type(nutrition_check) is str:
        return _schema_result(scenario, normalized_elapsed, nutrition_check)

    violations: list[Violation] = []
    selected_recipes: list[Recipe] = []

    # Authenticity checks deliberately precede every content-based judgment.
    for index, item in enumerate(menu):
        recipe_id = item["id"]
        official = official_recipes.get(recipe_id)
        if official is None:
            violations.append(
                _violation(
                    "recipe.unknown_id",
                    "Menu references an unknown official recipe ID.",
                    {"menu_index": index, "recipe_id": recipe_id},
                )
            )
            continue
        selected_recipes.append(official)
        if item["name"] != official.name:
            violations.append(
                _violation(
                    "recipe.name_mismatch",
                    "Recipe name does not match the official recipe.",
                    {"recipe_id": recipe_id},
                )
            )
        if item["ingredients"] != official.ingredients:
            violations.append(
                _violation(
                    "recipe.ingredients_mismatch",
                    "Recipe ingredients do not match the official recipe.",
                    {"recipe_id": recipe_id},
                )
            )

    seen: set[int] = set()
    duplicate_ids: list[int] = []
    for item in menu:
        recipe_id = item["id"]
        if recipe_id in seen and recipe_id not in duplicate_ids:
            duplicate_ids.append(recipe_id)
        seen.add(recipe_id)
    if duplicate_ids:
        violations.append(
            _violation(
                "recipe.duplicate_id",
                "Menu contains duplicate recipe IDs.",
                {"recipe_ids": duplicate_ids},
            )
        )

    for forbidden_term in scenario.expectation.forbidden_terms:
        for official in selected_recipes:
            official_text = f"{official.name} {official.ingredients} {' '.join(official.labels)}"
            if contains_food_term(official_text, forbidden_term):
                violations.append(
                    _violation(
                        "constraint.forbidden_term",
                        "Official recipe contains a forbidden food term.",
                        {"term": forbidden_term, "recipe_id": official.id},
                    )
                )

    expectation = scenario.expectation
    if expectation.dish_count is not None and len(menu) != expectation.dish_count:
        violations.append(
            _violation(
                "structure.dish_count",
                "Dish count does not match the expectation.",
                {"expected": expectation.dish_count, "actual": len(menu)},
            )
        )

    features = [(item, analyze_recipe(item)) for item in selected_recipes]
    unknown_protein_ids = [
        item.id
        for item, feature in features
        if feature.protein_style not in {"meat", "vegetable"}
    ]
    unknown_method_ids = [
        item.id for item, feature in features if feature.cooking_method == "unknown"
    ]
    if unknown_protein_ids or unknown_method_ids:
        violations.append(
            _violation(
                "coverage.recipe_structure",
                "Some official recipes have unknown structure features.",
                {
                    "protein_style_recipe_ids": unknown_protein_ids,
                    "cooking_method_recipe_ids": unknown_method_ids,
                },
                severity="soft_review",
            )
        )

    meat_count = sum(feature.protein_style == "meat" for _, feature in features)
    vegetable_count = sum(
        feature.protein_style == "vegetable" for _, feature in features
    )
    if expectation.meat_count is not None and (
        meat_count != expectation.meat_count or unknown_protein_ids
    ):
        violations.append(
            _violation(
                "structure.meat_count",
                "Meat dish count does not match or cannot be proven.",
                {
                    "expected": expectation.meat_count,
                    "actual": meat_count,
                    "unproven_recipe_ids": unknown_protein_ids,
                },
            )
        )
    if expectation.vegetable_count is not None and (
        vegetable_count != expectation.vegetable_count or unknown_protein_ids
    ):
        violations.append(
            _violation(
                "structure.vegetable_count",
                "Vegetable dish count does not match or cannot be proven.",
                {
                    "expected": expectation.vegetable_count,
                    "actual": vegetable_count,
                    "unproven_recipe_ids": unknown_protein_ids,
                },
            )
        )
    known_methods = {
        feature.cooking_method
        for _, feature in features
        if feature.cooking_method != "unknown"
    }
    if expectation.minimum_cooking_methods is not None and (
        len(known_methods) < expectation.minimum_cooking_methods or unknown_method_ids
    ):
        violations.append(
            _violation(
                "structure.cooking_diversity",
                "Cooking-method diversity does not match or cannot be proven.",
                {
                    "minimum": expectation.minimum_cooking_methods,
                    "actual": len(known_methods),
                    "unproven_recipe_ids": unknown_method_ids,
                },
            )
        )

    extracted_groups, extracted_allergens, extracted_goals = health_fields
    health_checks = (
        (
            "special_groups",
            set(extracted_groups),
            set(scenario.persona.special_groups),
        ),
        ("allergens", set(extracted_allergens), set(scenario.persona.allergens)),
        ("goals", set(extracted_goals), set(scenario.persona.health_goals)),
    )
    for field, actual, expected in health_checks:
        false_positive = sorted(actual - expected)
        if false_positive:
            violations.append(
                _violation(
                    f"health.{field}_false_positive",
                    f"Extracted {field} contain values absent from ground truth.",
                    {"values": false_positive},
                )
            )
        false_negative = sorted(expected - actual)
        if false_negative:
            violations.append(
                _violation(
                    f"health.{field}_false_negative",
                    f"Extracted {field} omit ground-truth values.",
                    {"values": false_negative},
                )
            )

    if nutrition_check is not None:
        violations.append(nutrition_check)

    if (
        scenario.expectation.clarification_required
        and response_map.get("clarification_required") is not True
    ):
        violations.append(
            _violation(
                "dialogue.clarification",
                "Response did not explicitly request required clarification.",
                None,
            )
        )

    if scenario.expectation.preserve_unaffected:
        changes = response_map.get("changes")
        score_card = response_map.get("score_card")
        minimal_change_satisfied = (
            isinstance(changes, Mapping)
            and changes.get("mode") == "minimal_revision"
            and isinstance(score_card, Mapping)
            and score_card.get("minimal_change") is True
        )
        if not minimal_change_satisfied:
            violations.append(
                _violation(
                    "dialogue.minimal_change",
                    "Response did not prove the required minimal revision.",
                    None,
                )
            )

    if normalized_elapsed > 15000:
        violations.append(
            _violation(
                "performance.response_timeout",
                "Response exceeded the 15000ms limit.",
                {"elapsed_ms": normalized_elapsed},
            )
        )

    violations = _apply_known_gaps(scenario, violations, known_gaps_path)
    return _result(scenario, violations, normalized_elapsed)
