from __future__ import annotations

from .health_rules import check_recipe
from .models import Constraints, Recipe, UserProfile


MEAL_COMPATIBILITY = {
    "早餐": ["早餐", "主食", "粥", "面", "点心"],
    "午餐": ["午餐", "主食", "下饭", "上班族"],
    "晚餐": ["晚餐", "清淡", "家常", "汤"],
    "夜宵": ["夜宵", "粥", "面", "汤", "热"],
}

HEALTH_LABEL_HINTS = {
    "减脂": ["减脂", "低脂", "清淡", "健身"],
    "增肌": ["增肌", "蛋白", "牛肉", "鸡肉", "鱼", "虾"],
    "补钙": ["补钙", "牛奶", "芝士", "豆腐", "虾皮"],
    "补铁": ["补铁", "补气血", "牛肉", "红枣", "菠菜"],
    "控糖": ["控糖", "低糖", "清淡"],
    "降压": ["降压", "清淡"],
    "降尿酸": ["低嘌呤", "清淡"],
    "健胃消食": ["健胃消食", "养胃", "粥", "汤"],
}


def rank_recipes(
    recipes: list[Recipe],
    constraints: Constraints,
    user: UserProfile | None,
    limit: int = 18,
) -> list[dict]:
    scored: list[dict] = []
    for recipe in recipes:
        health = check_recipe(recipe, constraints, user)
        if not health["passed"]:
            continue
        score, reasons = score_recipe(recipe, constraints)
        if score <= 0:
            continue
        scored.append(
            {
                "recipe": recipe,
                "score": score,
                "reasons": reasons,
                "warnings": health["warnings"],
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def score_recipe(recipe: Recipe, constraints: Constraints) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    labels_text = " ".join(recipe.labels)
    all_text = f"{recipe.name} {recipe.ingredients} {labels_text}"

    if constraints.meal:
        hints = MEAL_COMPATIBILITY.get(constraints.meal, [constraints.meal])
        if constraints.meal in labels_text or any(hint in all_text for hint in hints):
            score += 16
            reasons.append(f"适合{constraints.meal}")

    if constraints.taste and constraints.taste in all_text:
        score += 14
        reasons.append(f"匹配{constraints.taste}口味")
    elif constraints.taste == "清淡" and ("清淡" in all_text or "汤" in recipe.name):
        score += 12
        reasons.append("整体偏清淡")

    for goal in constraints.health_goals:
        hints = HEALTH_LABEL_HINTS.get(goal, [goal])
        if any(hint in all_text for hint in hints):
            score += 12
            reasons.append(f"匹配健康需求：{goal}")

    for ingredient in constraints.preferred_ingredients:
        if ingredient in all_text:
            score += 10
            reasons.append(f"包含偏好食材：{ingredient}")

    if constraints.difficulty == "简单":
        step_count = recipe.steps.count("第")
        if step_count <= 5:
            score += 8
            reasons.append("步骤较少，适合简单快手场景")

    if constraints.max_minutes:
        minutes = _extract_minutes(recipe.steps)
        if minutes is not None and minutes <= constraints.max_minutes:
            score += 10
            reasons.append(f"预计烹饪时间不超过{constraints.max_minutes}分钟")
        elif minutes is None and constraints.difficulty == "简单":
            score += 3

    if constraints.scene == "便当" and any(word in all_text for word in ["午餐", "上班族", "主食"]):
        score += 8
        reasons.append("适合带去公司")
    if constraints.scene == "聚餐" and any(word in all_text for word in ["晚餐", "宴客", "地方风味"]):
        score += 6
        reasons.append("适合聚餐或正式场景")
    if constraints.scene == "夏季清爽" and any(word in all_text for word in ["清淡", "凉", "汤", "蔬菜"]):
        score += 8
        reasons.append("适合夏季清爽需求")

    if not reasons:
        if constraints.meal is None and constraints.health_goals:
            score += 3
        elif constraints.meal is None:
            score += 1

    return score, reasons


def _extract_minutes(steps: str) -> int | None:
    import re

    values = [int(match) for match in re.findall(r"(\d+)\s*分钟", steps)]
    if not values:
        return None
    return sum(values)
