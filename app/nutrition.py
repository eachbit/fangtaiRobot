from __future__ import annotations

import re
from typing import Any

from .models import Constraints


NUTRIENT_ZERO = {
    "kcal": 0.0,
    "protein_g": 0.0,
    "fat_g": 0.0,
    "carbohydrate_g": 0.0,
    "fiber_g": 0.0,
    "sugar_g": 0.0,
    "sodium_mg": 0.0,
}


# Conservative per-100g estimates for common ingredients in the official recipe set.
# This is an offline fallback table, not clinical nutrition data.
NUTRIENT_TABLE: list[tuple[str, dict[str, float]]] = [
    ("马苏里拉芝士", {"kcal": 280, "protein_g": 22, "fat_g": 17, "carbohydrate_g": 3, "fiber_g": 0, "sugar_g": 2, "sodium_mg": 620}),
    ("番茄酱", {"kcal": 95, "protein_g": 1.7, "fat_g": 0.3, "carbohydrate_g": 23, "fiber_g": 1, "sugar_g": 18, "sodium_mg": 900}),
    ("橄榄油", {"kcal": 884, "protein_g": 0, "fat_g": 100, "carbohydrate_g": 0, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 2}),
    ("蝴蝶面", {"kcal": 350, "protein_g": 12, "fat_g": 1.5, "carbohydrate_g": 72, "fiber_g": 3, "sugar_g": 3, "sodium_mg": 8}),
    ("西葫芦", {"kcal": 19, "protein_g": 1.2, "fat_g": 0.3, "carbohydrate_g": 3.1, "fiber_g": 1.1, "sugar_g": 2.5, "sodium_mg": 8}),
    ("罗汉果", {"kcal": 169, "protein_g": 13, "fat_g": 0.8, "carbohydrate_g": 65, "fiber_g": 12, "sugar_g": 0, "sodium_mg": 25}),
    ("红枣", {"kcal": 264, "protein_g": 3.2, "fat_g": 0.5, "carbohydrate_g": 67, "fiber_g": 6.2, "sugar_g": 55, "sodium_mg": 6}),
    ("冰糖", {"kcal": 397, "protein_g": 0, "fat_g": 0, "carbohydrate_g": 99, "fiber_g": 0, "sugar_g": 99, "sodium_mg": 0}),
    ("牛肉", {"kcal": 125, "protein_g": 20, "fat_g": 4.2, "carbohydrate_g": 0, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 55}),
    ("牛筋", {"kcal": 151, "protein_g": 35, "fat_g": 0.5, "carbohydrate_g": 2.6, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 60}),
    ("咸肉", {"kcal": 390, "protein_g": 16, "fat_g": 35, "carbohydrate_g": 2, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 1600}),
    ("鸡蛋", {"kcal": 143, "protein_g": 13, "fat_g": 9.5, "carbohydrate_g": 0.7, "fiber_g": 0, "sugar_g": 0.4, "sodium_mg": 140}),
    ("乳鸽", {"kcal": 201, "protein_g": 16, "fat_g": 14, "carbohydrate_g": 0, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 70}),
    ("鲈鱼", {"kcal": 105, "protein_g": 19, "fat_g": 3, "carbohydrate_g": 0, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 70}),
    ("鲍鱼", {"kcal": 84, "protein_g": 17, "fat_g": 0.8, "carbohydrate_g": 2, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 300}),
    ("海蛎", {"kcal": 68, "protein_g": 7, "fat_g": 2.5, "carbohydrate_g": 4, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 250}),
    ("牡蛎", {"kcal": 68, "protein_g": 7, "fat_g": 2.5, "carbohydrate_g": 4, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 250}),
    ("花甲", {"kcal": 45, "protein_g": 7.7, "fat_g": 0.6, "carbohydrate_g": 2.6, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 420}),
    ("鲜虾", {"kcal": 93, "protein_g": 18.6, "fat_g": 0.8, "carbohydrate_g": 2.8, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 165}),
    ("虾", {"kcal": 93, "protein_g": 18.6, "fat_g": 0.8, "carbohydrate_g": 2.8, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 165}),
    ("生菜", {"kcal": 15, "protein_g": 1.4, "fat_g": 0.2, "carbohydrate_g": 2.9, "fiber_g": 1.3, "sugar_g": 0.8, "sodium_mg": 28}),
    ("茄子", {"kcal": 25, "protein_g": 1, "fat_g": 0.2, "carbohydrate_g": 5.9, "fiber_g": 3, "sugar_g": 3.5, "sodium_mg": 2}),
    ("番茄", {"kcal": 18, "protein_g": 0.9, "fat_g": 0.2, "carbohydrate_g": 3.9, "fiber_g": 1.2, "sugar_g": 2.6, "sodium_mg": 5}),
    ("洋葱", {"kcal": 40, "protein_g": 1.1, "fat_g": 0.1, "carbohydrate_g": 9.3, "fiber_g": 1.7, "sugar_g": 4.2, "sodium_mg": 4}),
    ("彩椒", {"kcal": 31, "protein_g": 1, "fat_g": 0.3, "carbohydrate_g": 6, "fiber_g": 2.1, "sugar_g": 4.2, "sodium_mg": 4}),
    ("梨", {"kcal": 57, "protein_g": 0.4, "fat_g": 0.1, "carbohydrate_g": 15, "fiber_g": 3.1, "sugar_g": 10, "sodium_mg": 1}),
    ("姜", {"kcal": 80, "protein_g": 1.8, "fat_g": 0.8, "carbohydrate_g": 18, "fiber_g": 2, "sugar_g": 1.7, "sodium_mg": 13}),
    ("盐", {"kcal": 0, "protein_g": 0, "fat_g": 0, "carbohydrate_g": 0, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 39300}),
    ("糖", {"kcal": 397, "protein_g": 0, "fat_g": 0, "carbohydrate_g": 99, "fiber_g": 0, "sugar_g": 99, "sodium_mg": 0}),
    ("油", {"kcal": 884, "protein_g": 0, "fat_g": 100, "carbohydrate_g": 0, "fiber_g": 0, "sugar_g": 0, "sodium_mg": 2}),
]

