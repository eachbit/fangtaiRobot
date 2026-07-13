from __future__ import annotations

import re
from typing import Any

from .health_rules import check_recipe
from .models import Constraints, Recipe, UserProfile
from .nutrition_calculator import calculate_recipe_nutrition
from .planner import finalize_menu
from .recipe_features import classify_recipe, is_bad_breakfast
from .retriever import rank_recipes


FULL_RESET_TERMS = ("全部换掉", "全部换", "重新推荐", "换一桌", "全换")
POSITION_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}


def is_full_reset(messages: list[str]) -> bool:
    latest = messages[-1] if messages else ""
    return any(term in latest for term in FULL_RESET_TERMS)


def revise_menu(
    recipes: list[Recipe],
    old_result: dict[str, Any],
    constraints: Constraints,
    user: UserProfile | None,
) -> dict[str, Any]:
    recipe_by_id = {recipe.id: recipe for recipe in recipes}
    old_menu = old_result.get("menu", [])
    desired_size = constraints.requested_dish_count or len(old_menu)
    desired_size = max(1, min(desired_size, 8))
    slots: list[dict[str, Any] | None] = []
    removed: list[tuple[int, dict[str, Any], Recipe | None, str]] = []
    used_ids: set[int] = set()
    requested_positions = _requested_replacement_positions(constraints.raw_messages)

    for index, item in enumerate(old_menu[:desired_size]):
        recipe = recipe_by_id.get(item["id"])
        failure = (
            "用户明确要求替换该位置菜品"
            if index in requested_positions
            else _revision_failure(recipe, constraints, user)
        )
        if failure:
            slots.append(None)
            removed.append((index, item, recipe, failure))
        else:
            slots.append(item)
            used_ids.add(item["id"])

    while len(slots) < desired_size:
        slots.append(None)

    ranked = rank_recipes(recipes, constraints, user, limit=max(50, desired_size * 10))
    original_ids = {item["id"] for item in old_menu}
    replacements: list[dict[str, Any]] = []
    for index, old_item, old_recipe, reason in removed:
        candidate = _pick_candidate(
            ranked,
            used_ids | original_ids,
            classify_recipe(old_recipe) if old_recipe else None,
        )
        if candidate is None:
            continue
        new_item = _menu_item(candidate)
        slots[index] = new_item
        used_ids.add(new_item["id"])
        replacements.append({"old_id": old_item["id"], "new_id": new_item["id"], "reason": reason})

    for index, item in enumerate(slots):
        if item is not None:
            continue
        candidate = _pick_candidate(ranked, used_ids | original_ids, None)
        if candidate is None:
            continue
        new_item = _menu_item(candidate)
        slots[index] = new_item
        used_ids.add(new_item["id"])

    menu = [item for item in slots if item is not None]
    warnings = _collect_menu_warnings(menu, recipe_by_id, constraints, user)
    kept_ids = [item["id"] for item in menu if item["id"] in {old["id"] for old in old_menu}]
    changes = {
        "mode": "minimal_revision",
        "kept_dishes": kept_ids,
        "replaced_dishes": replacements,
        "change_count": (
            len(replacements)
            + max(0, len(old_menu) - desired_size)
            + max(0, desired_size - len(old_menu))
        ),
    }
    return finalize_menu(
        menu,
        constraints,
        user,
        warnings=warnings,
        changes=changes,
        minimal_change=True,
    )


def _revision_failure(
    recipe: Recipe | None,
    constraints: Constraints,
    user: UserProfile | None,
) -> str | None:
    if recipe is None:
        return "原菜谱不存在于官方菜谱库"
    health = check_recipe(recipe, constraints, user)
    if not health["passed"]:
        return "；".join(health["hard_failures"])
    if constraints.meal == "早餐" and is_bad_breakfast(recipe):
        return "新增早餐场景约束"
    return None


def _pick_candidate(ranked: list[dict], used_ids: set[int], category: str | None) -> dict | None:
    available = [item for item in ranked if item["recipe"].id not in used_ids]
    if category:
        same_category = [item for item in available if classify_recipe(item["recipe"]) == category]
        if same_category:
            return same_category[0]
    return available[0] if available else None


def _requested_replacement_positions(messages: list[str]) -> set[int]:
    latest = messages[-1] if messages else ""
    positions: set[int] = set()
    for match in re.finditer(r"第([一二两三四五六七八\d]+)道[^，。；]*(?:换掉|替换|换一个)", latest):
        value = match.group(1)
        number = int(value) if value.isdigit() else POSITION_NUMBERS.get(value)
        if number and number > 0:
            positions.add(number - 1)
    return positions


def _collect_menu_warnings(
    menu: list[dict[str, Any]],
    recipe_by_id: dict[int, Recipe],
    constraints: Constraints,
    user: UserProfile | None,
) -> list[str]:
    warnings: list[str] = []
    for item in menu:
        recipe = recipe_by_id.get(item["id"])
        if recipe is None:
            continue
        for warning in check_recipe(recipe, constraints, user)["warnings"]:
            if warning not in warnings:
                warnings.append(warning)
    return warnings


def _menu_item(item: dict[str, Any]) -> dict[str, Any]:
    recipe = item["recipe"]
    return {
        "id": recipe.id,
        "name": recipe.name,
        "ingredients": recipe.ingredients,
        "steps": recipe.steps,
        "labels": recipe.labels,
        "score": item["score"],
        "reason": "；".join(item["reasons"]) if item["reasons"] else "满足新增约束的官方菜谱。",
        "nutrition": calculate_recipe_nutrition(recipe),
    }
