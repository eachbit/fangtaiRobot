from __future__ import annotations

from .models import Constraints, Recipe, UserProfile
from .recipe_features import classify_recipe
from .retriever import rank_recipes


def plan_meal(recipes: list[Recipe], constraints: Constraints, user: UserProfile | None) -> dict:
    ranked = rank_recipes(recipes, constraints, user)
    menu_size = _menu_size(constraints)
    selected = _diverse_select(ranked, menu_size)
    warnings = _collect_warnings(selected)

    if not selected and recipes:
        fallback_ranked = rank_recipes(recipes, constraints, user, limit=5)
        selected = fallback_ranked[:1]
        warnings.append("未找到完全理想方案，已返回最接近的官方菜谱。")

    menu = []
    for item in selected:
        recipe = item["recipe"]
        menu.append(
            {
                "id": recipe.id,
                "name": recipe.name,
                "ingredients": recipe.ingredients,
                "steps": recipe.steps,
                "labels": recipe.labels,
                "score": item["score"],
                "reason": "；".join(item["reasons"]) if item["reasons"] else "来自官方菜谱库，适合作为备选菜品。",
            }
        )

    score_card = build_score_card(menu, constraints, selected)
    return {
        "menu": menu,
        "score_card": score_card,
        "warnings": warnings,
        "answer": build_answer(menu, constraints, warnings),
    }


def _menu_size(constraints: Constraints) -> int:
    if constraints.requested_dish_count:
        return max(1, min(constraints.requested_dish_count, 8))
    people_count = constraints.people_count
    if people_count is None or people_count <= 1:
        return 2
    if people_count <= 2:
        return 3
    if people_count <= 4:
        return 4
    return 6


def _diverse_select(ranked: list[dict], size: int) -> list[dict]:
    if size >= 3:
        return _balanced_select(ranked, size)
    selected: list[dict] = []
    used_names: set[str] = set()
    for item in ranked:
        recipe = item["recipe"]
        if recipe.name in used_names:
            continue
        selected.append(item)
        used_names.add(recipe.name)
        if len(selected) >= size:
            break
    return selected


def _balanced_select(ranked: list[dict], size: int) -> list[dict]:
    selected: list[dict] = []
    used_names: set[str] = set()
    category_targets = _category_targets(size)
    for category in category_targets:
        item = _first_by_category(ranked, category, used_names)
        if item:
            selected.append(item)
            used_names.add(item["recipe"].name)
    for item in ranked:
        recipe = item["recipe"]
        if recipe.name in used_names:
            continue
        if classify_recipe(recipe) == "dessert" and len(selected) < max(1, size - 1):
            continue
        selected.append(item)
        used_names.add(recipe.name)
        if len(selected) >= size:
            break
    return selected[:size]


def _category_targets(size: int) -> list[str]:
    if size <= 3:
        return ["staple", "meat", "vegetable"]
    if size == 4:
        return ["staple", "meat", "vegetable", "soup"]
    return ["staple", "meat", "meat", "vegetable", "vegetable", "soup"]


def _first_by_category(ranked: list[dict], category: str, used_names: set[str]) -> dict | None:
    for item in ranked:
        recipe = item["recipe"]
        if recipe.name in used_names:
            continue
        if classify_recipe(recipe) == category:
            return item
    return None


def _collect_warnings(selected: list[dict]) -> list[str]:
    warnings: list[str] = []
    for item in selected:
        for warning in item.get("warnings", []):
            if warning not in warnings:
                warnings.append(warning)
    return warnings


def build_score_card(menu: list[dict], constraints: Constraints, selected: list[dict]) -> dict:
    total = len(menu)
    health_hits = sum(1 for item in selected if any("健康需求" in reason for reason in item["reasons"]))
    taste_hits = sum(1 for item in selected if any("口味" in reason or "清淡" in reason for reason in item["reasons"]))
    scenario_hits = sum(
        1
        for item in selected
        if any("适合" in reason or "时间" in reason or "步骤" in reason for reason in item["reasons"])
    )
    return {
        "official_recipe": total > 0,
        "allergy_passed": True,
        "health_match": _level(health_hits, total) if constraints.health_goals else "not_required",
        "taste_match": _level(taste_hits, total) if constraints.taste else "not_required",
        "scenario_match": _level(scenario_hits, total),
        "minimal_change": True,
        "menu_count": total,
    }


def _level(hit: int, total: int) -> str:
    if total <= 0:
        return "low"
    ratio = hit / total
    if ratio >= 0.67:
        return "high"
    if ratio >= 0.34:
        return "medium"
    return "low"


def build_answer(menu: list[dict], constraints: Constraints, warnings: list[str]) -> str:
    if not menu:
        return "暂未找到合适的官方菜谱，请减少限制条件后重试。"
    meal = constraints.meal or "这一餐"
    names = "、".join(item["name"] for item in menu)
    parts = [f"建议{meal}安排：{names}。"]
    if constraints.health_goals:
        parts.append(f"已优先考虑：{'、'.join(constraints.health_goals)}。")
    if constraints.allergens:
        parts.append(f"已避开过敏食材：{'、'.join(constraints.allergens)}。")
    if constraints.avoid_ingredients:
        parts.append(f"已避开忌口食材：{'、'.join(constraints.avoid_ingredients)}。")
    if warnings:
        parts.append(f"注意：{'；'.join(warnings)}")
    return "".join(parts)
