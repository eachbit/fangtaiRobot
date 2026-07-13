from __future__ import annotations

from copy import deepcopy

from .models import Constraints, UserProfile


MEAL_TARGETS = {
    "早餐": {
        "kcal": {"min": 350.0, "max": 600.0},
        "protein_g": {"min": 18.0, "max": 40.0},
        "fat_g": {"min": 10.0, "max": 25.0},
        "carbohydrate_g": {"min": 45.0, "max": 85.0},
        "fiber_g": {"min": 6.0, "max": None},
        "sugar_g": {"min": None, "max": 15.0},
        "sodium_mg": {"min": None, "max": 700.0},
    },
    "午餐": {
        "kcal": {"min": 500.0, "max": 850.0},
        "protein_g": {"min": 25.0, "max": 55.0},
        "fat_g": {"min": 12.0, "max": 32.0},
        "carbohydrate_g": {"min": 55.0, "max": 110.0},
        "fiber_g": {"min": 8.0, "max": None},
        "sugar_g": {"min": None, "max": 20.0},
        "sodium_mg": {"min": None, "max": 900.0},
    },
    "晚餐": {
        "kcal": {"min": 450.0, "max": 750.0},
        "protein_g": {"min": 25.0, "max": 50.0},
        "fat_g": {"min": 10.0, "max": 28.0},
        "carbohydrate_g": {"min": 45.0, "max": 90.0},
        "fiber_g": {"min": 8.0, "max": None},
        "sugar_g": {"min": None, "max": 18.0},
        "sodium_mg": {"min": None, "max": 800.0},
    },
}


def build_nutrition_targets(
    constraints: Constraints,
    user: UserProfile | None,
) -> dict[str, dict[str, float | None]]:
    meal = constraints.meal if constraints.meal in MEAL_TARGETS else "晚餐"
    targets = deepcopy(MEAL_TARGETS[meal])
    special_groups = set(constraints.inferred_profile.get("special_groups", []))
    goals = set(constraints.health_goals)
    if user:
        special_groups.update(user.special_groups)
        goals.update(user.health_goals)

    if "高血压" in special_groups or "降压" in goals:
        targets["sodium_mg"]["max"] = 600.0
    if "高血糖" in special_groups or "控糖" in goals:
        targets["sugar_g"]["max"] = 10.0
        targets["carbohydrate_g"]["max"] = min(
            float(targets["carbohydrate_g"]["max"]), 75.0
        )
    if "增肌" in goals:
        targets["protein_g"]["min"] = 35.0
    if "减脂" in goals:
        targets["kcal"]["max"] = min(float(targets["kcal"]["max"]), 650.0)
        targets["fat_g"]["max"] = min(float(targets["fat_g"]["max"]), 22.0)
    return targets
