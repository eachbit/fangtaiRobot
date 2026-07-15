from __future__ import annotations

import re
from typing import Any

from .health_rules import check_recipe
from .models import Constraints, Recipe, UserProfile
from .nutrition_calculator import calculate_recipe_nutrition
from .planner import finalize_menu
from .recipe_features import analyze_recipe, classify_recipe, is_bad_breakfast
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
    structure_targets = _structure_targets(constraints)
    kept_structure_counts = {"meat": 0, "vegetable": 0}

    old_items = old_menu if structure_targets is not None else old_menu[:desired_size]
    for index, item in enumerate(old_items):
        recipe = recipe_by_id.get(item["id"])
        failure = _old_item_failure(
            index,
            recipe,
            constraints,
            user,
            requested_positions,
            structure_targets,
            kept_structure_counts,
            len(slots),
            desired_size,
        )
        if failure:
            if structure_targets is None:
                slots.append(None)
            removed.append((index, item, recipe, failure))
            continue
        slots.append(item)
        used_ids.add(item["id"])

    while len(slots) < desired_size:
        slots.append(None)

    ranked_limit = (
        len(recipes)
        if structure_targets is not None or constraints.minimum_cooking_methods
        else max(50, desired_size * 10)
    )
    ranked = rank_recipes(recipes, constraints, user, limit=ranked_limit)
    original_ids = {item["id"] for item in old_menu}
    replacements: list[dict[str, Any]] = []
    for index, old_item, old_recipe, reason in removed:
        target_index = (
            index
            if structure_targets is None and index < len(slots)
            else _first_empty_slot(slots)
        )
        if target_index is None:
            continue
        required_style = _next_required_style(structure_targets, kept_structure_counts, old_recipe)
        candidate = _pick_candidate(
            ranked,
            used_ids | original_ids,
            classify_recipe(old_recipe) if old_recipe else None,
            required_style,
        )
        if candidate is None:
            continue
        new_item = _menu_item(candidate)
        slots[target_index] = new_item
        used_ids.add(new_item["id"])
        _record_structure_choice(kept_structure_counts, candidate["recipe"])
        replacements.append({"old_id": old_item["id"], "new_id": new_item["id"], "reason": reason})

    for index, item in enumerate(slots):
        if item is not None:
            continue
        required_style = _next_required_style(structure_targets, kept_structure_counts, None)
        candidate = _pick_candidate(
            ranked,
            used_ids | original_ids,
            None,
            required_style,
        )
        if candidate is None:
            continue
        new_item = _menu_item(candidate)
        slots[index] = new_item
        used_ids.add(new_item["id"])
        _record_structure_choice(kept_structure_counts, candidate["recipe"])

    if constraints.minimum_cooking_methods:
        replacements.extend(
            _enforce_cooking_diversity(
                slots,
                ranked,
                used_ids,
                original_ids,
                recipe_by_id,
                structure_targets,
                constraints.minimum_cooking_methods,
            )
        )

    menu = [item for item in slots if item is not None]
    warnings = _collect_menu_warnings(menu, recipe_by_id, constraints, user)
    kept_ids = [item["id"] for item in menu if item["id"] in {old["id"] for old in old_menu}]
    changes = {
        "mode": "minimal_revision",
        "kept_dishes": kept_ids,
        "replaced_dishes": replacements,
        "change_count": max(len(old_menu), len(menu)) - len(kept_ids),
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


def _old_item_failure(
    index: int,
    recipe: Recipe | None,
    constraints: Constraints,
    user: UserProfile | None,
    requested_positions: set[int],
    structure_targets: dict[str, int] | None,
    structure_counts: dict[str, int],
    kept_count: int,
    desired_size: int,
) -> str | None:
    if index in requested_positions:
        return "用户明确要求替换该位置菜品"
    failure = _revision_failure(recipe, constraints, user)
    if failure is not None or structure_targets is None or recipe is None:
        return failure
    if kept_count >= desired_size:
        return "减少菜品数量"
    style = analyze_recipe(recipe).protein_style
    if style not in structure_targets or structure_counts[style] >= structure_targets[style]:
        return "新增荤素搭配约束"
    structure_counts[style] += 1
    return None


def _first_empty_slot(slots: list[dict[str, Any] | None]) -> int | None:
    for index, item in enumerate(slots):
        if item is None:
            return index
    return None


def _pick_candidate(
    ranked: list[dict],
    used_ids: set[int],
    category: str | None,
    protein_style: str | None = None,
) -> dict | None:
    available = [item for item in ranked if item["recipe"].id not in used_ids]
    if protein_style:
        same_style = [
            item
            for item in available
            if analyze_recipe(item["recipe"]).protein_style == protein_style
        ]
        return same_style[0] if same_style else None
    if category:
        same_category = [item for item in available if classify_recipe(item["recipe"]) == category]
        if same_category:
            return same_category[0]
    return available[0] if available else None


def _structure_targets(constraints: Constraints) -> dict[str, int] | None:
    if (
        constraints.requested_meat_count is None
        or constraints.requested_vegetable_count is None
    ):
        return None
    if (
        constraints.requested_dish_count is not None
        and constraints.requested_meat_count + constraints.requested_vegetable_count
        != constraints.requested_dish_count
    ):
        return None
    return {
        "meat": constraints.requested_meat_count,
        "vegetable": constraints.requested_vegetable_count,
    }


def _next_required_style(
    targets: dict[str, int] | None,
    counts: dict[str, int],
    old_recipe: Recipe | None,
) -> str | None:
    if targets is None:
        return None
    old_style = analyze_recipe(old_recipe).protein_style if old_recipe else None
    if old_style in targets and counts[old_style] < targets[old_style]:
        return old_style
    for style in ("meat", "vegetable"):
        if counts[style] < targets[style]:
            return style
    return None


def _record_structure_choice(counts: dict[str, int], recipe: Recipe) -> None:
    style = analyze_recipe(recipe).protein_style
    if style in counts:
        counts[style] += 1


def _enforce_cooking_diversity(
    slots: list[dict[str, Any] | None],
    ranked: list[dict],
    used_ids: set[int],
    original_ids: set[int],
    recipe_by_id: dict[int, Recipe],
    structure_targets: dict[str, int] | None,
    minimum_methods: int,
) -> list[dict[str, Any]]:
    replacements: list[dict[str, Any]] = []
    while True:
        methods = [
            analyze_recipe(recipe_by_id[item["id"]]).cooking_method
            for item in slots
            if item is not None and item["id"] in recipe_by_id
        ]
        used_methods = set(methods) - {"unknown"}
        if len(used_methods) >= minimum_methods:
            return replacements

        method_counts = {method: methods.count(method) for method in set(methods)}
        replaceable: list[tuple[int, dict[str, Any], Recipe]] = []
        for index, item in enumerate(slots):
            if item is None:
                continue
            recipe = recipe_by_id.get(item["id"])
            if recipe is None:
                continue
            method = analyze_recipe(recipe).cooking_method
            if method == "unknown" or method_counts.get(method, 0) > 1:
                replaceable.append((index, item, recipe))
        replaceable.sort(
            key=lambda value: (
                value[1]["id"] in original_ids,
                analyze_recipe(value[2]).cooking_method != "unknown",
                value[0],
            )
        )

        chosen: tuple[int, dict[str, Any], Recipe, dict] | None = None
        for index, old_item, old_recipe in replaceable:
            protein_style = (
                analyze_recipe(old_recipe).protein_style
                if structure_targets is not None
                else None
            )
            candidate = _pick_method_candidate(
                ranked,
                used_ids | original_ids,
                used_methods,
                protein_style,
            )
            if candidate is not None:
                chosen = index, old_item, old_recipe, candidate
                break
        if chosen is None:
            return replacements

        index, old_item, _, candidate = chosen
        new_item = _menu_item(candidate)
        slots[index] = new_item
        used_ids.discard(old_item["id"])
        used_ids.add(new_item["id"])
        if old_item["id"] in original_ids:
            replacements.append(
                {
                    "old_id": old_item["id"],
                    "new_id": new_item["id"],
                    "reason": "新增烹饪方式多样性约束",
                }
            )


def _pick_method_candidate(
    ranked: list[dict],
    blocked_ids: set[int],
    used_methods: set[str],
    protein_style: str | None,
) -> dict | None:
    for item in ranked:
        recipe = item["recipe"]
        if recipe.id in blocked_ids:
            continue
        feature = analyze_recipe(recipe)
        if feature.cooking_method in used_methods | {"unknown"}:
            continue
        if protein_style and feature.protein_style != protein_style:
            continue
        return item
    return None


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
