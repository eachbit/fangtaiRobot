from __future__ import annotations

from typing import Any

from .models import Constraints, UserProfile
from .nutrition_repository import NUTRIENT_KEYS
from .nutrition_targets import build_nutrition_targets


def score_table_nutrition(
    dishes: list[dict[str, Any]],
    constraints: Constraints,
    user: UserProfile | None,
) -> dict[str, Any]:
    table_total = {key: 0.0 for key in NUTRIENT_KEYS}
    confidence_scores: list[float] = []
    confidence_levels: list[str] = []
    reasons: list[str] = []
    for dish in dishes:
        for key in NUTRIENT_KEYS:
            table_total[key] += float(dish.get("nutrients", {}).get(key, 0.0))
        confidence = dish.get("confidence", {})
        confidence_scores.append(float(confidence.get("score", 0.0)))
        confidence_levels.append(str(confidence.get("level", "low")))
        for reason in confidence.get("reasons", []):
            if reason not in reasons:
                reasons.append(reason)

    people_count = max(1, constraints.people_count or 1)
    table_total = _round_nutrients(table_total)
    per_person = _round_nutrients(
        {key: value / people_count for key, value in table_total.items()}
    )
    targets = build_nutrition_targets(constraints, user)
    components = {
        key: _component_status(per_person[key], target)
        for key, target in targets.items()
    }
    ratios = _macro_energy_ratios(per_person)
    score = _overall_score(components, ratios)
    confidence_score = min(confidence_scores) if confidence_scores else 0.0
    confidence_level = _lowest_confidence(confidence_levels)
    if confidence_level == "low":
        assessment = "insufficient_data"
    elif score >= 80:
        assessment = "balanced"
    elif score >= 60:
        assessment = "needs_adjustment"
    else:
        assessment = "unbalanced"

    return {
        "table_total": table_total,
        "per_person": per_person,
        "people_count": people_count,
        "targets": targets,
        "components": components,
        "macro_energy_ratios": ratios,
        "score": score,
        "assessment": assessment,
        "confidence": {
            "score": round(confidence_score, 4),
            "level": confidence_level,
            "reasons": reasons or (["没有可计算的菜品营养"] if not dishes else []),
        },
    }


def _component_status(value: float, target: dict[str, float | None]) -> dict[str, Any]:
    minimum = target.get("min")
    maximum = target.get("max")
    if minimum is not None and value < minimum:
        status = "low"
    elif maximum is not None and value > maximum:
        status = "high"
    else:
        status = "within_range"
    return {"value": value, "status": status, "target": target}


def _macro_energy_ratios(values: dict[str, float]) -> dict[str, float]:
    protein_energy = values["protein_g"] * 4
    fat_energy = values["fat_g"] * 9
    carbohydrate_energy = values["carbohydrate_g"] * 4
    total = protein_energy + fat_energy + carbohydrate_energy
    if total <= 0:
        return {"protein": 0.0, "fat": 0.0, "carbohydrate": 0.0}
    return {
        "protein": round(protein_energy / total, 4),
        "fat": round(fat_energy / total, 4),
        "carbohydrate": round(carbohydrate_energy / total, 4),
    }


def _overall_score(components: dict[str, dict[str, Any]], ratios: dict[str, float]) -> int:
    score = 100
    penalties = {
        "kcal": 12,
        "protein_g": 10,
        "fat_g": 8,
        "carbohydrate_g": 8,
        "fiber_g": 8,
        "sugar_g": 12,
        "sodium_mg": 25,
    }
    for key, component in components.items():
        if component["status"] != "within_range":
            score -= penalties[key]
    if ratios["protein"] and not 0.15 <= ratios["protein"] <= 0.35:
        score -= 5
    if ratios["fat"] and not 0.2 <= ratios["fat"] <= 0.4:
        score -= 5
    if ratios["carbohydrate"] and not 0.4 <= ratios["carbohydrate"] <= 0.65:
        score -= 5
    return max(0, score)


def _round_nutrients(values: dict[str, float]) -> dict[str, float]:
    return {key: round(float(values.get(key, 0.0)), 2) for key in NUTRIENT_KEYS}


def _lowest_confidence(levels: list[str]) -> str:
    if not levels or "low" in levels:
        return "low"
    if "medium" in levels:
        return "medium"
    return "high"
