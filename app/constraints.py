from __future__ import annotations

import re

from .models import Constraints, UserProfile


TASTE_KEYWORDS = {
    "清淡": ["清淡", "清爽", "不油", "少油"],
    "偏辣": ["辣", "重口", "下饭"],
    "酸甜": ["酸甜", "酸一点", "甜一点"],
    "甜": ["甜", "甜口"],
    "咸香": ["咸香", "咸口"],
}

HEALTH_KEYWORDS = {
    "减脂": ["减脂", "减肥", "低脂", "控制体重"],
    "增肌": ["增肌", "蛋白", "高蛋白"],
    "补钙": ["补钙"],
    "补铁": ["补铁", "补气血", "气血"],
    "控糖": ["控糖", "血糖", "少糖"],
    "降压": ["降压", "血压", "高血压"],
    "降尿酸": ["尿酸", "痛风"],
    "健胃消食": ["胃口不好", "养胃", "健胃", "消食"],
}

MEAL_KEYWORDS = {
    "早餐": ["早餐", "早饭", "早上"],
    "午餐": ["午餐", "午饭", "中午", "带去公司"],
    "晚餐": ["晚餐", "晚饭", "晚上", "今晚"],
    "夜宵": ["夜宵", "晚上有点饿"],
}

INGREDIENT_KEYWORDS = [
    "番茄",
    "鸡蛋",
    "土豆",
    "面",
    "鱼",
    "鸡翅",
    "牛肉",
    "虾",
    "豆腐",
    "鸡肉",
]


def _contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def extract_constraints(messages: list[str], user: UserProfile | None = None) -> Constraints:
    text = "\n".join(messages)
    constraints = Constraints(raw_messages=messages)

    if user:
        constraints.taste = user.taste_preference or None
        constraints.health_goals.extend(user.health_goals)
        constraints.allergens.extend(user.allergens)

    for meal, keywords in MEAL_KEYWORDS.items():
        if _contains_any(text, keywords):
            constraints.meal = meal
            break

    people_match = re.search(r"([一二两三四五六七八九十\d]+)\s*个?人", text)
    if people_match:
        constraints.people_count = _parse_chinese_number(people_match.group(1))
    elif "一家四口" in text:
        constraints.people_count = 4

    for taste, keywords in TASTE_KEYWORDS.items():
        if _contains_any(text, keywords):
            constraints.taste = taste

    if "别辣" in text or "不辣" in text or "一点辣都不想" in text:
        constraints.avoid_tastes.append("辣")
        if constraints.taste == "偏辣":
            constraints.taste = "清淡"

    for goal, keywords in HEALTH_KEYWORDS.items():
        if _contains_any(text, keywords):
            constraints.health_goals.append(goal)

    for ingredient in INGREDIENT_KEYWORDS:
        if ingredient in text:
            constraints.preferred_ingredients.append(ingredient)

    for match in re.finditer(r"不(?:要|吃|想吃)([\u4e00-\u9fa5]{1,6})", text):
        constraints.avoid_ingredients.append(match.group(1))

    minutes_match = re.search(r"(\d+)\s*分钟", text)
    if minutes_match:
        constraints.max_minutes = int(minutes_match.group(1))
    elif "十分钟" in text:
        constraints.max_minutes = 10
    elif "半小时" in text:
        constraints.max_minutes = 30

    if any(word in text for word in ["简单", "快手", "别太复杂", "太麻烦"]):
        constraints.difficulty = "简单"
    if any(word in text for word in ["仪式感", "正式", "请几个人"]):
        constraints.scene = "聚餐"
    if "公司" in text:
        constraints.scene = "便当"
    if "夏天" in text or "清爽" in text:
        constraints.scene = "夏季清爽"

    constraints.health_goals = _dedupe(constraints.health_goals)
    constraints.allergens = _dedupe(constraints.allergens)
    constraints.avoid_tastes = _dedupe(constraints.avoid_tastes)
    constraints.preferred_ingredients = _dedupe(constraints.preferred_ingredients)
    constraints.avoid_ingredients = _dedupe(constraints.avoid_ingredients)
    return constraints


def _parse_chinese_number(value: str) -> int | None:
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if value.isdigit():
        return int(value)
    if value in mapping:
        return mapping[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + mapping.get(value[1], 0)
    if value.endswith("十") and len(value) == 2:
        return mapping.get(value[0], 0) * 10
    return None
