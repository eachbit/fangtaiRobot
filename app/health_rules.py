from __future__ import annotations

from .models import Constraints, Recipe, UserProfile


HIGH_PURINE = ["海鲜", "虾", "蟹", "贝", "动物内脏", "猪肝", "牛肉汤", "浓汤"]
HIGH_SUGAR = ["糖", "冰糖", "蜂蜜", "甜", "糖浆", "奶油"]
HIGH_SALT_OR_FAT = ["腊", "咸", "酱", "油炸", "烧烤", "肥肉"]
PREGNANCY_CAUTION = ["酒", "咖啡", "生食", "刺身"]


def check_recipe(recipe: Recipe, constraints: Constraints, user: UserProfile | None) -> dict:
    text = f"{recipe.name} {recipe.ingredients} {' '.join(recipe.labels)}"
    warnings: list[str] = []
    hard_failures: list[str] = []

    for allergen in constraints.allergens:
        if allergen and allergen in text:
            hard_failures.append(f"命中过敏食材：{allergen}")

    for avoid in constraints.avoid_ingredients:
        if avoid and avoid in text:
            hard_failures.append(f"命中用户明确不想吃的食材：{avoid}")

    if "辣" in constraints.avoid_tastes and ("辣" in text or "重口味" in text):
        hard_failures.append("用户要求不辣，但菜谱标签或食材包含辣味")

    special_groups = user.special_groups if user else []
    if "高尿酸" in special_groups and _contains_any(text, HIGH_PURINE):
        warnings.append("用户高尿酸，建议谨慎选择高嘌呤相关食材")
    if "高血糖" in special_groups and _contains_any(text, HIGH_SUGAR):
        warnings.append("用户高血糖，建议控制糖分")
    if "高血压" in special_groups and _contains_any(text, HIGH_SALT_OR_FAT):
        warnings.append("用户高血压，建议控制盐油摄入")
    if ("孕妇" in special_groups or "备孕" in special_groups) and _contains_any(text, PREGNANCY_CAUTION):
        warnings.append("孕期或备孕用户，建议避免风险食材")

    return {
        "passed": not hard_failures,
        "hard_failures": hard_failures,
        "warnings": warnings,
    }


def _contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)
