from __future__ import annotations

import re

from .food_terms import contains_food_term, expand_terms
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

ALLERGEN_PATTERNS = [
    "海鲜",
    "贝类",
    "海蛎子",
    "花生",
    "牛奶",
    "鸡蛋",
    "虾",
    "蟹",
    "坚果",
    "芒果",
    "羊肉",
]

SPECIAL_GROUP_KEYWORDS = {
    "高血压": ["高血压", "血压高", "降血压", "降压"],
    "高血糖": ["高血糖", "血糖高", "控糖", "糖尿病"],
    "高尿酸": ["高尿酸", "尿酸高", "痛风", "降尿酸"],
    "孕妇": ["孕妇", "怀孕", "孕期"],
    "备孕": ["备孕"],
    "哺乳期": ["哺乳", "哺乳期"],
    "老人": ["老人", "老年", "爸妈", "父母"],
    "儿童": ["儿童", "小孩", "孩子"],
}


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
    inferred_profile = infer_profile_from_text(text)
    constraints.inferred_profile = inferred_profile

    if user:
        constraints.taste = user.taste_preference or None
        constraints.health_goals.extend(user.health_goals)
        constraints.allergens.extend(user.allergens)

    if inferred_profile.get("taste_preference"):
        constraints.taste = inferred_profile["taste_preference"]
    constraints.health_goals.extend(inferred_profile.get("health_goals", []))
    constraints.allergens.extend(inferred_profile.get("allergens", []))

    for meal, keywords in MEAL_KEYWORDS.items():
        if _contains_any(text, keywords):
            constraints.meal = meal
            break

    people_match = re.search(r"([一二两三四五六七八九十\d]+)\s*个?人", text)
    if people_match:
        constraints.people_count = _parse_chinese_number(people_match.group(1))
    elif "一家四口" in text:
        constraints.people_count = 4

    dish_count_match = re.search(r"(?:推荐|安排|来|做|给我)?\s*([一二两三四五六七八九十\d]+)\s*道(?:菜|餐|饭)?", text)
    if dish_count_match:
        constraints.requested_dish_count = _parse_chinese_number(dish_count_match.group(1))

    constraints.avoid_tastes.extend(_extract_avoid_tastes(text))

    for taste, keywords in TASTE_KEYWORDS.items():
        if taste == "偏辣" and "辣" in constraints.avoid_tastes:
            continue
        if taste in ("甜", "酸甜") and "甜" in constraints.avoid_tastes:
            continue
        if _contains_any(text, keywords):
            constraints.taste = taste

    if "辣" in constraints.avoid_tastes and constraints.taste == "偏辣":
        constraints.taste = "清淡"
    if "甜" in constraints.avoid_tastes and constraints.taste in ("甜", "酸甜"):
        constraints.taste = None

    for goal, keywords in HEALTH_KEYWORDS.items():
        if _contains_any(text, keywords):
            constraints.health_goals.append(goal)

    for ingredient in INGREDIENT_KEYWORDS:
        if ingredient in text:
            constraints.preferred_ingredients.append(ingredient)

    for match in re.finditer(r"(?:不|别)(?:喜欢吃|爱吃|想吃|要吃|吃|要)([\u4e00-\u9fa5]{1,8})", text):
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
    constraints.allergens = _dedupe(expand_terms(constraints.allergens))
    constraints.avoid_tastes = _dedupe(constraints.avoid_tastes)
    constraints.preferred_ingredients = _dedupe(constraints.preferred_ingredients)
    constraints.avoid_ingredients = _dedupe(expand_terms(constraints.avoid_ingredients))
    constraints.preferred_ingredients = _remove_blocked_preferred_ingredients(
        constraints.preferred_ingredients,
        constraints.allergens + constraints.avoid_ingredients,
    )
    return constraints


def infer_profile_from_text(text: str) -> dict:
    profile: dict = {
        "source": "dialog",
        "gender": None,
        "age": None,
        "labor_intensity": None,
        "special_groups": [],
        "pregnancy_week": None,
        "taste_preference": None,
        "allergens": [],
        "health_goals": [],
    }

    if any(word in text for word in ["我是男", "男生", "男性", "我老公", "我爸"]):
        profile["gender"] = "男"
    if any(word in text for word in ["我是女", "女生", "女性", "我老婆", "我妈", "女朋友"]):
        profile["gender"] = "女"

    age_match = re.search(r"(\d{1,3})\s*岁", text)
    if age_match:
        profile["age"] = int(age_match.group(1))

    week_match = re.search(r"孕(?:周|期)?\s*(\d{1,2})\s*周|怀孕\s*(\d{1,2})\s*周", text)
    if week_match:
        week = week_match.group(1) or week_match.group(2)
        profile["pregnancy_week"] = f"{week}周"
        profile["special_groups"].append("孕妇")

    if any(word in text for word in ["体力活", "运动量大", "劳动强度高", "经常运动"]):
        profile["labor_intensity"] = "高"
    elif any(word in text for word in ["久坐", "办公室", "不怎么运动", "劳动强度低"]):
        profile["labor_intensity"] = "低"

    for group, keywords in SPECIAL_GROUP_KEYWORDS.items():
        if _contains_any(text, keywords):
            profile["special_groups"].append(group)

    avoid_tastes = _extract_avoid_tastes(text)
    for taste, keywords in TASTE_KEYWORDS.items():
        if taste == "偏辣" and "辣" in avoid_tastes:
            continue
        if taste in ("甜", "酸甜") and "甜" in avoid_tastes:
            continue
        if _contains_any(text, keywords):
            profile["taste_preference"] = taste

    for goal, keywords in HEALTH_KEYWORDS.items():
        if _contains_any(text, keywords):
            profile["health_goals"].append(goal)

    for allergen in ALLERGEN_PATTERNS:
        if (
            f"对{allergen}过敏" in text
            or f"{allergen}过敏" in text
            or f"不能吃{allergen}" in text
            or f"不吃{allergen}" in text
        ):
            profile["allergens"].append(allergen)

    profile["special_groups"] = _dedupe(profile["special_groups"])
    profile["allergens"] = _dedupe(expand_terms(profile["allergens"]))
    profile["health_goals"] = _dedupe(profile["health_goals"])
    return profile


def _extract_avoid_tastes(text: str) -> list[str]:
    avoid: list[str] = []
    if any(pattern in text for pattern in ["别辣", "不辣", "不吃辣", "不能吃辣", "不要辣", "一点辣都不想", "少辣"]):
        avoid.append("辣")
    if any(pattern in text for pattern in ["别太甜", "不甜", "不吃甜", "不能吃甜", "不要甜", "少甜", "不要太甜"]):
        avoid.append("甜")
    if any(pattern in text for pattern in ["别太油", "不油", "少油", "不要油", "不能太油"]):
        avoid.append("油")
    return _dedupe(avoid)


def _remove_blocked_preferred_ingredients(preferred: list[str], blocked: list[str]) -> list[str]:
    result: list[str] = []
    for ingredient in preferred:
        if any(contains_food_term(ingredient, value) or contains_food_term(value, ingredient) for value in blocked):
            continue
        result.append(ingredient)
    return result


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