DEFAULT_GRAMS = 80.0
UNIT_GRAMS = {
    "个": 80.0,
    "只": 80.0,
    "枚": 50.0,
    "勺": 10.0,
    "匙": 5.0,
    "毫升": 1.0,
    "ml": 1.0,
}


def estimate_menu_nutrition(menu: list[dict[str, Any]], constraints: Constraints) -> dict[str, Any]:
    dishes = [_estimate_dish(item) for item in menu]
    totals = dict(NUTRIENT_ZERO)
    matched_grams = 0.0
    estimated_grams = 0.0
    missing: list[str] = []
    for dish in dishes:
        _add_into(totals, dish["totals"])
        matched_grams += dish["matched_grams"]
        estimated_grams += dish["estimated_grams"]
        for item in dish["missing_ingredients"]:
            if item not in missing:
                missing.append(item)

    people_count = constraints.people_count or 1
    rounded_totals = _rounded_nutrients(totals)
    per_person = {key: _round(value / people_count) for key, value in rounded_totals.items()}
    coverage_ratio = matched_grams / estimated_grams if estimated_grams else 0.0
    coverage_ratio = round(max(0.0, min(1.0, coverage_ratio)), 2)
    return {
        "dishes": dishes,
        "table": {
            "dish_count": len(menu),
            "people_count": people_count,
            "totals": rounded_totals,
        },
        "per_person": per_person,
        "balance_level": _balance_level(per_person, constraints),
        "confidence": {
            "level": _confidence_level(coverage_ratio),
            "coverage_ratio": coverage_ratio,
            "matched_grams": _round(matched_grams),
            "estimated_total_grams": _round(estimated_grams),
            "missing_ingredients": missing[:12],
            "assumptions": [
                "按菜谱食材文本中的克/毫升/个等单位估算",
                "未识别明确用量时采用保守默认份量",
                "结果用于竞赛解释和排序，不作为医学诊断",
            ],
        },
    }


