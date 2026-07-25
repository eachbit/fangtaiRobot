from __future__ import annotations

from .health_rules import check_recipe
from .models import Constraints, Recipe, UserProfile
from .nutrition import estimate_menu_nutrition
from .recipe_features import classify_recipe, is_bad_breakfast
from .retriever import rank_recipes, score_recipe


def plan_meal(
    recipes: list[Recipe],
    constraints: Constraints,
    user: UserProfile | None,
    previous_menu_ids: list[int] | None = None,
) -> dict:
    ranked = rank_recipes(recipes, constraints, user, limit=48)
    menu_size = _menu_size(constraints)
    selected = _revision_select(recipes, ranked, constraints, user, menu_size, previous_menu_ids)
    changes = _build_changes(previous_menu_ids, selected)
    warnings = _collect_warnings(selected)

    if not selected and recipes:
        fallback_ranked = rank_recipes(recipes, constraints, user, limit=5)
        selected = fallback_ranked[:1]
        changes = _build_changes(previous_menu_ids, selected)
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

    nutrition = estimate_menu_nutrition(menu, constraints)
    score_card = build_score_card(menu, constraints, selected, changes, nutrition)
    return {
        "menu": menu,
        "nutrition": nutrition,
        "score_card": score_card,
        "changes": changes,
        "warnings": warnings,
        "answer": build_answer(menu, constraints, warnings),
    }


def _revision_select(
    recipes: list[Recipe],
    ranked: list[dict],
    constraints: Constraints,
    user: UserProfile | None,
    size: int,
    previous_menu_ids: list[int] | None,
) -> list[dict]:
    if not previous_menu_ids:
        return _diverse_select(ranked, size)

    by_id = {recipe.id: recipe for recipe in recipes}
    selected: list[dict] = []
    used_names: set[str] = set()
    for recipe_id in previous_menu_ids:
        recipe = by_id.get(recipe_id)
        if not recipe or recipe.name in used_names:
            continue
        item = _existing_item_if_allowed(recipe, constraints, user)
        if item is None:
            continue
        selected.append(item)
        used_names.add(recipe.name)
        if len(selected) >= size:
            return selected[:size]

    if len(selected) >= size:
        return selected[:size]

    remaining = [item for item in ranked if item["recipe"].name not in used_names]
    selected.extend(_diverse_select(remaining, size - len(selected)))
    return selected[:size]


def _existing_item_if_allowed(recipe: Recipe, constraints: Constraints, user: UserProfile | None) -> dict | None:
    health = check_recipe(recipe, constraints, user)
    if not health["passed"]:
        return None
    if constraints.meal == "早餐" and is_bad_breakfast(recipe):
        return None
    score, reasons = score_recipe(recipe, constraints)
    if not reasons:
        reasons = ["保留上一轮已确认菜品"]
    return {
        "recipe": recipe,
        "score": max(score, 1),
        "reasons": reasons,
        "warnings": health["warnings"],
    }


def _build_changes(previous_menu_ids: list[int] | None, selected: list[dict]) -> dict:
    selected_ids = [item["recipe"].id for item in selected]
    if not previous_menu_ids:
        return {
            "mode": "new_menu",
            "kept_dishes": [],
            "replaced_dishes": [],
            "change_count": len(selected_ids),
        }
    kept = [recipe_id for recipe_id in previous_menu_ids if recipe_id in selected_ids]
    removed = [recipe_id for recipe_id in previous_menu_ids if recipe_id not in selected_ids]
    added = [recipe_id for recipe_id in selected_ids if recipe_id not in previous_menu_ids]
    replaced = [
        {"old_id": old_id, "new_id": new_id}
        for old_id, new_id in zip(removed, added)
    ]
    for new_id in added[len(replaced):]:
        replaced.append({"old_id": None, "new_id": new_id})
    return {
        "mode": "minimal_revision",
        "kept_dishes": kept,
        "replaced_dishes": replaced,
        "change_count": len(removed) + max(0, len(added) - len(removed)),
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


def build_score_card(
    menu: list[dict],
    constraints: Constraints,
    selected: list[dict],
    changes: dict | None = None,
    nutrition: dict | None = None,
) -> dict:
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
        "minimal_change": (changes or {}).get("change_count", 0) <= 1,
        "nutrition_balance": (nutrition or {}).get("balance_level", "low"),
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
