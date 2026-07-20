from __future__ import annotations

import math
from typing import Any

from .ingredient_parser import ParsedIngredient, parse_ingredients
from .models import Recipe
from .nutrition_repository import (
    NUTRIENT_KEYS,
    NutritionRepository,
    get_nutrition_repository,
)
from .recipe_features import classify_recipe


PORTION_GRAMS = {
    "staple": 250.0,
    "meat": 180.0,
    "vegetable": 200.0,
    "soup": 350.0,
    "dessert": 120.0,
}

SOURCE_QUALITY = {
    "explicit": 1.0,
    "estimated": 0.75,
    "default": 0.4,
    "unknown": 0.0,
}

SEASONING_NAMES = {"盐", "食用油", "白砂糖", "生抽", "老抽", "酱油", "料酒"}


def calculate_recipe_nutrition(
    recipe: Recipe,
    repository: NutritionRepository | None = None,
) -> dict[str, Any]:
    repository = repository or get_nutrition_repository()
    parsed = parse_ingredients(recipe.ingredients)
    totals = {key: 0.0 for key in NUTRIENT_KEYS}
    ingredient_details: list[dict[str, Any]] = []
    missing: list[str] = []
    unweighted: list[str] = []
    matched_count = 0
    weighted_count = 0
    known_weight = 0.0
    portion_weight = 0.0
    source_quality_total = 0.0
    uses_derived_data = False

    for ingredient in parsed:
        food = repository.resolve(ingredient.canonical_name)
        if ingredient.grams is not None:
            weighted_count += 1
            known_weight += float(ingredient.grams)
        source_quality_total += SOURCE_QUALITY.get(ingredient.amount_source, 0.0)

        detail = ingredient.to_dict()
        detail.update({"matched": food is not None, "source": None, "source_id": None, "nutrients": None})
        if food is None:
            if ingredient.canonical_name and ingredient.canonical_name not in missing:
                missing.append(ingredient.canonical_name)
            ingredient_details.append(detail)
            continue

        matched_count += 1
        uses_derived_data = uses_derived_data or food.source == "USDA-derived estimate"
        detail["source"] = food.source
        detail["source_id"] = food.source_id
        if ingredient.grams is not None:
            if ingredient.canonical_name not in SEASONING_NAMES:
                portion_weight += float(ingredient.grams)
            factor = float(ingredient.grams) / 100.0
            item_nutrients = {
                key: food.nutrients_per_100g[key] * factor for key in NUTRIENT_KEYS
            }
            for key, value in item_nutrients.items():
                totals[key] += value
            detail["nutrients"] = _round_nutrients(item_nutrients)
        elif ingredient.canonical_name not in unweighted:
            unweighted.append(ingredient.canonical_name)
        ingredient_details.append(detail)

    total_count = len(parsed)
    match_coverage = matched_count / total_count if total_count else 0.0
    weight_coverage = weighted_count / total_count if total_count else 0.0
    source_quality = source_quality_total / total_count if total_count else 0.0
    confidence_score = _confidence_score(match_coverage, weight_coverage, source_quality)
    if match_coverage < 1.0:
        confidence_score = min(confidence_score, 0.79)
    if uses_derived_data:
        confidence_score = min(confidence_score, 0.79)
    category = classify_recipe(recipe)
    serving_weight = known_weight if category == "soup" else portion_weight
    servings, serving_range = _estimate_servings(recipe, serving_weight)

    return {
        "nutrients": _round_nutrients(totals),
        "ingredients": ingredient_details,
        "estimated_servings": servings,
        "serving_range": serving_range,
        "known_weight_g": round(known_weight, 2),
        "portion_weight_g": round(serving_weight, 2),
        "missing_ingredients": missing,
        "unweighted_ingredients": unweighted,
        "ingredient_match_coverage": round(match_coverage, 4),
        "weight_coverage": round(weight_coverage, 4),
        "confidence": {
            "score": round(confidence_score, 4),
            "level": _confidence_level(confidence_score),
            "reasons": _confidence_reasons(
                parsed, match_coverage, weight_coverage, uses_derived_data
            ),
        },
    }


def _round_nutrients(values: dict[str, float]) -> dict[str, float]:
    return {key: round(float(values.get(key, 0.0)), 2) for key in NUTRIENT_KEYS}


def _confidence_score(match_coverage: float, weight_coverage: float, source_quality: float) -> float:
    return max(0.0, min(1.0, match_coverage * 0.5 + weight_coverage * 0.3 + source_quality * 0.2))


def _confidence_level(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _confidence_reasons(
    ingredients: list[ParsedIngredient],
    match_coverage: float,
    weight_coverage: float,
    uses_derived_data: bool,
) -> list[str]:
    reasons: list[str] = []
    if not ingredients:
        return ["食材清单为空"]
    if match_coverage < 1:
        reasons.append("部分食材缺少营养条目")
    if weight_coverage < 1:
        reasons.append("部分食材缺少可换算用量")
    if any(item.amount_source == "default" for item in ingredients):
        reasons.append("包含适量或少许的默认用量")
    if any(item.amount_source == "estimated" for item in ingredients):
        reasons.append("包含体积或自然单位换算")
    if uses_derived_data:
        reasons.append("包含派生营养数据")
    if not reasons:
        reasons.append("食材与重量均完整匹配")
    return reasons


def _estimate_servings(recipe: Recipe, known_weight: float) -> tuple[float, list[int]]:
    if known_weight <= 0:
        return 1.0, [1, 1]
    category = classify_recipe(recipe)
    portion = PORTION_GRAMS.get(category, 200.0)
    estimate = max(1.0, min(10.0, round(known_weight / portion, 1)))
    lower = max(1, math.floor(estimate - 1))
    upper = min(10, math.ceil(estimate + 1))
    return estimate, [lower, upper]