def _estimate_dish(item: dict[str, Any]) -> dict[str, Any]:
    ingredients_text = item.get("ingredients", "")
    totals = dict(NUTRIENT_ZERO)
    matched: list[dict[str, Any]] = []
    used_spans: list[tuple[int, int]] = []
    for term, nutrients in sorted(NUTRIENT_TABLE, key=lambda value: len(value[0]), reverse=True):
        for match in re.finditer(re.escape(term), ingredients_text):
            if _overlaps(match.span(), used_spans):
                continue
            grams, source = _extract_grams(ingredients_text, match.end())
            contribution = _scale(nutrients, grams)
            _add_into(totals, contribution)
            matched.append({"name": term, "grams": _round(grams), "amount_source": source})
            used_spans.append(match.span())

    estimated_grams = _estimate_total_grams(ingredients_text, matched)
    matched_grams = sum(item["grams"] for item in matched)
    missing = _missing_ingredient_samples(ingredients_text, matched)
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "totals": _rounded_nutrients(totals),
        "matched_ingredients": matched[:16],
        "missing_ingredients": missing[:6],
        "matched_grams": _round(matched_grams),
        "estimated_grams": _round(estimated_grams),
    }


def _extract_grams(text: str, offset: int) -> tuple[float, str]:
    nearby = text[offset : offset + 18]
    match = re.match(r"\D*?(\d+(?:\.\d+)?)\s*(g|克|毫升|ml|个|只|枚|勺|匙)?", nearby, flags=re.IGNORECASE)
    if not match:
        return DEFAULT_GRAMS, "default"
    amount = float(match.group(1))
    unit = (match.group(2) or "克").lower()
    if unit in {"g", "克"}:
        return amount, "explicit"
    return amount * UNIT_GRAMS.get(unit, DEFAULT_GRAMS), "unit_estimated"


def _estimate_total_grams(text: str, matched: list[dict[str, Any]]) -> float:
    explicit = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*(?:g|克|毫升|ml)", text, flags=re.IGNORECASE)]
    explicit_total = sum(explicit)
    matched_total = sum(item["grams"] for item in matched)
    return max(explicit_total, matched_total, DEFAULT_GRAMS * max(1, len(_ingredient_chunks(text))))


def _missing_ingredient_samples(text: str, matched: list[dict[str, Any]]) -> list[str]:
    matched_names = {item["name"] for item in matched}
    samples: list[str] = []
    for chunk in _ingredient_chunks(text):
        clean = re.sub(r"\d+(?:\.\d+)?\s*(?:g|克|毫升|ml|个|只|枚|勺|匙)", "", chunk, flags=re.IGNORECASE)
        clean = re.sub(r"^[A-Z]料[:：]?", "", clean).strip(" ：:；;（）()、")
        if len(clean) < 2 or any(name in clean for name in matched_names):
            continue
        if clean not in samples:
            samples.append(clean)
    return samples


def _ingredient_chunks(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"[；;，,、\n]", text) if chunk.strip()]


def _scale(nutrients: dict[str, float], grams: float) -> dict[str, float]:
    return {key: nutrients.get(key, 0.0) * grams / 100 for key in NUTRIENT_ZERO}


def _add_into(target: dict[str, float], source: dict[str, float]) -> None:
    for key in NUTRIENT_ZERO:
        target[key] = target.get(key, 0.0) + source.get(key, 0.0)


def _rounded_nutrients(values: dict[str, float]) -> dict[str, float]:
    return {key: _round(value) for key, value in values.items()}


def _round(value: float) -> float:
    return round(float(value), 1)


def _confidence_level(coverage_ratio: float) -> str:
    if coverage_ratio >= 0.75:
        return "high"
    if coverage_ratio >= 0.45:
        return "medium"
    return "low"


def _balance_level(per_person: dict[str, float], constraints: Constraints) -> str:
    kcal = per_person["kcal"]
    protein = per_person["protein_g"]
    fat = per_person["fat_g"]
    sodium = per_person["sodium_mg"]
    sugar = per_person["sugar_g"]
    if constraints.meal == "早餐":
        kcal_ok = 250 <= kcal <= 550
    else:
        kcal_ok = 350 <= kcal <= 900
    protein_ok = protein >= 12
    fat_ok = fat <= 38
    sodium_limit = 800 if "降压" in constraints.health_goals else 1200
    sodium_ok = sodium <= sodium_limit
    sugar_ok = sugar <= 35 or "控糖" not in constraints.health_goals
    passed = sum([kcal_ok, protein_ok, fat_ok, sodium_ok, sugar_ok])
    if passed >= 5:
        return "high"
    if passed >= 3:
        return "medium"
    return "low"


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)
